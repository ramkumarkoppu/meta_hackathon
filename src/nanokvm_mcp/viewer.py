"""``nanokvm viewer``: the locked computer's screen, live, in a window on this laptop (Tk).

Made for checking the setup by eye. The window is read-only:
  * it plays the service's MJPEG live stream (/stream.mjpg) and polls /api/status, so the service
    stays the only process that opens the NanoKVM;
  * it never opens the keyboard/mouse link and sends nothing to the locked computer: keys and
    clicks in the window stay on this laptop;
  * frames live only in memory; nothing is written to disk.
Like the browser live view it uses the service token without a client id, so Claude's screen-sharing
gate doesn't apply: it is for the person at this laptop, and Claude can't see the window.

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
from .client import ServiceClient, ServiceError, ServiceUnreachable
from .config import Config

DEFAULT_FPS = 30               # the capture runs at about 30 fps
MAX_PART = 16_000_000          # bytes; a 1920x1080 JPEG is far smaller

BG, BAR_BG, FG = "#111111", "#1c1c1c", "#cccccc"
OK_FG, WARN_FG, BAD_FG = "#8fd18f", "#e3b341", "#ff7b72"


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
    seq: int | None                  # capture sequence number; None for the service's "no video" picture
    captured: float | None           # wall-clock capture time (time.time()) sent by the service


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
        self.reconnects = 0
        self._latest: Picture | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="viewer-stream", daemon=True)

    def start(self) -> "LiveFeed":
        self._thread.start()
        return self

    def stop(self, wait: float = 0.0) -> None:
        self._stop.set()
        if wait and self._thread.is_alive():
            self._thread.join(wait)

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
                if self._stop.is_set():
                    return
                self.reconnects += 1
            self._stop.wait(backoff)
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
                      _number(headers.get("x-frame-seq"), int), _number(headers.get("x-frame-time"), float))
        with self._lock:
            self._latest = pic
            self.parts += 1


class StatusPoller:
    """Fetches /api/status about once a second on a background thread, so the window never waits on
    HTTP. Also looks for the NanoKVM's COM port by USB ID (listed only, never opened)."""

    def __init__(self, client: ServiceClient, period: float = 1.0) -> None:
        self.client = client
        self.period = period
        self.latest: tuple[dict | None, str | None] = (None, None)    # (status, error)
        self.serial_port: str | None = None
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
                self.serial_port = find_serial_port(self.client.cfg)
            polls += 1
            try:
                self.latest = (self.client.request("GET", "/api/status", timeout=3.0), None)
            except ServiceError as e:
                self.latest = (None, str(e))
            self._stop.wait(self.period)


def find_serial_port(cfg: Config) -> str | None:
    """The NanoKVM keyboard/mouse COM port(s), by USB ID as in nanokvm diag: listed, never opened."""
    if cfg.serial_port:
        return cfg.serial_port
    try:
        from .hid import list_serial_ports
        hits = [p["device"] for p in list_serial_ports() if (p["vid"], p["pid"]) in cfg.serial_id_list]
    except Exception:  # noqa: BLE001  (pyserial missing, malformed serial_ids, ...)
        return None
    return ", ".join(hits) or None


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

    def record(self, pic: Picture) -> None:
        now = time.monotonic()
        self.shown += 1
        self._recent.append(now)
        if pic.seq is None:
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


def video_line(status: dict | None, status_error: str | None, feed, stats: ViewStats) -> tuple[str, str]:
    """The window's first status line (video) and its colour. ``feed`` is the LiveFeed."""
    if feed.state != "streaming":
        why = feed.error or status_error or "Connecting to the NanoKVM service…"
        if feed.unreachable:
            why += " – press R to start it"
        return why, (FG if feed.state == "connecting" else BAD_FG)
    if status is None:
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
    colour = OK_FG
    if cap.get("blank"):
        parts.append("the picture is uniform: no HDMI signal, display asleep, or a blank screen?")
        colour = WARN_FG
    idle = stats.seconds_since_new()
    if idle is not None and idle > 2:
        parts.append(f"no new frame for {idle:.0f} s")
        colour = WARN_FG
    return " · ".join(parts), colour


def keyboard_text(control: dict, serial_port: str | None) -> str:
    if not control.get("enabled"):
        return "Keyboard/mouse: off (control = false)"
    if control.get("link"):                        # opened by an action from Claude or the nanokvm command
        text = f"Keyboard/mouse: {control['link']}"
        info = control.get("device_info")
        if isinstance(info, dict):
            text += (f", chip {info.get('chip_version')}, locked computer USB "
                     + ("connected" if info.get("target_usb_connected") else "NOT connected"))
        if control.get("error"):
            text += f" ({control['error']})"
        return text
    if serial_port:
        return f"Keyboard/mouse: {serial_port} found (opens on Claude's first action)"
    return "Keyboard/mouse: NanoKVM COM port not found (check the HOST-end USB-C cable)"


def other_line(status: dict | None, serial_port: str | None) -> tuple[str, str]:
    """The second status line (keyboard/mouse, Claude's screen sharing, keys) and its colour."""
    if status is None:
        kb, share, n = "Keyboard/mouse: ?", "Claude screen sharing: ?", None
    else:
        kb = keyboard_text(status.get("control") or {}, serial_port)
        n = status.get("shares_active")
        share = ("Claude screen sharing: ?" if n is None else "Claude screen sharing: off" if n == 0
                 else f"Claude screen sharing: ON ({n} conversation{'' if n == 1 else 's'})")
    keys = "read-only · F11 full screen, 1 actual pixels, Q quit"
    return f"{kb} · {share} · {keys}", (WARN_FG if n else FG)


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


class ViewerWindow:
    TICK_MS = 8                     # how often the window looks for a new picture

    def __init__(self, client: ServiceClient, fps: int = DEFAULT_FPS, close_after: float | None = None) -> None:
        import tkinter as tk
        _dpi_aware()
        self.tk = tk
        self.client = client
        self.root = tk.Tk()
        self.root.title("Locked computer – live view (read-only)")
        self.root.configure(bg=BG)
        self.root.minsize(320, 240)
        self.root.report_callback_exception = self._callback_error
        bar = tk.Frame(self.root, bg=BAR_BG)
        bar.pack(side="bottom", fill="x")
        self.line1 = tk.Label(bar, anchor="w", bg=BAR_BG, fg=FG, font=("Segoe UI", 9), padx=8)
        self.line2 = tk.Label(bar, anchor="w", bg=BAR_BG, fg=FG, font=("Segoe UI", 9), padx=8)
        self.line1.pack(side="top", fill="x")
        self.line2.pack(side="top", fill="x")
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.note_item = self.canvas.create_text(0, 0, fill=FG, font=("Segoe UI", 12),
                                                 text="Connecting to the NanoKVM service…")
        self.photo = None           # the picture on screen; Tk shows nothing once it is garbage-collected
        self.mode = "fit"           # "fit" the window, or "actual" pixels (drag to pan)
        self.closed = False
        self.start_error: str | None = None
        self.stats = ViewStats()
        self.feed = LiveFeed(client, fps)
        self.poller = StatusPoller(client)
        self._placed: tuple[int, int, int, int] | None = None
        self._next_bar = 0.0

        fw, fh = max(1, client.cfg.capture_width), max(1, client.cfg.capture_height)
        w, h = fit_size(fw, fh, min(fw, int(self.root.winfo_screenwidth() * 0.8)),
                        min(fh, int(self.root.winfo_screenheight() * 0.75)))
        self.root.geometry(f"{w}x{h + 2 * self.line1.winfo_reqheight()}")
        self.feed.view = ("fit", w, h)
        self.canvas.coords(self.note_item, w // 2, h // 2)

        for seq in ("<F11>", "<Key-f>", "<Key-F>", "<Double-Button-1>"):
            self.root.bind(seq, lambda _e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda _e: self.set_fullscreen(False))
        self.root.bind("<Key-1>", lambda _e: self.toggle_actual_size())
        for seq in ("<Key-r>", "<Key-R>"):
            self.root.bind(seq, lambda _e: self.start_service())
        for seq in ("<Key-q>", "<Key-Q>", "<Control-w>"):
            self.root.bind(seq, lambda _e: self.close())
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
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
        self.feed.stop()
        self.poller.stop()
        try:
            self.root.destroy()
        except self.tk.TclError:
            pass

    def summary(self) -> str:
        text = self.stats.summary()
        if self.feed.reconnects:
            text += f" The stream reconnected {self.feed.reconnects} time(s)."
        return text

    def _callback_error(self, exc, val, tb) -> None:
        if issubclass(exc, KeyboardInterrupt):      # Ctrl+C in the terminal that started the viewer
            self.close()
        else:
            traceback.print_exception(exc, val, tb)

    def _tick(self) -> None:
        if self.closed:
            return
        try:
            pic = self.feed.take()
            if pic is not None:
                self._show(pic)
            if time.monotonic() >= self._next_bar:
                self._next_bar = time.monotonic() + 0.25
                self._update_bar()
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

    def _update_bar(self) -> None:
        status, error = self.poller.latest
        text, colour = video_line(status, self.start_error or error, self.feed, self.stats)
        self.line1.configure(text=text, fg=colour)
        text, colour = other_line(status, self.poller.serial_port)
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
        self.root.attributes("-fullscreen", on)

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not bool(self.root.attributes("-fullscreen")))

    def start_service(self) -> None:
        """R: start the service if it isn't running (on a thread, so the window stays responsive)."""
        def start() -> None:
            try:
                self.client.ensure_running()
                self.start_error = None
            except ServiceError as e:
                self.start_error = str(e)
        threading.Thread(target=start, name="viewer-start-service", daemon=True).start()


def run_viewer(client: ServiceClient, fps: int = DEFAULT_FPS, close_after: float | None = None) -> int:
    """Open the window and block until it is closed. Returns the process exit code."""
    try:
        win = ViewerWindow(client, fps, close_after)
    except Exception as e:  # noqa: BLE001  (no Tk, or no desktop to open a window on)
        print(f"error: cannot open the viewer window ({type(e).__name__}: {e}). "
              "Use 'nanokvm viewer --browser' instead.", file=sys.stderr)
        return 2
    print("The locked computer's screen is open in a window (read-only). Close it or press Q to quit.",
          flush=True)
    win.run()
    win.feed.stop(wait=1.0)
    win.poller.stop(wait=1.0)
    print(win.summary(), flush=True)
    return 0
