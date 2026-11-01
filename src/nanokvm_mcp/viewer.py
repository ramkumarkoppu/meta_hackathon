"""``nanokvm viewer``: the locked computer's screen, live, in a window on this laptop (Tk), with a prompt box
beside it for questions about the screen (assist.py).

The live view itself is read-only:
  * it plays the service's MJPEG live stream (/stream.mjpg) and polls /api/status, so the service
    stays the only process that opens the NanoKVM;
  * it never makes the service open the keyboard/mouse link and sends nothing to the locked
    computer: keys and clicks in the window stay on this laptop;
  * frames live only in memory; nothing is written to disk.
Like the browser live view it uses the service token without a client id, so Claude's screen-sharing
gate doesn't apply: it is for the person at this laptop. Nothing in it is sent to Claude, but it is an
ordinary window, so anything that captures this laptop's screen captures the locked computer's too.

The prompt box does nothing until it is used. A question shares the screen with Muse Glimmer (asked in the
window first) and sends Spark only redacted text, as ``nanokvm ask`` does. Fix it does a task on the locked computer
one step at a time, each step approved in the window, and hands a change to code over to ``nanokvm fix``'s loop,
which types it by line number, each edit and the save approved in the window too.

Frames are decoded and scaled on a background thread and the window shows only the newest one, so
it skips frames rather than falling behind the capture.
"""
from __future__ import annotations

import collections
import http.client
import statistics
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from typing import BinaryIO, Iterator

import cv2
import numpy as np

from . import geometry as G
from .assist import READ_KINDS, Approval, AssistSession, EventQueue
from .client import ServiceClient, ServiceError, ServiceUnreachable
from .config import Config

DEFAULT_FPS = 30               # the capture runs at about 30 fps
MAX_PART = 16_000_000          # bytes; a 1920x1080 JPEG is far smaller

BG, BAR_BG, FG = "#111111", "#1c1c1c", "#cccccc"
OK_FG, WARN_FG, BAD_FG = "#8fd18f", "#e3b341", "#ff7b72"
PANEL_BG, FIELD_BG, DIM_FG = "#181818", "#242424", "#8b949e"
TARGET_FG = "#ff9500"          # the outline of a Fix it step's target on the picture


# ---------------------------------------------------------------- the stream
def read_parts(fp: BinaryIO, boundary: bytes = b"frame") -> Iterator[tuple[dict[str, str], bytes]]:
    """(headers, body) for each part of a multipart/x-mixed-replace stream whose parts carry a
    Content-Length, as /stream.mjpg's do. Header names are lower-cased. Stops when the stream ends."""
    marker = b"--" + boundary
    while True:
        line = fp.readline(1024)
        if not line:
            return
        if line.strip() != marker:
            continue                    # the CRLF that ends the previous part
        headers: dict[str, str] = {}
        while True:
            h = fp.readline(1024)
            if not h:
                return
            h = h.strip()
            if not h:
                break
            name, _, value = h.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
        try:
            n = int(headers.get("content-length", ""))
        except ValueError:
            raise ValueError("a live-stream part has no Content-Length") from None
        if not 0 < n <= MAX_PART:
            raise ValueError(f"a live-stream part has an impossible Content-Length ({n})")
        body = fp.read(n)
        if len(body) < n:
            return                      # the stream ended inside a part
        yield headers, body


def _boundary(content_type: str | None) -> bytes:
    for param in (content_type or "").split(";")[1:]:
        name, _, value = param.strip().partition("=")
        if name.lower() == "boundary" and value:
            return value.strip('"').encode("latin-1")
    return b"frame"


def _number(text: str | None, kind: type) -> int | float | None:
    try:
        return kind(text) if text else None
    except ValueError:
        return None


def fit_size(w: int, h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """The largest size with w:h's aspect ratio that fits in box_w x box_h (w x h if the box is unknown)."""
    if box_w < 2 or box_h < 2:
        return w, h
    s = min(box_w / w, box_h / h)
    return max(1, min(box_w, round(w * s))), max(1, min(box_h, round(h * s)))


@dataclass
class Picture:
    ppm: bytes                       # binary PPM, the format Tk's PhotoImage reads fastest
    width: int                       # size as shown
    height: int
    frame_size: tuple[int, int]      # size as streamed
    seq: int | None                  # capture sequence number (None from an older service)
    captured: float | None           # wall-clock capture time (time.time()) sent by the service
    placeholder: bool = False        # the service's "no video" picture


class LiveFeed:
    """Reads the live stream on a background thread, decodes each frame and scales it for the window.
    Only the newest picture is kept (``take``), so a slow window skips frames instead of lagging.
    Reconnects by itself if the stream drops; it never starts the service."""

    def __init__(self, client: ServiceClient, fps: int = DEFAULT_FPS) -> None:
        self.client = client
        self.fps = fps
        self.view: tuple[str, int, int] = ("fit", 0, 0)   # set by the window: ("fit" | "actual", w, h)
        self.state = "connecting"                          # connecting | streaming | reconnecting
        self.error: str | None = None
        self.unreachable = False                           # the last failure: nothing listens on the port
        self.parts = 0                                     # pictures received, placeholders included
        self.reconnects = 0                                # streams opened again after one was lost
        self._had_stream = False
        self._latest: Picture | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()                     # cuts a back-off wait short
        self._thread = threading.Thread(target=self._run, name="viewer-stream", daemon=True)

    def start(self) -> "LiveFeed":
        self._thread.start()
        return self

    def stop(self, wait: float = 0.0) -> None:
        self._stop.set()
        self._wake.set()
        if wait and self._thread.is_alive():
            self._thread.join(wait)

    def retry_now(self) -> None:
        """Try to connect again at once instead of finishing the back-off wait (after R started the service)."""
        self._wake.set()

    def take(self) -> Picture | None:
        """The newest picture not taken yet, or None."""
        with self._lock:
            pic, self._latest = self._latest, None
        return pic

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                resp = self.client.open_stream(self.fps)
            except ServiceError as e:
                self.state, self.error, self.unreachable = "reconnecting", str(e), isinstance(e, ServiceUnreachable)
            else:
                backoff = 0.5
                self.state, self.error, self.unreachable = "streaming", None, False
                if self._had_stream:
                    self.reconnects += 1
                self._had_stream = True
                try:
                    for headers, body in read_parts(resp, _boundary(resp.getheader("Content-Type"))):
                        if self._stop.is_set():
                            return
                        self._accept(headers, body)
                    self.state, self.error = "reconnecting", "the service ended the live stream"
                except (OSError, ValueError, http.client.HTTPException) as e:
                    self.state, self.error = "reconnecting", f"the live stream was interrupted ({e or type(e).__name__})"
                finally:
                    resp.close()
            self._wake.wait(backoff)
            self._wake.clear()
            backoff = min(backoff * 2, 5.0)

    def _accept(self, headers: dict[str, str], body: bytes) -> None:
        img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return                                         # a damaged picture: wait for the next one
        fh, fw = img.shape[:2]
        mode, box_w, box_h = self.view
        if mode == "fit":
            img = G.resize(img, *fit_size(fw, fh, box_w, box_h))
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        pic = Picture(b"P6\n%d %d\n255\n" % (w, h) + rgb.tobytes(), w, h, (fw, fh),
                      _number(headers.get("x-frame-seq"), int), _number(headers.get("x-frame-time"), float),
                      headers.get("x-frame-placeholder") == "1")
        with self._lock:
            self._latest = pic
            self.parts += 1


class StatusPoller:
    """Fetches /api/status about once a second on a background thread, so the window never waits on
    HTTP. Also looks for the NanoKVM's COM port (listed only, never opened). /api/status itself never
    opens the keyboard/mouse link."""

    def __init__(self, client: ServiceClient, period: float = 1.0) -> None:
        self.client = client
        self.period = period
        self.latest: tuple[dict | None, str | None] = (None, None)    # (status, error)
        self.serial: tuple[str | None, bool] = (None, False)          # find_serial_port's answer
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="viewer-status", daemon=True)

    def start(self) -> "StatusPoller":
        self._thread.start()
        return self

    def stop(self, wait: float = 0.0) -> None:
        self._stop.set()
        if wait and self._thread.is_alive():
            self._thread.join(wait)

    def _run(self) -> None:
        polls = 0
        while not self._stop.is_set():
            if polls % 10 == 0:
                self.serial = find_serial_port(self.client.cfg)
            polls += 1
            try:
                self.latest = (self.client.request("GET", "/api/status", timeout=3.0), None)
            except ServiceError as e:
                self.latest = (None, str(e))
            self._stop.wait(self.period)


def find_serial_port(cfg: Config) -> tuple[str | None, bool]:
    """(port, present): the NanoKVM keyboard/mouse COM port(s) by USB ID as in nanokvm diag, or the
    serial_port set in nanokvm.toml and whether it exists. Ports are listed, never opened."""
    try:
        from .hid import list_serial_ports
        ports = list_serial_ports()
        if cfg.serial_port:
            return cfg.serial_port, cfg.serial_port.upper() in {p["device"].upper() for p in ports}
        hits = [p["device"] for p in ports if (p["vid"], p["pid"]) in cfg.serial_id_list]
    except Exception:  # noqa: BLE001  (pyserial missing, malformed serial_ids, ...)
        return cfg.serial_port, False
    return ", ".join(hits) or None, bool(hits)


# ---------------------------------------------------------------- status lines
class ViewStats:
    """Pictures shown, the window's frame rate and the delay from capture to window."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.shown = 0
        self.placeholders = 0
        self.delays_ms: collections.deque[float] = collections.deque(maxlen=20_000)
        self._recent: collections.deque[float] = collections.deque(maxlen=120)   # monotonic show times
        self._last_new: float | None = None
        self.scale = 1.0                 # shown size / streamed size of the last picture

    def record(self, pic: Picture) -> None:
        now = time.monotonic()
        self.shown += 1
        self._recent.append(now)
        self.scale = pic.width / max(1, pic.frame_size[0])
        if pic.placeholder:
            self.placeholders += 1
            return
        self._last_new = now
        if pic.captured is not None:
            self.delays_ms.append(max(0.0, (time.time() - pic.captured) * 1000.0))

    def fps(self) -> float:
        """Pictures shown per second over the last 2 s."""
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 2.0:
            self._recent.popleft()
        if len(self._recent) < 2:
            return 0.0
        return (len(self._recent) - 1) / max(1e-6, self._recent[-1] - self._recent[0])

    def recent_delay(self) -> float | None:
        """Median delay of the last 30 frames, in ms (None if the service sends no capture times)."""
        if not self.delays_ms:
            return None
        return statistics.median(list(self.delays_ms)[-30:])

    def seconds_since_new(self) -> float | None:
        return None if self._last_new is None else time.monotonic() - self._last_new

    def summary(self) -> str:
        secs = max(1e-6, time.monotonic() - self.started)
        real = self.shown - self.placeholders
        text = f"Live view closed after {secs:.0f} s: {real} frames shown ({real / secs:.1f} fps)"
        if self.delays_ms:
            d = sorted(self.delays_ms)
            text += (f"; delay from capture to window: median {statistics.median(d):.0f} ms, "
                     f"95th percentile {d[int(0.95 * (len(d) - 1))]:.0f} ms")
        if self.placeholders:
            text += f"; {self.placeholders} 'no video' pictures"
        return text + "."


def video_line(status: dict | None, status_error: str | None, feed, stats: ViewStats,
               starting: bool = False, start_error: str | None = None) -> tuple[str, str]:
    """The window's first status line (video) and its colour. ``feed`` is the LiveFeed; ``starting``
    and ``start_error`` describe a service start requested with R."""
    if feed.state != "streaming":
        if starting:
            return "Starting the NanoKVM service…", FG
        if start_error:
            first = start_error.splitlines()[0].split(". ")[0].rstrip(".:")
            return f"Couldn't start the NanoKVM service: {first} – press R to try again", BAD_FG
        why = feed.error or status_error or "Connecting to the NanoKVM service…"
        if feed.unreachable:
            why += " – press R to start it"
        return why, (FG if feed.state == "connecting" else BAD_FG)
    if status is None:
        if status_error is None:                   # the first status answer hasn't arrived yet
            return f"Video: window {stats.fps():.0f} fps · waiting for the service status…", FG
        return f"Video: window {stats.fps():.0f} fps (service status not available: {status_error})", WARN_FG
    cap = status.get("capture") or {}
    sim = "SIMULATOR · " if status.get("simulated") else ""
    if cap.get("state") != "streaming":
        return f"{sim}No video ({cap.get('state')}): {cap.get('error') or 'waiting for frames'}", BAD_FG
    mode = " ".join(str(cap.get("mode") or "").split()[:2]) or "x".join(map(str, cap.get("frame_size") or []))
    parts = [f"{sim}Video: {mode}", f"capture {float(cap.get('fps') or 0):.0f} fps", f"window {stats.fps():.0f} fps"]
    delay = stats.recent_delay()
    if delay is not None:
        parts.append(f"delay {delay:.0f} ms")
    if stats.scale < 0.98:                         # fitted into a smaller window: small text gets softer
        parts.append(f"shown at {stats.scale:.0%} (press 1 for actual pixels)")
    colour = OK_FG
    if cap.get("blank"):
        parts.append("the picture is uniform: no HDMI signal, display asleep, or a blank screen?")
        colour = WARN_FG
    idle = stats.seconds_since_new()
    if idle is not None and idle > 2:
        parts.append(f"no new frame for {idle:.0f} s")
        colour = WARN_FG
    if "shares_active" not in status:              # a service process started from older code
        parts.append("an older service is running (no frame times or --fps): run nanokvm stop, then reopen this")
        colour = WARN_FG
    return " · ".join(parts), colour


def keyboard_text(control: dict, serial: tuple[str | None, bool]) -> str:
    """``serial`` is find_serial_port's (port, present)."""
    if not control.get("enabled"):
        return "Keyboard/mouse: off (control = false)"
    if control.get("link"):                        # opened by a keyboard/mouse action
        text = f"Keyboard/mouse: {control['link']}"
        info = control.get("device_info")
        if isinstance(info, dict):
            text += (f", chip {info.get('chip_version')}, locked computer USB "
                     + ("connected" if info.get("target_usb_connected") else "NOT connected"))
        if control.get("error"):
            text += f" ({control['error']})"
        return text
    port, present = serial
    if port and present:
        return f"Keyboard/mouse: {port} found (opens on the first keyboard/mouse action)"
    if port:
        return f"Keyboard/mouse: {port} (serial_port in nanokvm.toml) is not present"
    return "Keyboard/mouse: NanoKVM COM port not found (check the HOST-end USB-C cable)"


def other_line(status: dict | None, serial: tuple[str | None, bool]) -> tuple[str, str]:
    """The second status line (keyboard/mouse, screen sharing, keys) and its colour."""
    claude = 0
    if status is None:
        kb, share = "Keyboard/mouse: ?", "Claude screen sharing: ?"
    else:
        control = status.get("control") or {}
        if status.get("simulated") and not control.get("link"):
            kb = "Keyboard/mouse: simulated"
        else:
            kb = keyboard_text(control, serial)
        n = status.get("shares_active")
        claude = n or 0
        share = ("Claude screen sharing: ?" if n is None else "Claude screen sharing: off" if n == 0
                 else f"Claude screen sharing: ON ({n} conversation{'' if n == 1 else 's'})")
        if status.get("local_shares_active"):
            share += " · Muse Glimmer on this laptop is reading it (nanokvm read/ask/edit)"
    keys = ("the picture is view-only; only Fix it clicks or types, after your yes (with Auto, one yes for the task) "
            "· F11 full screen, F2 prompt box, "
            "1 actual pixels, Q quit (click the picture first)")
    return f"{kb} · {share} · {keys}", (WARN_FG if claude else FG)


# ---------------------------------------------------------------- the window
def _dpi_aware() -> None:
    """On a scaled (high-DPI) display, draw one picture pixel per screen pixel instead of letting
    Windows stretch the whole window, which blurs small text. Must run before the window exists."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)        # system DPI aware
    except (AttributeError, OSError):
        pass


class AskPanel:
    """The prompt box beside the live picture. The work is done by an AssistSession on its own thread; this panel
    shows its events and approvals. Nothing is read or sent until the first question (or Fix it)."""

    WIDTH = 440
    ARM_S = 0.6                     # an approval's buttons work only after this long, so a click or key meant for
                                    # something else can't answer it (as the terminal drops type-ahead)

    def __init__(self, win: "ViewerWindow") -> None:
        tk = win.tk
        self.tk, self.cfg = tk, win.client.cfg
        self.reveal = None              # set by the window: shows the panel (and leaves full screen) for an approval
        self.show_target = None         # set by the window: outlines a Fix it step's target on the live picture
        self.events = EventQueue()
        self.session: AssistSession | None = None
        self.approval: Approval | None = None
        font, small, mono = ("Segoe UI", 10), ("Segoe UI", 9), ("Consolas", 9)

        f = self.frame = tk.Frame(win.root, bg=PANEL_BG, width=self.WIDTH)
        f.pack_propagate(False)
        tk.Label(f, text="Ask about the locked computer's screen", bg=PANEL_BG, fg=FG, anchor="w",
                 font=("Segoe UI", 11, "bold"), padx=8, pady=6).pack(side="top", fill="x")

        # bottom up: status, buttons, prompt, options, approval; the log takes what is left
        self.status = tk.Label(f, bg=PANEL_BG, fg=DIM_FG, anchor="w", justify="left", font=small, padx=8,
                               wraplength=self.WIDTH - 16)
        self.status.pack(side="bottom", fill="x", pady=(0, 6))
        row = tk.Frame(f, bg=PANEL_BG)
        row.pack(side="bottom", fill="x", padx=8, pady=4)
        self.ask_btn = tk.Button(row, text="Ask", width=7, command=self.submit_ask)
        self.do_btn = tk.Button(row, text="Fix it", width=7, command=self.submit_do)
        self.stop_btn = tk.Button(row, text="Stop", width=6, command=self.stop, state="disabled")
        for b in (self.ask_btn, self.do_btn, self.stop_btn):
            b.pack(side="left", padx=(0, 5))
        self.auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Auto", variable=self.auto_var, bg=PANEL_BG, fg=FG, selectcolor=FIELD_BG,
                       activebackground=PANEL_BG, activeforeground=FG, font=small).pack(side="left")
        self.prompt = tk.Text(f, height=3, wrap="word", bg=FIELD_BG, fg=FG, insertbackground=FG, font=font,
                              relief="flat", padx=6, pady=4)
        self.prompt.pack(side="bottom", fill="x", padx=8, pady=(4, 0))
        self.prompt.bind("<Return>", lambda _e: (self.submit_ask(), "break")[1])
        self.prompt.bind("<Shift-Return>", lambda _e: (self.prompt.insert("insert", "\n"), "break")[1])

        opts = self.opts = tk.Frame(f, bg=PANEL_BG)
        opts.pack(side="bottom", fill="x", padx=8, pady=(4, 0))
        self.kind_var = tk.StringVar(value="window")
        tk.Label(opts, text="Glimmer reads:", bg=PANEL_BG, fg=DIM_FG, font=small).grid(row=0, column=0, sticky="w")
        for col, kind in enumerate(READ_KINDS, 1):
            tk.Radiobutton(opts, text={"window": "the window in front", "code": "the code"}[kind], value=kind,
                           variable=self.kind_var, bg=PANEL_BG, fg=FG, selectcolor=FIELD_BG,
                           activebackground=PANEL_BG, activeforeground=FG, font=small).grid(row=0, column=col, sticky="w")
        self.local_var = tk.BooleanVar(value=self.cfg.outbound == "local_only")
        self.show_var = tk.BooleanVar(value=self.cfg.outbound == "approve")
        self.local_box = tk.Checkbutton(opts, text="Local only: Glimmer answers too, nothing leaves this laptop",
                                        variable=self.local_var, command=self.refresh, bg=PANEL_BG, fg=FG,
                                        selectcolor=FIELD_BG, activebackground=PANEL_BG, activeforeground=FG, font=small)
        self.show_box = tk.Checkbutton(opts, text="Show me the exact text before it is sent", variable=self.show_var,
                                       bg=PANEL_BG, fg=FG, selectcolor=FIELD_BG, activebackground=PANEL_BG,
                                       activeforeground=FG, font=small)
        self.spark_var = tk.BooleanVar(value=False)   # Fix it plans (and fixes code) on this laptop unless this is ticked
        self.spark_box = tk.Checkbutton(opts, text="Fix it: Spark plans and fixes code (sends the redacted reading)",
                                        variable=self.spark_var, bg=PANEL_BG, fg=FG, selectcolor=FIELD_BG,
                                        activebackground=PANEL_BG, activeforeground=FG, font=small)
        self.local_box.grid(row=1, column=0, columnspan=3, sticky="w")
        self.show_box.grid(row=2, column=0, columnspan=3, sticky="w")
        self.spark_box.grid(row=3, column=0, columnspan=3, sticky="w")
        if self.cfg.outbound == "local_only":         # the window can make nanokvm.toml stricter, never looser
            self.local_box.configure(state="disabled")
        if self.cfg.outbound == "approve":
            self.show_box.configure(state="disabled")

        # The approval card: the title, then the buttons packed before the text, so a short panel squeezes the text
        # (which scrolls) and never the buttons.
        self.card = tk.Frame(f, bg=FIELD_BG, highlightthickness=1, highlightbackground=WARN_FG)
        self.card_title = tk.Label(self.card, bg=FIELD_BG, fg=WARN_FG, anchor="w", justify="left",
                                   font=("Segoe UI", 10, "bold"), padx=6, pady=4, wraplength=self.WIDTH - 30)
        self.card_title.pack(side="top", fill="x")
        buttons = tk.Frame(self.card, bg=FIELD_BG)
        buttons.pack(side="bottom", fill="x", padx=6, pady=6)
        self.yes_btn = tk.Button(buttons, command=lambda: self.decide(True), state="disabled")
        self.no_btn = tk.Button(buttons, command=lambda: self.decide(False), state="disabled")
        self.yes_btn.pack(side="left", padx=(0, 6))
        self.no_btn.pack(side="left")
        self.card_text = tk.Text(self.card, height=16, wrap="word", bg=FIELD_BG, fg=FG, relief="flat", font=font,
                                 padx=6, pady=2)
        card_scroll = tk.Scrollbar(self.card, command=self.card_text.yview)
        self.card_text.configure(yscrollcommand=card_scroll.set)
        card_scroll.pack(side="right", fill="y")
        self.card_text.pack(side="left", fill="both", expand=True)
        self.card_fonts = (font, mono)

        logf = tk.Frame(f, bg=PANEL_BG)
        logf.pack(side="top", fill="both", expand=True, padx=8)
        self.log = tk.Text(logf, wrap="word", bg=PANEL_BG, fg=FG, relief="flat", font=font, state="disabled",
                           padx=2, pady=2, cursor="arrow")
        scroll = tk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("q", foreground="#e6edf3", font=("Segoe UI", 10, "bold"), spacing1=8)
        self.log.tag_configure("say", foreground=DIM_FG, font=small)
        self.log.tag_configure("meta", foreground=DIM_FG, font=small, spacing3=4)
        self.log.tag_configure("mono", font=mono)
        self.log.tag_configure("err", foreground=BAD_FG)
        self.log.tag_configure("ok", foreground=OK_FG)
        self._write("Type a question about what the locked computer shows and press Enter (Shift+Enter: new line), or "
                    "describe a task and press Fix it: it is done there one step at a time, each step shown and "
                    "outlined on the picture first; a change to code in VS Code is made by line number, each edit "
                    "shown first and checked on the screen. Auto skips asking for each step (it still asks before a "
                    "step that changes something, and once before a fix's edits). Nothing is read or sent until you "
                    "ask. F2 hides this panel.", "say")
        self.refresh()

    # ---- the log
    def _write(self, text: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip("\n") + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    # ---- actions
    def _session(self) -> AssistSession:
        if self.session is None:
            self.session = AssistSession(self.cfg, self.events.put)
        return self.session

    def _text(self) -> str:
        return self.prompt.get("1.0", "end").strip()

    @property
    def shown(self) -> bool:
        return bool(self.frame.winfo_manager())

    def submit_ask(self) -> None:
        question = self._text()
        if not question or self.busy or not self.shown:      # never from a hidden prompt box
            return
        if self._session().ask(question, self.kind_var.get(), self.local_var.get(), self.show_var.get()):
            self.prompt.delete("1.0", "end")
        self.refresh()

    def submit_do(self) -> None:
        """Fix it: the task typed in the prompt box, only (a question asked earlier isn't turned into actions)."""
        if self.busy or self.do_blocked() or not self.shown:
            return
        task = self._text()
        if not task:
            self._write("Type the task in the box first, then press Fix it.", "say")
            return
        spark = self.spark_var.get() and not self.local_var.get()     # Local only keeps the planning here too
        if self._session().do(task, self.auto_var.get(), spark, self.show_var.get()):
            self.prompt.delete("1.0", "end")
        self.refresh()

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()
            self._write("Stopping…", "say")
            self._drop_card()

    def decide(self, yes: bool) -> None:
        a = self.approval
        if a is None or self.session is None:
            return
        self.approval = None
        self.card.pack_forget()
        self._write(f"You: {a.yes if yes else a.no}", "q")
        self.session.answer(a, yes)
        self.refresh()

    def close(self) -> None:
        if self.session is not None:
            self.session.close()

    # ---- state
    @property
    def busy(self) -> bool:
        return self.session is not None and self.session.busy

    def do_blocked(self) -> str | None:
        return None if self.cfg.control else "keyboard/mouse control is off (control = false in nanokvm.toml)"

    def refresh(self) -> None:
        """Buttons and the status line, from the session's state."""
        busy, waiting = self.busy, self.approval is not None
        no_do = self.do_blocked()
        self.ask_btn.configure(state="disabled" if busy else "normal")
        self.do_btn.configure(state="disabled" if busy or no_do else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")
        local = self.local_var.get() or self.cfg.outbound == "local_only"
        # While work runs its choices are fixed: ticking Local only part-way would look as if it stopped sending
        self.spark_box.configure(state="disabled" if local or busy else "normal")   # Local only: Fix it plans here
        self.local_box.configure(state="disabled" if busy or self.cfg.outbound == "local_only" else "normal")
        self.show_box.configure(state="disabled" if busy or self.cfg.outbound == "approve" else "normal")
        s = self.session
        shared = s.shared_minutes_left() if s else 0
        parts = ["Waiting for your answer above." if waiting else "Working… (Stop ends it)" if busy else "Ready."]
        parts.append(f"Glimmer can read the screen for {shared} more min." if shared else
                     "Glimmer can't read the screen until you ask.")
        if no_do and not busy:
            parts.append(f"Fix it is off: {no_do}.")
        self.status.configure(text=" ".join(parts))

    def poll(self) -> None:
        """The session's events, on the window's thread."""
        for kind, payload in self.events.take():
            if kind == "say":
                self._write(f"… {payload}", "say")
            elif kind == "question":
                self._write(f"You: {payload}", "q")
            elif kind == "answer":
                text, report = payload
                self._write(text)
                self._write(report, "meta")
            elif kind == "edit":
                self._write(str(payload), "mono")
            elif kind == "report":
                for line, tag in payload:                    # coloured by nanokvm, never by Spark's text
                    self._write(line, tag)
            elif kind == "info":
                self._write(str(payload), "say")
            elif kind == "error":
                self._write(str(payload), "err")
            elif kind == "approve":
                self._show_approval(payload)
            elif kind == "target":                           # a Fix it step's target: outline it on the picture
                if self.show_target is not None:
                    self.show_target(payload)
            elif kind == "idle":
                self._drop_card()                            # an approval Stop answered no
                if self.show_target is not None:
                    self.show_target(None)
            if kind in ("idle", "approve"):
                self.refresh()

    def _drop_card(self) -> None:
        self.approval = None
        self.yes_btn.configure(state="disabled")
        self.no_btn.configure(state="disabled")
        self.card.pack_forget()

    def _show_approval(self, a: Approval) -> None:
        self.approval = a
        self.card_title.configure(text=a.title)
        self.card_text.configure(state="normal", font=self.card_fonts[1] if a.mono else self.card_fonts[0])
        self.card_text.delete("1.0", "end")
        self.card_text.insert("1.0", a.text)
        self.card_text.configure(state="disabled")
        self.card.pack(side="bottom", fill="x", padx=8, pady=(6, 0), after=self.opts)   # just above the options
        self.card_text.yview_moveto(0)
        self.card_text.update_idletasks()
        if self.card_text.yview()[1] < 1.0:                  # more than the card shows
            self.card_title.configure(text=f"{a.title} (scroll to read all of it)")
        if self.reveal is not None:                          # an approval must be seen: show the panel
            self.reveal()
        self.arm_later()

    def arm_later(self) -> None:
        """The buttons work only ARM_S after the card can be seen (again, when the panel comes back)."""
        a = self.approval
        if a is None:
            return
        self.yes_btn.configure(text=a.yes, state="disabled")
        self.no_btn.configure(text=a.no, state="disabled")
        self.frame.after(int(self.ARM_S * 1000), lambda: self._arm(a))

    def _arm(self, a: Approval) -> None:
        if self.approval is a and self.shown:
            self.yes_btn.configure(state="normal")
            self.no_btn.configure(state="normal")


class ViewerWindow:
    TICK_MS = 8                     # how often the window looks for a new picture

    def __init__(self, client: ServiceClient, fps: int = DEFAULT_FPS, close_after: float | None = None) -> None:
        import tkinter as tk
        _dpi_aware()
        self.tk = tk
        self.client = client
        self.root = tk.Tk()
        self.root.title("Locked computer – live view, and questions about it")
        self.root.configure(bg=BG)
        self.root.minsize(320, 240)
        self.root.report_callback_exception = self._callback_error
        self.bar = bar = tk.Frame(self.root, bg=BAR_BG)
        bar.pack(side="bottom", fill="x")
        self.line1 = tk.Label(bar, anchor="w", bg=BAR_BG, fg=FG, font=("Segoe UI", 9), padx=8)
        self.line2 = tk.Label(bar, anchor="w", bg=BAR_BG, fg=FG, font=("Segoe UI", 9), padx=8)
        self.line1.pack(side="top", fill="x")
        self.line2.pack(side="top", fill="x")
        self.panel = AskPanel(self)
        self.panel.frame.pack(side="right", fill="y")
        self.panel.reveal = self._reveal_panel
        self._panel_hidden_by_fullscreen = False
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0, takefocus=True)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.note_item = self.canvas.create_text(0, 0, fill=FG, font=("Segoe UI", 12),
                                                 text="Connecting to the NanoKVM service…")
        # A Fix it step's target, outlined on the picture before the step is done (per mille of the screen)
        self.target_item = self.canvas.create_rectangle(0, 0, 0, 0, outline=TARGET_FG, width=3, state="hidden")
        self.target: tuple[int, int, int, int] | None = None
        self.panel.show_target = self.show_target
        self.photo = None           # the picture on screen; Tk shows nothing once it is garbage-collected
        self.mode = "fit"           # "fit" the window, or "actual" pixels (drag to pan)
        self.closed = False
        self.start_error: str | None = None       # why the last R start failed
        self._starter: threading.Thread | None = None
        self.stats = ViewStats()
        self.feed = LiveFeed(client, fps)
        self.poller = StatusPoller(client)
        self._placed: tuple[int, int, int, int] | None = None
        self._next_bar = 0.0

        fw, fh = max(1, client.cfg.capture_width), max(1, client.cfg.capture_height)
        w, h = fit_size(fw, fh, min(fw, int(self.root.winfo_screenwidth() * 0.9) - AskPanel.WIDTH),
                        min(fh, int(self.root.winfo_screenheight() * 0.75)))
        self.root.geometry(f"{w + AskPanel.WIDTH}x{h + 2 * self.line1.winfo_reqheight()}")
        self.feed.view = ("fit", w, h)
        self.canvas.coords(self.note_item, w // 2, h // 2)

        # The letter keys are for the picture: while a text box has the focus they are typing. Click the picture
        # to use them again.
        for seq in ("<Key-f>", "<Key-F>"):
            self.root.bind(seq, self._unless_typing(self.toggle_fullscreen))
        self.root.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.canvas.bind("<Double-Button-1>", lambda _e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda _e: self.set_fullscreen(False))
        self.root.bind("<F2>", lambda _e: self.toggle_panel())
        self.root.bind("<Key-1>", self._unless_typing(self.toggle_actual_size))
        for seq in ("<Key-r>", "<Key-R>"):
            self.root.bind(seq, self._unless_typing(self.start_service))
        for seq in ("<Key-q>", "<Key-Q>"):
            self.root.bind(seq, self._unless_typing(self.close))
        self.root.bind("<Control-w>", lambda _e: self.close())
        self.canvas.bind("<ButtonPress-1>", lambda e: (self.canvas.focus_set(), self.canvas.scan_mark(e.x, e.y)))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<Configure>", self._on_resize)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if close_after:
            self.root.after(int(close_after * 1000), self.close)

    # ---- running
    def run(self) -> None:
        """Show the window until it is closed (blocks)."""
        self.feed.start()
        self.poller.start()
        self.root.after(self.TICK_MS, self._tick)
        self.root.mainloop()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.panel.close()          # stops its work, releases every key if it typed, ends its share
        finally:
            self.panel.reveal = self.panel.show_target = None   # no window -> panel -> window cycle (see below)
            self.feed.stop()
            self.poller.stop()
            try:
                self.root.destroy()
            except self.tk.TclError:
                pass
        # Break the root -> window reference cycle, so reference counting frees the Tk interpreter here, on
        # its own thread. If garbage collection on another thread frees it, Tcl aborts the whole process
        # ("async handler deleted by the wrong thread").
        self.root.__dict__.pop("report_callback_exception", None)
        self.photo = None

    def summary(self) -> str:
        text = self.stats.summary()
        if self.feed.reconnects:
            text += f" The stream reconnected {self.feed.reconnects} time(s)."
        return text

    def _callback_error(self, exc, val, tb) -> None:
        if issubclass(exc, KeyboardInterrupt):      # Ctrl+C that lands inside a callback; it usually
            self.close()                            # comes out of mainloop() instead (run_viewer)
        else:
            traceback.print_exception(exc, val, tb)

    def _tick(self) -> None:
        if self.closed:
            return
        try:
            pic = self.feed.take()
            if pic is not None:
                self._show(pic)
            self.panel.poll()
            if time.monotonic() >= self._next_bar:
                self._next_bar = time.monotonic() + 0.25
                self._update_bar()
                self.panel.refresh()
        finally:
            if not self.closed:
                self.root.after(self.TICK_MS, self._tick)

    def _show(self, pic: Picture) -> None:
        try:
            photo = self.tk.PhotoImage(width=pic.width, height=pic.height, data=pic.ppm, format="PPM")
        except self.tk.TclError:
            return
        self.canvas.itemconfigure(self.image_item, image=photo)
        self.photo = photo
        self._place(pic.width, pic.height)
        if self.stats.shown == 0:
            self.canvas.itemconfigure(self.note_item, state="hidden")
        self.stats.record(pic)

    def _place(self, w: int, h: int) -> None:
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if (w, h, cw, ch) == self._placed:
            return
        self._placed = (w, h, cw, ch)
        self.canvas.coords(self.image_item, max(0, (cw - w) // 2), max(0, (ch - h) // 2))
        self.canvas.configure(scrollregion=(0, 0, max(cw, w), max(ch, h)))
        self._draw_target()

    def show_target(self, box: tuple[int, int, int, int] | None) -> None:
        """Outline a Fix it step's target (a box per mille of the whole picture) on it, or clear it. In actual-pixel
        mode the view scrolls so that the outline can be seen."""
        self.target = box
        self._draw_target()
        if box is not None and self.mode == "actual" and self.photo is not None:
            pw, ph = self.photo.width(), self.photo.height()
            cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
            mx, my = (box[0] + box[2]) / 2000 * pw, (box[1] + box[3]) / 2000 * ph
            if pw > cw:
                self.canvas.xview_moveto(max(0.0, mx - cw / 2) / pw)
            if ph > ch:
                self.canvas.yview_moveto(max(0.0, my - ch / 2) / ph)

    def _draw_target(self) -> None:
        if self.target is None or self.photo is None:
            self.canvas.itemconfigure(self.target_item, state="hidden")
            return
        ox, oy = self.canvas.coords(self.image_item)[:2]
        pw, ph = self.photo.width(), self.photo.height()
        x1, y1, x2, y2 = self.target
        self.canvas.coords(self.target_item, ox + x1 * pw / 1000 - 3, oy + y1 * ph / 1000 - 3,
                           ox + x2 * pw / 1000 + 3, oy + y2 * ph / 1000 + 3)
        self.canvas.itemconfigure(self.target_item, state="normal")
        self.canvas.tag_raise(self.target_item)

    @property
    def starting(self) -> bool:
        return self._starter is not None and self._starter.is_alive()

    def _update_bar(self) -> None:
        status, error = self.poller.latest
        if self.feed.state == "streaming":
            self.start_error = None
        text, colour = video_line(status, error, self.feed, self.stats, self.starting, self.start_error)
        self.line1.configure(text=text, fg=colour)
        text, colour = other_line(status, self.poller.serial)
        self.line2.configure(text=text, fg=colour)

    # ---- keys
    def _on_resize(self, event) -> None:
        self.feed.view = (self.mode, event.width, event.height)
        self.canvas.coords(self.note_item, event.width // 2, event.height // 2)
        if self.photo is not None:
            self._place(self.photo.width(), self.photo.height())

    def toggle_actual_size(self) -> None:
        self.mode = "actual" if self.mode == "fit" else "fit"
        self.feed.view = (self.mode, self.canvas.winfo_width(), self.canvas.winfo_height())
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.canvas.configure(cursor="fleur" if self.mode == "actual" else "")

    def set_fullscreen(self, on: bool) -> None:
        """Full screen shows the picture alone, so on a screen the size of the capture (1920x1080) it is
        at actual size; the status lines and the prompt box come back when you leave full screen."""
        self.root.attributes("-fullscreen", on)
        if on:
            self.bar.pack_forget()
            self._panel_hidden_by_fullscreen = self.panel.shown
            self._hide_panel()
        else:
            if not self.bar.winfo_manager():
                self.bar.pack(side="bottom", fill="x", before=self.canvas)
            if self._panel_hidden_by_fullscreen:
                self._panel_hidden_by_fullscreen = False
                self._show_panel()

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not bool(self.root.attributes("-fullscreen")))

    def toggle_panel(self) -> None:
        """F2: hide or show the prompt box (its work goes on while hidden)."""
        if self.panel.shown:
            self._hide_panel()
        else:
            self._show_panel()

    def _hide_panel(self) -> None:
        # Keys go to the focused widget even when it is hidden: take the focus off the prompt, or typing would go
        # on into a box nobody can see (and Enter would ask what was typed there).
        self.canvas.focus_set()
        self.panel.frame.pack_forget()

    def _show_panel(self) -> None:
        self.panel.frame.pack(side="right", fill="y", before=self.canvas)
        self.panel.arm_later()          # a waiting approval's buttons work only a moment after it is seen again

    def _reveal_panel(self) -> None:
        """An approval is waiting: leave full screen, and show the prompt box if F2 hid it."""
        if bool(self.root.attributes("-fullscreen")):
            self.set_fullscreen(False)
        if not self.panel.shown:
            self._show_panel()

    def _unless_typing(self, action):
        """A key handler that does nothing while a text box (the prompt, the answers) has the focus."""
        def handler(_event) -> None:
            try:
                focused = self.root.focus_get()
            except (KeyError, self.tk.TclError):
                focused = None
            if not isinstance(focused, (self.tk.Text, self.tk.Entry)):
                action()
        return handler

    def start_service(self) -> None:
        """R: start the service if it isn't running. One start at a time (a held key repeats), on a
        thread so the window stays responsive; the status line says it is starting, or why it failed."""
        if self.starting:
            return
        self.start_error = None

        def start() -> None:
            try:
                self.client.ensure_running()
            except ServiceError as e:
                self.start_error = str(e)
            else:
                self.feed.retry_now()
        self._starter = threading.Thread(target=start, name="viewer-start-service", daemon=True)
        self._starter.start()


def run_viewer(client: ServiceClient, fps: int = DEFAULT_FPS, close_after: float | None = None) -> int:
    """Open the window and block until it is closed. Returns the process exit code."""
    try:
        win = ViewerWindow(client, fps, close_after)
    except Exception as e:  # noqa: BLE001  (no Tk, or no desktop to open a window on)
        print(f"error: cannot open the viewer window ({type(e).__name__}: {e}). "
              "Use 'nanokvm viewer --browser' instead.", file=sys.stderr)
        return 2
    print("The locked computer's screen is open in a window, with a prompt box for questions about it (nothing is "
          "read or sent until you ask). Close it, or click the picture and press Q, to quit.", flush=True)
    interrupted = False
    try:
        win.run()
    except KeyboardInterrupt:          # Ctrl+C in the terminal: mainloop() usually raises it
        interrupted = True
        win.close()
    win.feed.stop(wait=1.0)
    win.poller.stop(wait=1.0)
    print(win.summary(), flush=True)
    return 130 if interrupted else 0
