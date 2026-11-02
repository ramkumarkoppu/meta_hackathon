"""The shared NanoKVM service: the ONLY process that opens the NanoKVM-USB.

It captures HDMI continuously, drives keyboard/mouse over serial, and serves a small HTTP API and
the live picture on 127.0.0.1, so every nanokvm command and the ``nanokvm viewer`` window can share
the device.

Security: bound to 127.0.0.1 only, exclusive port bind, Host header must be localhost (blocks
DNS rebinding), and every request needs the random token stored in .run/token (header
``X-NanoKVM-Token``, never in a URL), so web pages cannot drive the keyboard.

Run: ``python -m nanokvm_mcp.service`` (started automatically; ``nanokvm serve`` runs it in the foreground).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import logging.handlers
import math
import os
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

from . import __version__
from . import geometry as G
from .capture import CaptureSource, Frame, HdmiCapture
from .config import Config
from .hid import Hid, HidAborted, HidBusy, HidError, Link, SerialLink
from .keys import KeyError_, parse_sequence

log = logging.getLogger("nanokvm.service")
SERVICE_NAME = "nanokvm"


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def load_or_create_token(run_dir: Path) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "token"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        for _ in range(20):
            tok = path.read_text(encoding="ascii").strip()
            if tok:
                return tok
            time.sleep(0.05)
        raise RuntimeError(f"{path} is empty")
    tok = secrets.token_urlsafe(24)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(tok)
    return tok


def _clock(wall: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(wall)) + f".{int((wall % 1) * 1000):03d}"


class Service:
    def __init__(self, cfg: Config, capture: CaptureSource, link_factory: Callable[[], Link] | None) -> None:
        self.cfg = cfg
        self.capture = capture
        self._link_factory = link_factory
        self._hid: Hid | None = None
        self._hid_lock = threading.Lock()
        self._calib_path = cfg.run_dir / "calibration.json"
        self._calib = self._load_calibration()
        self._viewer_lock = threading.Lock()                # guards the viewers counter
        self._encode_lock = threading.Lock()                # one live-view encode at a time (viewer_jpeg)
        self._viewer_cache: tuple[int, bytes] = (0, b"")
        self.viewers = 0
        self._activity = time.monotonic()
        self.started = time.time()
        # Screen sharing: client id -> expiry (monotonic). Requests from nanokvm read/ask/edit/fix and the
        # viewer's prompt box carry X-NanoKVM-Client; captures for that client need an active share.
        self._shares: dict[str, float] = {}
        self._shares_lock = threading.Lock()

    # ------------------------------------------------------------ bookkeeping
    def touch(self) -> None:
        self._activity = time.monotonic()

    def idle_seconds(self) -> float:
        return 0.0 if self.viewers else time.monotonic() - self._activity

    def close(self) -> None:
        if self._hid is not None:
            try:
                self._hid.close()
            except Exception as e:  # noqa: BLE001
                log.warning("closing keyboard/mouse failed: %s", e)
        self.capture.close()

    # ------------------------------------------------------------ geometry
    def _load_calibration(self) -> dict | None:
        try:
            return json.loads(self._calib_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def region_for(self, frame_w: int, frame_h: int) -> tuple[G.Region, str]:
        manual = self.cfg.region_tuple
        if manual:
            return G.Region(*manual).clamp_to(frame_w, frame_h), "manual (nanokvm.toml region)"
        c = self._calib
        if c and c.get("frame_size") == [frame_w, frame_h] and c.get("region"):
            r = c["region"]
            return G.Region(r["x"], r["y"], r["w"], r["h"]).clamp_to(frame_w, frame_h), \
                f"calibration ({c.get('method')})"
        return G.Region.full(frame_w, frame_h), "full frame"

    def coord_space(self, frame: Frame | None = None) -> tuple[G.Region, int, int, str]:
        frame = frame or self.capture.latest()
        if frame is not None:
            fh, fw = frame.image.shape[:2]
        else:
            fw, fh = self.cfg.capture_width, self.cfg.capture_height
        region, source = self.region_for(fw, fh)
        sw, sh = G.shot_size(region, self.cfg.shot_width, self.cfg.aspect_value)
        return region, sw, sh, source

    # ------------------------------------------------------------ frames
    def _capture_problem(self) -> str:
        st = self.capture.status()
        return f"capture state '{st.get('state')}': {st.get('error') or 'no frames yet'}"

    def _require_frame(self, timeout: float = 4.0) -> Frame:
        f = self.capture.latest()
        if f is not None and time.monotonic() - f.t < 1.0:
            return f
        if f is None and self.capture.status().get("state") == "starting":
            # freshly started service: opening DirectShow and negotiating MJPEG can take several seconds
            timeout = max(timeout, 12.0)
        g = self.capture.wait_frame(f.seq if f else 0, timeout=timeout)
        if g is not None:
            return g
        if f is not None:
            return f   # stale but better than nothing; meta reports its age
        raise ServiceError("No video from the locked computer yet - " + self._capture_problem(), 503)

    def frame(self, rect: tuple[float, float, float, float] | None = None, fmt: str = "png",
              max_tokens: int = G.VISION_MAX_TOKENS) -> dict:
        """The screen at full capture resolution, for the LOCAL vision model (Muse Glimmer): the whole
        region, or a screen-coordinate rectangle of it. Not scaled to the coordinate space (shot_width);
        shrunk only if it would exceed max_tokens visual tokens. Nothing is written to disk, and the
        cropped copy is zeroed once encoded."""
        if fmt not in ("png", "jpeg"):
            raise ServiceError("format must be 'png' or 'jpeg'")
        max_tokens = max(64, min(int(max_tokens), G.VISION_MAX_TOKENS))
        frame = self._require_frame()
        region, sw, sh, _ = self.coord_space(frame)
        if rect is None:
            cx, cy, cw, ch = region.x, region.y, region.w, region.h
        else:
            x, y, w, h = rect
            if w <= 0 or h <= 0:
                raise ServiceError("frame width and height must be positive")
            if x < 0 or y < 0 or x >= sw or y >= sh:
                raise ServiceError(f"frame origin ({x}, {y}) is outside the screen (0..{sw - 1}, 0..{sh - 1})")
            cx, cy, cw, ch = G.screen_rect_to_capture(rect, region, sw, sh)
        crop = frame.image[cy:cy + ch, cx:cx + cw].copy()
        out_w, out_h = G.fit_token_budget(cw, ch, max_tokens)
        img = G.resize(crop, out_w, out_h)
        data = G.encode_png(img) if fmt == "png" else G.encode_jpeg(img, 95)
        blank, mean, _std = G.blank_stats(img)
        img.fill(0)
        crop.fill(0)
        age_ms = int((time.monotonic() - frame.t) * 1000)
        warnings = []
        if blank:
            warnings.append("the picture is uniform (%s) - no HDMI signal, display asleep, or a blank "
                            "screen" % ("black" if mean < 20 else f"mean {mean:.0f}"))
        if age_ms > 2000:
            warnings.append(f"frame is {age_ms / 1000:.1f}s old - " + self._capture_problem())
        sx0, sy0 = (cx - region.x) * sw / region.w, (cy - region.y) * sh / region.h
        sx1, sy1 = (cx + cw - region.x) * sw / region.w, (cy + ch - region.y) * sh / region.h
        return {
            "seq": frame.seq, "captured_at": _clock(frame.wall), "age_ms": age_ms,
            "frame_size": [int(frame.image.shape[1]), int(frame.image.shape[0])],
            "capture_rect": [cx, cy, cw, ch],
            "screen_rect": [round(sx0, 1), round(sy0, 1), round(sx1 - sx0, 1), round(sy1 - sy0, 1)],
            "screen_size": [sw, sh],
            "width": out_w, "height": out_h, "format": fmt,
            "visual_tokens": G.vision_tokens(out_w, out_h),
            "image_sha256": hashlib.sha256(data).hexdigest(),
            "warnings": warnings,
            "image_b64": base64.b64encode(data).decode("ascii"),
        }

    # ------------------------------------------------------------ screen sharing
    SHARE_REFUSED = ("Screen sharing is off (or has expired), so the locked computer's screen cannot be captured. "
                     "A screen share approved at this laptop is needed first.")
    MAX_SHARE_MINUTES = 480

    def share_active(self, client: str | None) -> bool:
        """Captures requested with a client id need an active share (the live view sends none, and
        /api/frame refuses requests without one)."""
        if client is None:
            return True
        with self._shares_lock:
            expiry = self._shares.get(client)
            if expiry is None:
                return False
            if time.monotonic() >= expiry:
                del self._shares[client]
                return False
            return True

    def require_share(self, client: str | None) -> None:
        if not self.share_active(client):
            raise ServiceError(self.SHARE_REFUSED, 403)

    def share(self, client: str | None, minutes: int) -> dict:
        if not client:
            raise ServiceError("screen sharing needs a client id (X-NanoKVM-Client header)")
        minutes = max(1, min(int(minutes), self.MAX_SHARE_MINUTES))
        with self._shares_lock:
            self._shares[client] = time.monotonic() + minutes * 60
        log.info("screen sharing ON for client %s for %d min", client[:8], minutes)
        return {"sharing": True, "minutes": minutes}

    def unshare(self, client: str | None) -> dict:
        with self._shares_lock:
            was = (self._shares.pop(client, None) is not None) if client else False
        if was:
            log.info("screen sharing OFF for client %s", client[:8])
        return {"sharing": False, "was_sharing": was}

    def active_shares(self) -> int:
        """How many readers (nanokvm read/ask/edit/fix, the viewer's prompt box) may capture the screen
        right now. Expired shares are dropped."""
        now = time.monotonic()
        with self._shares_lock:
            for c in [c for c, exp in self._shares.items() if now >= exp]:
                del self._shares[c]
            return len(self._shares)

    def share_info(self, client: str | None) -> dict | None:
        if client is None:
            return None
        if not self.share_active(client):
            return {"active": False}
        with self._shares_lock:
            exp = self._shares.get(client)
        if exp is None:
            return {"active": False}
        return {"active": True, "remaining_min": round((exp - time.monotonic()) / 60, 1)}

    # ------------------------------------------------------------ status
    def status(self, client: str | None = None) -> dict:
        region, sw, sh, source = self.coord_space()
        control: dict = {"enabled": self.cfg.control}
        if self.cfg.control:
            if self._hid is None:
                control["state"] = "not opened yet (opens on the first keyboard/mouse action)"
            elif not getattr(self._hid.link, "is_open", True):
                # never (re)open the port for a status request: opening it sends an all-keys-up report
                # to the locked computer (a refused action or a dropped link leaves it closed)
                control["state"] = "not open (opens on the next keyboard/mouse action)"
            else:
                control["link"] = self._hid.link.describe()
                try:
                    info, busy = self._hid.info_nowait(max_age=5.0)   # never waits for a running action
                    control["state"] = "busy (an action is running)" if busy else "ok"
                    if info:
                        control["device_info"] = info.as_dict()
                    elif not busy:
                        control["device_info"] = "no GET_INFO reply (device may not implement it)"
                except HidError as e:
                    control["state"] = "error"
                    control["error"] = str(e)
        latest = self.capture.latest()
        blank = None
        if latest is not None:
            blank = G.blank_stats(latest.image)[0]
        return {
            "service": SERVICE_NAME, "version": __version__, "pid": os.getpid(),
            "uptime_s": int(time.time() - self.started),
            "simulated": self.cfg.simulate,
            "capture": {**self.capture.status(), "blank": blank},
            "control": control,
            "screen": {"width": sw, "height": sh, "region": region.as_dict(), "region_source": source},
            "viewers": self.viewers,
            "idle_exit_min": self.cfg.idle_exit_min,
            "sharing": self.share_info(client),
            "shares_active": self.active_shares(),   # nanokvm read/ask/edit/fix, the viewer's prompt box (Glimmer)
        }

    # ------------------------------------------------------------ keyboard / mouse
    def hid(self) -> Hid:
        if not self.cfg.control:
            raise ServiceError("keyboard/mouse control is disabled (control = false in nanokvm.toml)", 403)
        if self._link_factory is None:
            raise ServiceError("keyboard/mouse is not available in this service", 503)
        with self._hid_lock:
            if self._hid is None:
                self._hid = Hid(self._link_factory(), self.cfg.key_hold_ms, self.cfg.key_gap_ms,
                                self.cfg.click_hold_ms, wake_after_s=self.cfg.wake_after_s)
            return self._hid

    def act(self, body: dict) -> dict:
        action = str(body.get("action", ""))
        _region, sw, sh, _ = self.coord_space()

        def num(name: str) -> float:
            if name not in body:
                raise ServiceError(f"missing parameter '{name}'")
            try:
                v = float(body[name])
            except (TypeError, ValueError):
                raise ServiceError(f"parameter '{name}' must be a number") from None
            if not math.isfinite(v):
                raise ServiceError(f"parameter '{name}' must be finite")
            return v

        def to_abs(xn: str, yn: str) -> tuple[int, int]:
            x, y = num(xn), num(yn)
            if not (0 <= x < sw and 0 <= y < sh):
                raise ServiceError(f"({x:g}, {y:g}) is outside the screen; valid x is 0..{sw - 1}, "
                                   f"y is 0..{sh - 1} (the screen's coordinate space, which nanokvm status shows)")
            return G.screen_to_abs(x, y, sw, sh)

        hid = self.hid()
        summary = ""
        try:
            if action == "click":
                ax, ay = to_abs("x", "y")
                count = int(body.get("count", 1))
                if not 1 <= count <= 3:
                    raise ServiceError("count must be 1, 2 or 3")
                button = str(body.get("button", "left"))
                hid.click(ax, ay, button, count)
                summary = f"{button} {'double-' if count == 2 else 'triple-' if count == 3 else ''}click at ({num('x'):g}, {num('y'):g})"
            elif action == "move":
                ax, ay = to_abs("x", "y")
                hid.move(ax, ay)
                summary = f"moved pointer to ({num('x'):g}, {num('y'):g})"
            elif action == "scroll":
                ax, ay = to_abs("x", "y")
                direction = str(body.get("direction", "down")).lower()
                notches = int(body.get("notches", 3))
                if direction not in ("up", "down"):
                    raise ServiceError("direction must be up or down")
                if not 1 <= notches <= 50:
                    raise ServiceError("notches must be 1..50")
                sign = 1 if direction == "up" else -1
                hid.scroll(ax, ay, sign * notches)
                summary = f"scrolled {direction} {notches} notch(es) at ({num('x'):g}, {num('y'):g})"
            elif action == "type":
                text = str(body.get("text", ""))
                if not text:
                    raise ServiceError("text is empty")
                if len(text) > self.cfg.max_text_len:
                    raise ServiceError(f"text is {len(text)} characters; the limit per call is "
                                       f"{self.cfg.max_text_len}. Split it into several calls.")
                layout = self.cfg.keyboard_layout
                typed, skipped, aborted = hid.type_text(text, layout)
                summary = f"typed {typed} character(s)"
                if skipped:
                    uniq = "".join(sorted(set(skipped)))
                    summary += (f"; skipped {len(skipped)} character(s) that cannot be typed on the "
                                f"{layout.upper()} keyboard layout: {uniq!r}")
                if aborted:
                    summary += "; STOPPED early by release_all"
            elif action == "keys":
                combos = parse_sequence(str(body.get("keys", "")), self.cfg.keyboard_layout)
                repeat = int(body.get("repeat", 1))
                if not 1 <= repeat <= 50:
                    raise ServiceError("repeat must be 1..50")
                sent = hid.press(combos, repeat)
                summary = f"pressed {' '.join(str(c) for c in combos)}" + (f" x{repeat}" if repeat > 1 else "")
                if sent < len(combos) * repeat:
                    summary += f" (stopped after {sent})"
            elif action == "release_all":
                hid.release_all()
                summary = "released all keys and mouse buttons"
            else:
                raise ServiceError(f"unknown action {action!r}")
        except KeyError_ as e:
            raise ServiceError(str(e)) from None
        except (HidBusy, HidAborted) as e:
            raise ServiceError(str(e), 409) from None
        except HidError as e:
            raise ServiceError(str(e), 503) from None
        note, hid.wake_note = hid.wake_note, None
        if note:                                     # the wake-up before this action couldn't confirm the keys
            summary += f" (note: {note})"
        return {"action": action, "summary": summary}

    # ------------------------------------------------------------ viewer
    def viewer_jpeg(self, frame: Frame) -> bytes:
        """The live-view JPEG of a frame. One encode per frame however many viewers wait for it: they
        are woken together, so the encode runs under its own lock and the others take the cached copy."""
        with self._encode_lock:
            seq, jpg = self._viewer_cache
            if seq == frame.seq:
                return jpg
            h, w = frame.image.shape[:2]
            vw = min(self.cfg.viewer_width, w)
            jpg = G.encode_jpeg(G.resize(frame.image, vw, max(1, round(h * vw / w))), self.cfg.viewer_quality)
            if frame.seq > seq:                      # a viewer that fell behind never moves the cache back
                self._viewer_cache = (frame.seq, jpg)
            return jpg

    def placeholder_jpeg(self) -> bytes:
        w = self.cfg.viewer_width
        img = np.full((w * 9 // 16, w, 3), 30, np.uint8)
        st = self.capture.status()
        lines = ["locked computer: no video", f"capture: {st.get('state')}"]
        err = st.get("error") or ""
        while err:
            lines.append(err[:70])
            err = err[70:]
        for i, line in enumerate(lines[:8]):
            cv2.putText(img, line, (30, 60 + 40 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 1, cv2.LINE_AA)
        return G.encode_jpeg(img, 70)


# ====================================================================== HTTP layer
class KvmHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False   # on Windows SO_REUSEADDR would allow port hijacking

    def __init__(self, addr, handler, service: Service, token: str) -> None:
        self.service = service
        self.token = token
        self.stopping = threading.Event()
        super().__init__(addr, handler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def request_stop(self) -> None:
        if not self.stopping.is_set():
            self.stopping.set()
            threading.Thread(target=self.shutdown, name="http-shutdown", daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server: KvmHTTPServer
    server_version = "nanokvm/" + __version__
    timeout = 30   # socket timeout: a stalled live-view client must not pin a thread (and the service) forever

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.debug("%s - " + fmt, self.address_string(), *args)

    # ---- helpers
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _authorised(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._json(403, {"error": "bad Host header"})
            return False
        tok = self.headers.get("X-NanoKVM-Token") or ""
        if not secrets.compare_digest(tok, self.server.token):
            self._json(401, {"error": "missing or wrong token (see .run/token)"})
            return False
        return True

    def _client(self) -> str | None:
        c = (self.headers.get("X-NanoKVM-Client") or "").strip()
        return c[:64] or None

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ServiceError("request too large", 413)
        if n == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            raise ServiceError("body is not valid JSON") from None
        if not isinstance(data, dict):
            raise ServiceError("body must be a JSON object")
        return data

    def _dispatch(self, fn: Callable[[], dict]) -> None:
        svc = self.server.service
        svc.touch()
        try:
            self._json(200, fn())
        except ServiceError as e:
            self._json(e.status, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("internal error")
            self._json(500, {"error": f"internal error: {type(e).__name__}: {e}"})
        finally:
            svc.touch()

    # ---- routes
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._authorised():
            return
        svc = self.server.service
        if url.path == "/stream.mjpg":
            self._stream(query)
        elif url.path == "/api/status":
            self._dispatch(lambda: svc.status(self._client()))
        else:
            self._json(404, {"error": "not found"})

    def _drain(self) -> None:
        """Read and drop a refused request's body: closing with it unread makes Windows reset the
        connection, and the client may then never see the refusal."""
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            if n > 0:
                self.rfile.read(n)
        except (ValueError, OSError):
            pass

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not self._authorised():
            self._drain()
            return
        svc = self.server.service
        try:
            body = self._body()
        except ServiceError as e:
            self._json(e.status, {"error": str(e)})
            return
        client = self._client()

        def frame() -> dict:
            # Full-resolution frames are only for a client that holds an active share (the local
            # pipeline asks for one); a request without a client id is refused.
            if client is None:
                raise ServiceError("full-resolution frames need a client id (X-NanoKVM-Client) "
                                   "and an active screen share", 403)
            svc.require_share(client)
            rect = None
            if any(k in body for k in ("x", "y", "w", "h")):
                rect = (float(body.get("x", 0)), float(body.get("y", 0)),
                        float(body.get("w", 0)), float(body.get("h", 0)))
            return svc.frame(rect, str(body.get("format", "png")),
                             int(body.get("max_tokens", G.VISION_MAX_TOKENS)))

        routes: dict[str, Callable[[], dict]] = {
            "/api/frame": frame,
            "/api/act": lambda: svc.act(body),
            "/api/share": lambda: svc.share(client, int(body.get("minutes", 1))),
            "/api/unshare": lambda: svc.unshare(client),
            "/api/shutdown": self._shutdown,
        }
        fn = routes.get(url.path)
        if fn is None:
            self._json(404, {"error": "not found"})
        else:
            self._dispatch(fn)

    def _shutdown(self) -> dict:
        log.info("shutdown requested over HTTP")
        self.server.request_stop()
        return {"stopping": True}

    def _stream(self, query: dict) -> None:
        """MJPEG live view for the viewer window. ``?fps=N`` (1-60, default 30). Each
        real frame's part carries X-Frame-Seq and X-Frame-Time (wall-clock capture time) so the viewer
        window can show its delay; the "no video" picture carries X-Frame-Placeholder: 1 instead.
        Like every capture, a request with a client id needs an active share."""
        svc = self.server.service
        client = self._client()
        if client is not None and not svc.share_active(client):
            self._json(403, {"error": svc.SHARE_REFUSED})
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            fps = int((query.get("fps") or [30])[0])
        except ValueError:
            fps = 30
        period = 1.0 / max(1, min(fps, 60))
        last_seq = 0
        with svc._viewer_lock:
            svc.viewers += 1
        try:
            while not self.server.stopping.is_set():
                t0 = time.monotonic()
                f = svc.capture.wait_frame(last_seq, timeout=1.0)
                if f is None:
                    jpg, extra = svc.placeholder_jpeg(), b"X-Frame-Placeholder: 1\r\n"
                else:
                    last_seq = f.seq
                    jpg = svc.viewer_jpeg(f)
                    extra = b"X-Frame-Seq: %d\r\nX-Frame-Time: %.6f\r\n" % (f.seq, f.wall)
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n" + extra + b"Content-Length: "
                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                self.wfile.flush()
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with svc._viewer_lock:
                svc.viewers -= 1
                last = svc.viewers == 0
            if last:                                  # nobody is watching: don't keep the last picture
                with svc._encode_lock:
                    svc._viewer_cache = (0, b"")
            svc.touch()


# ====================================================================== process entry
def build_service(cfg: Config) -> Service:
    if cfg.simulate:
        from .simulator import SimCapture, SimDesktop, SimLink
        desk = SimDesktop(cfg.capture_width, cfg.capture_height)
        return Service(cfg, SimCapture(desk), lambda: SimLink(desk))
    capture = HdmiCapture(cfg.video_id_list, cfg.video_index, cfg.capture_width, cfg.capture_height,
                          cfg.capture_fps, cfg.capture_fourcc)
    factory = (lambda: SerialLink(cfg.serial_port, cfg.serial_id_list, cfg.baud, cfg.video_id_list)) \
        if cfg.control else None
    return Service(cfg, capture, factory)


def make_server(cfg: Config, service: Service, token: str) -> KvmHTTPServer:
    return KvmHTTPServer(("127.0.0.1", cfg.port), Handler, service, token)


def _setup_logging(cfg: Config, foreground: bool) -> None:
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        cfg.run_dir / "service.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if foreground and sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)


def serve(cfg: Config, foreground: bool = False) -> int:
    _setup_logging(cfg, foreground)
    token = load_or_create_token(cfg.run_dir)
    # Bind first: if another service already owns the port, exit before touching any device.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", cfg.port))
        probe.close()
    except OSError as e:
        log.error("port %d is busy (%s); another nanokvm service is probably running", cfg.port, e)
        return 3
    service = build_service(cfg)
    try:
        server = make_server(cfg, service, token)
    except OSError as e:
        log.error("cannot bind 127.0.0.1:%d: %s", cfg.port, e)
        service.close()
        return 3
    log.info("nanokvm service %s listening on %s (simulate=%s, control=%s)",
             __version__, cfg.base_url, cfg.simulate, cfg.control)

    def watchdog() -> None:
        while not server.stopping.wait(15):
            if cfg.idle_exit_min > 0 and service.idle_seconds() > cfg.idle_exit_min * 60:
                log.info("idle for %.0f min, exiting", cfg.idle_exit_min)
                server.request_stop()
                return

    threading.Thread(target=watchdog, name="idle-watchdog", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        # Release the camera and COM port BEFORE closing the listening socket: clients treat
        # "connection refused" as "stopped", so the devices must already be free by then.
        service.close()
        server.server_close()
        log.info("service stopped")
    return 0


def main() -> int:
    return serve(Config.load())


if __name__ == "__main__":
    sys.exit(main())
