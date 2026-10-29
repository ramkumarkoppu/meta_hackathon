"""The shared NanoKVM service: the ONLY process that opens the NanoKVM-USB.

It captures HDMI continuously, drives keyboard/mouse over serial, and serves a small HTTP API +
live viewer on 127.0.0.1 so every Claude Code session (each runs its own MCP server) and
the user's live view (the ``nanokvm viewer`` window, a browser or VS Code Simple Browser) can
share the device.

Security: bound to 127.0.0.1 only, exclusive port bind, Host header must be localhost (blocks
DNS rebinding), and every request needs the random token stored in .run/token (header
``X-NanoKVM-Token`` or ``?t=``), so web pages cannot drive the keyboard.

Run: ``python -m nanokvm_mcp.service [--foreground]`` (normally started automatically).
"""
from __future__ import annotations

import argparse
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

from . import __version__, saved_images
from . import geometry as G
from .capture import CaptureSource, Frame, HdmiCapture
from .config import Config
from .hid import Hid, HidAborted, HidBusy, HidError, Link, SerialLink
from .keys import KeyError_, parse_combo, parse_sequence
from .protocol import ABS_RANGE

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
        self._viewer_lock = threading.Lock()
        self._viewer_cache: tuple[int, bytes] = (0, b"")
        self.viewers = 0
        self._activity = time.monotonic()
        self.started = time.time()
        self._last_space: tuple[int, int, G.Region] | None = None   # space of the last screenshot served
        self.sim_desktop = None   # set in simulator mode (tests read its state through /api/status)
        # Screen sharing: Claude conversation id -> (expiry monotonic, end wall-clock). Requests from
        # the MCP server carry X-NanoKVM-Client; captures for that client need an active share.
        self._shares: dict[str, tuple[float, float]] = {}
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
        if f is None:
            # freshly started service: opening DirectShow and negotiating MJPEG can take several seconds
            timeout = max(timeout, 12.0)
        g = self.capture.wait_frame(f.seq if f else 0, timeout=timeout)
        if g is not None:
            return g
        if f is not None:
            return f   # stale but better than nothing; meta reports its age
        raise ServiceError("No video from the locked computer yet - " + self._capture_problem(), 503)

    def _shot(self, frame: Frame) -> dict:
        region, sw, sh, source = self.coord_space(frame)
        self._last_space = (sw, sh, region)
        img = G.resize(G.crop(frame.image, region), sw, sh)
        blank, mean, _std = G.blank_stats(img)
        age_ms = int((time.monotonic() - frame.t) * 1000)
        warnings = []
        if blank:
            warnings.append("the picture is uniform (%s) - no HDMI signal, display asleep, or a blank "
                            "screen" % ("black" if mean < 20 else f"mean {mean:.0f}"))
        if age_ms > 2000:
            warnings.append(f"frame is {age_ms / 1000:.1f}s old - " + self._capture_problem())
        return {
            "seq": frame.seq,
            "captured_at": _clock(frame.wall),
            "age_ms": age_ms,
            "width": sw, "height": sh,
            "frame_size": [int(frame.image.shape[1]), int(frame.image.shape[0])],
            "region": region.as_dict(), "region_source": source,
            "warnings": warnings,
            "jpeg_b64": base64.b64encode(G.encode_jpeg(img, self.cfg.jpeg_quality)).decode("ascii"),
        }

    def screenshot(self) -> dict:
        return self._shot(self._require_frame())

    def zoom(self, x: float, y: float, w: float, h: float, max_side: int = 1456) -> dict:
        frame = self._require_frame()
        region, sw, sh, _ = self.coord_space(frame)
        if w <= 0 or h <= 0:
            raise ServiceError("zoom width and height must be positive")
        if x < 0 or y < 0 or x >= sw or y >= sh:
            raise ServiceError(f"zoom origin ({x}, {y}) is outside the screen (0..{sw - 1}, 0..{sh - 1})")
        cx, cy, cw, ch = G.screen_rect_to_capture((x, y, w, h), region, sw, sh)
        crop = frame.image[cy:cy + ch, cx:cx + cw]
        scale = min(max_side / max(cw, ch), 3.0)
        # stay inside the vision budget so the model sees these exact pixels (no silent downscale)
        out_w, out_h = G.fit_vision_budget(max(1, round(cw * scale)), max(1, round(ch * scale)))
        img = G.resize(crop, out_w, out_h)
        # screen-coordinate rectangle actually covered (after clamping)
        sx0 = (cx - region.x) * sw / region.w
        sy0 = (cy - region.y) * sh / region.h
        sx1 = (cx + cw - region.x) * sw / region.w
        sy1 = (cy + ch - region.y) * sh / region.h
        return {
            "seq": frame.seq, "captured_at": _clock(frame.wall),
            "width": out_w, "height": out_h,
            "screen_rect": [round(sx0, 1), round(sy0, 1), round(sx1 - sx0, 1), round(sy1 - sy0, 1)],
            "screen_size": [sw, sh],
            "capture_rect": [cx, cy, cw, ch],
            "jpeg_b64": base64.b64encode(G.encode_jpeg(img, max(self.cfg.jpeg_quality, 90))).decode("ascii"),
        }

    def frame(self, rect: tuple[float, float, float, float] | None = None, fmt: str = "png",
              max_tokens: int = G.VISION_MAX_TOKENS) -> dict:
        """The screen at full capture resolution, for the LOCAL vision model (Muse Glimmer): the whole
        calibrated region, or a screen-coordinate rectangle of it. Not reduced to Claude's screenshot
        size; shrunk only if it would exceed max_tokens visual tokens. Nothing is written to disk, and
        the cropped copy is zeroed once encoded."""
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

    def _wait_change(self, base: Frame, timeout_s: float, min_change_pct: float,
                     settle_ms: int) -> tuple[bool, Frame, float]:
        """Wait until the screen differs from ``base`` by >= min_change_pct %, then until it has
        been stable for settle_ms. Returns (changed, frame, change_percent)."""
        base_thumb = G.thumbnail(base.image)
        deadline = time.monotonic() + timeout_s
        seq = base.seq
        last = base
        while time.monotonic() < deadline:
            f = self.capture.wait_frame(seq, timeout=min(1.0, max(0.05, deadline - time.monotonic())))
            if f is None:
                continue
            seq, last = f.seq, f
            pct = 100.0 * G.change_fraction(base_thumb, G.thumbnail(f.image))
            if pct < min_change_pct:
                continue
            # changed: now wait for the screen to settle (animations, page loads)
            prev = G.thumbnail(f.image)
            stable_since = time.monotonic()
            hard_stop = time.monotonic() + max(3.0, 6 * settle_ms / 1000.0)
            while (time.monotonic() - stable_since) * 1000 < settle_ms and time.monotonic() < hard_stop:
                g = self.capture.wait_frame(seq, timeout=0.5)
                if g is None:
                    continue
                seq, last = g.seq, g
                t = G.thumbnail(g.image)
                if G.change_fraction(prev, t) > 0.001:
                    stable_since = time.monotonic()
                prev = t
            final_pct = 100.0 * G.change_fraction(base_thumb, G.thumbnail(last.image))
            return True, last, final_pct
        return False, last, 0.0

    def wait_change(self, timeout_s: float = 30.0, min_change_pct: float = 0.5,
                    settle_ms: int = 700, include_shot_on_timeout: bool = False) -> dict:
        timeout_s = max(0.5, min(float(timeout_s), 600.0))
        base = self._require_frame()
        t0 = time.monotonic()
        changed, frame, pct = self._wait_change(base, timeout_s, float(min_change_pct), int(settle_ms))
        out = {"changed": changed, "waited_ms": int((time.monotonic() - t0) * 1000),
               "change_percent": round(pct, 2)}
        if changed or include_shot_on_timeout:
            out["shot"] = self._shot(frame)
        return out

    # ------------------------------------------------------------ screen sharing
    SHARE_REFUSED = ("Screen sharing is off (or has expired), so the locked computer's screen cannot be captured. "
                     "Ask the user whether to share it, then call start_screen_sharing - the user approves once.")
    MAX_SHARE_MINUTES = 480

    def share_active(self, client: str | None) -> bool:
        """Captures requested by a Claude conversation (client id set) need an active share. Requests
        without a client id (the nanokvm CLI, the live view, any program with the token) are not gated."""
        if client is None:
            return True
        with self._shares_lock:
            entry = self._shares.get(client)
            if entry is None:
                return False
            if time.monotonic() >= entry[0]:
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
        end_wall = time.time() + minutes * 60
        with self._shares_lock:
            self._shares[client] = (time.monotonic() + minutes * 60, end_wall)
        log.info("screen sharing ON for client %s for %d min", client[:8], minutes)
        return {"sharing": True, "minutes": minutes, "until": time.strftime("%H:%M", time.localtime(end_wall))}

    def unshare(self, client: str | None) -> dict:
        with self._shares_lock:
            was = (self._shares.pop(client, None) is not None) if client else False
        if was:
            log.info("screen sharing OFF for client %s", client[:8])
        return {"sharing": False, "was_sharing": was}

    def active_shares(self) -> int:
        """How many Claude conversations may capture the screen right now (expired shares are dropped)."""
        now = time.monotonic()
        with self._shares_lock:
            for c in [c for c, (exp, _end) in self._shares.items() if now >= exp]:
                del self._shares[c]
            return len(self._shares)

    def share_info(self, client: str | None) -> dict | None:
        if client is None:
            return None
        if not self.share_active(client):
            return {"active": False}
        with self._shares_lock:
            entry = self._shares.get(client)
        if entry is None:
            return {"active": False}
        exp, end_wall = entry
        return {"active": True, "remaining_min": round((exp - time.monotonic()) / 60, 1),
                "until": time.strftime("%H:%M", time.localtime(end_wall))}

    # ------------------------------------------------------------ status
    def status(self, client: str | None = None) -> dict:
        region, sw, sh, source = self.coord_space()
        control: dict = {"enabled": self.cfg.control}
        if self.cfg.control:
            if self._hid is None:
                control["state"] = "not opened yet (opens on the first keyboard/mouse action)"
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
            "shares_active": self.active_shares(),
            **({"sim": {"text": self.sim_desktop.text, "button_presses": self.sim_desktop.button_presses}}
               if self.sim_desktop is not None else {}),
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
                                self.cfg.click_hold_ms)
            return self._hid

    def act(self, body: dict, client: str | None = None) -> dict:
        action = str(body.get("action", ""))
        region, sw, sh, _ = self.coord_space()
        last = self._last_space
        if action in ("click", "move", "drag", "scroll") and last is not None and last != (sw, sh, region):
            raise ServiceError(
                f"the screen coordinate space changed from {last[0]}x{last[1]} to {sw}x{sh} since the last "
                "screenshot (calibration or display change). Take a new screenshot and use its coordinates.",
                409)

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
                                   f"y is 0..{sh - 1} (coordinates of the latest screenshot)")
            return G.screen_to_abs(x, y, sw, sh)

        before = self.capture.latest()
        hid = self.hid()
        summary = ""
        try:
            if action == "click":
                ax, ay = to_abs("x", "y")
                mods = parse_combo(body["modifiers"]).modifiers if body.get("modifiers") else 0
                count = int(body.get("count", 1))
                if not 1 <= count <= 3:
                    raise ServiceError("count must be 1, 2 or 3")
                button = str(body.get("button", "left"))
                hid.click(ax, ay, button, count, mods)
                summary = f"{button} {'double-' if count == 2 else 'triple-' if count == 3 else ''}click at ({num('x'):g}, {num('y'):g})"
            elif action == "move":
                ax, ay = to_abs("x", "y")
                hid.move(ax, ay)
                summary = f"moved pointer to ({num('x'):g}, {num('y'):g})"
            elif action == "drag":
                a, b = to_abs("x1", "y1"), to_abs("x2", "y2")
                button = str(body.get("button", "left"))
                hid.drag(a, b, button)
                summary = f"dragged ({num('x1'):g}, {num('y1'):g}) -> ({num('x2'):g}, {num('y2'):g})"
            elif action == "scroll":
                ax, ay = to_abs("x", "y")
                direction = str(body.get("direction", "down")).lower()
                notches = int(body.get("notches", 3))
                if direction not in ("up", "down", "left", "right"):
                    raise ServiceError("direction must be up, down, left or right")
                if not 1 <= notches <= 50:
                    raise ServiceError("notches must be 1..50")
                sign = 1 if direction in ("up", "left") else -1
                hid.scroll(ax, ay, sign * notches, horizontal=direction in ("left", "right"))
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
                if body.get("press_enter") and not aborted:
                    hid.press([parse_combo("enter")])
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
        result: dict = {"action": action, "summary": summary}
        if body.get("screenshot_after") and not self.share_active(client):
            result["note"] = "screen sharing is off, so no screenshot was taken"
        elif body.get("screenshot_after"):
            base = before or self.capture.latest()
            if base is not None:
                changed, frame, pct = self._wait_change(base, self.cfg.post_action_ms / 1000.0, 0.05, 300)
                result["screen_changed"] = changed
                result["shot"] = self._shot(self.capture.latest() or frame)
        return result

    # ------------------------------------------------------------ calibration
    def calibrate(self, reset: bool = False) -> dict:
        if reset:
            self._calib = None
            try:
                self._calib_path.unlink()
            except OSError:
                pass
            return {"method": "reset", "message": "calibration cleared; using the full frame"}
        frame = self._require_frame()
        fh, fw = frame.image.shape[:2]
        hid = self.hid()
        result: dict = {"frame_size": [fw, fh]}
        points = [(0.2, 0.2), (0.8, 0.2), (0.5, 0.5), (0.2, 0.8), (0.8, 0.8)]
        found_abs_x, found_px_x, found_abs_y, found_px_y = [], [], [], []
        details = []

        def grab_after(delay: float) -> Frame:
            time.sleep(delay)
            f0 = self.capture.latest()
            f = self.capture.wait_frame(f0.seq if f0 else 0, timeout=2.0)
            if f is None:
                raise ServiceError("capture stopped during calibration - " + self._capture_problem(), 503)
            return f

        try:
            for nx, ny in points:
                ax, ay = int(nx * ABS_RANGE), int(ny * ABS_RANGE)
                # park the pointer far away (diagonally opposite; top-right for the centre point)
                away_nx = 0.85 if nx == 0.5 else 1 - nx
                away_ny = 0.15 if ny == 0.5 else 1 - ny
                hid.move(int(away_nx * ABS_RANGE), int(away_ny * ABS_RANGE))
                away = grab_after(0.4)
                hid.move(ax, ay)
                here = grab_after(0.4)
                expect = (ax * fw / ABS_RANGE, ay * fh / ABS_RANGE)
                pos = G.locate_cursor(here.image, away.image, expect, search_radius=0.2 * fw)
                details.append({"abs": [ax, ay], "found_px": pos})
                if pos is not None:
                    found_abs_x.append(ax)
                    found_px_x.append(pos[0])
                    found_abs_y.append(ay)
                    found_px_y.append(pos[1])
            hid.move(ABS_RANGE // 2, ABS_RANGE // 2)
        except HidError as e:
            raise ServiceError(str(e), 503) from None
        result["points"] = details
        region = None
        if len(found_abs_x) >= 3 and len(set(found_abs_x)) >= 2 and len(set(found_abs_y)) >= 2:
            fx, fy = G.fit_axis(found_abs_x, found_px_x), G.fit_axis(found_abs_y, found_px_y)
            cand = G.region_from_fit(fx, fy, fw, fh)
            plausible = (max(fx.max_residual, fy.max_residual) <= 8.0
                         and cand.w >= 0.5 * fw and cand.h >= 0.5 * fh)
            result["fit"] = {"x": vars(fx), "y": vars(fy)}
            if plausible:
                region, result["method"] = cand, "cursor"
        if region is None:
            bars = G.detect_black_bars(self._require_frame().image)
            if bars is not None:
                region, result["method"] = bars, "black-bars"
            else:
                region, result["method"] = G.Region.full(fw, fh), "full-frame (cursor not found, no bars)"
        result["region"] = region.as_dict()
        self._calib = {"frame_size": [fw, fh], "region": region.as_dict(), "method": result["method"],
                       "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._calib_path.parent.mkdir(parents=True, exist_ok=True)
        self._calib_path.write_text(json.dumps(self._calib, indent=2), encoding="utf-8")
        _r, sw, sh, _ = self.coord_space()
        result["screen_size"] = [sw, sh]
        return result

    # ------------------------------------------------------------ viewer
    def viewer_jpeg(self, frame: Frame) -> bytes:
        with self._viewer_lock:
            if self._viewer_cache[0] == frame.seq:
                return self._viewer_cache[1]
        h, w = frame.image.shape[:2]
        vw = min(self.cfg.viewer_width, w)
        img = G.resize(frame.image, vw, max(1, round(h * vw / w)))
        jpg = G.encode_jpeg(img, self.cfg.viewer_quality)
        with self._viewer_lock:
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
VIEWER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Locked computer - live</title>
<style>
html,body{margin:0;height:100%;background:#111;color:#ccc;font:13px system-ui,sans-serif}
body{display:flex;flex-direction:column}
#bar{padding:4px 10px;background:#1c1c1c;border-bottom:1px solid #333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#wrap{flex:1;min-height:0}
img{width:100%;height:100%;object-fit:contain;display:block}
</style></head>
<body><div id="bar">Locked computer &mdash; live view (read-only) &middot; <span id="st">connecting&hellip;</span></div>
<div id="wrap"><img id="v" alt="locked computer screen"></div>
<script>
const t = new URLSearchParams(location.search).get('t') || '';
const q = 't=' + encodeURIComponent(t);
const v = document.getElementById('v'), st = document.getElementById('st');
function start(){ v.src = '/stream.mjpg?' + q + '&r=' + Date.now(); }
v.onerror = () => setTimeout(start, 2000);
start();
async function poll(){
  try {
    const s = await (await fetch('/api/status?' + q)).json();
    const c = s.capture;
    st.textContent = c.state + ' \\u00b7 ' + (c.frame_size ? c.frame_size.join('x') : '?') + ' @ ' + c.fps + ' fps'
      + (c.error ? ' \\u00b7 ' + c.error : '') + (s.simulated ? ' \\u00b7 SIMULATOR' : '');
  } catch (e) { st.textContent = 'service not reachable'; }
  setTimeout(poll, 2000);
}
poll();
</script></body></html>
"""


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

    def _authorised(self, query: dict) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._json(403, {"error": "bad Host header"})
            return False
        tok = self.headers.get("X-NanoKVM-Token") or (query.get("t") or [""])[0]
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
        if not self._authorised(query):
            return
        svc = self.server.service
        if url.path == "/":
            self._send(200, VIEWER_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/stream.mjpg":
            self._stream(query)
        elif url.path == "/api/status":
            self._dispatch(lambda: svc.status(self._client()))
        elif url.path == "/api/screenshot":
            self._dispatch(lambda: (svc.require_share(self._client()), svc.screenshot())[1])
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not self._authorised(parse_qs(url.query)):
            return
        svc = self.server.service
        try:
            body = self._body()
        except ServiceError as e:
            self._json(e.status, {"error": str(e)})
            return
        client = self._client()

        def zoom() -> dict:
            svc.require_share(client)
            return svc.zoom(float(body.get("x", 0)), float(body.get("y", 0)),
                            float(body.get("w", 0)), float(body.get("h", 0)))

        def frame() -> dict:
            # Full-resolution frames are only for a client that holds an active share (the local
            # pipeline asks for one); unlike screenshot, a request without a client id is refused.
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

        def wait_change() -> dict:
            svc.require_share(client)
            r = svc.wait_change(float(body.get("timeout_s", 30)), float(body.get("min_change_percent", 0.5)),
                                int(body.get("settle_ms", 700)), bool(body.get("include_shot_on_timeout", False)))
            if "shot" in r and not svc.share_active(client):     # sharing expired while waiting
                r.pop("shot")
                r["note"] = "screen sharing expired while waiting"
            return r

        routes: dict[str, Callable[[], dict]] = {
            "/api/zoom": zoom,
            "/api/frame": frame,
            "/api/wait_change": wait_change,
            "/api/act": lambda: svc.act(body, client),
            "/api/share": lambda: svc.share(client, int(body.get("minutes", svc.cfg.share_minutes))),
            "/api/unshare": lambda: svc.unshare(client),
            "/api/calibrate": lambda: svc.calibrate(bool(body.get("reset", False))),
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
        """MJPEG live view. ``?fps=N`` (1-60) overrides viewer_fps for this viewer. Each real frame's
        part carries X-Frame-Seq and X-Frame-Time (wall-clock capture time) so the viewer window can
        show its delay; the "no video" placeholder carries neither."""
        svc = self.server.service
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            fps = int((query.get("fps") or [svc.cfg.viewer_fps])[0])
        except ValueError:
            fps = svc.cfg.viewer_fps
        period = 1.0 / max(1, min(fps, 60))
        last_seq = 0
        with svc._viewer_lock:
            svc.viewers += 1
        try:
            while not self.server.stopping.is_set():
                t0 = time.monotonic()
                f = svc.capture.wait_frame(last_seq, timeout=1.0)
                if f is None:
                    jpg, extra = svc.placeholder_jpeg(), b""
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
            svc.touch()


# ====================================================================== process entry
def build_service(cfg: Config) -> Service:
    if cfg.simulate:
        from .simulator import SimCapture, SimDesktop, SimLink
        desk = SimDesktop(cfg.capture_width, cfg.capture_height)
        svc = Service(cfg, SimCapture(desk), lambda: SimLink(desk))
        svc.sim_desktop = desk
        return svc
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
    (cfg.run_dir / "service.pid").write_text(str(os.getpid()), encoding="ascii")
    log.info("nanokvm service %s listening on %s (simulate=%s, control=%s)",
             __version__, cfg.base_url, cfg.simulate, cfg.control)

    def sweep_saved(everything: bool = False) -> None:
        try:
            saved_images.sweep(cfg.run_dir, cfg.saved_image_minutes, everything=everything)
        except OSError as e:
            log.warning("could not clean up saved screenshots: %s", e)

    def watchdog() -> None:
        while not server.stopping.wait(15):
            sweep_saved()
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
        try:
            (cfg.run_dir / "service.pid").unlink()
        except OSError:
            pass
        sweep_saved(everything=True)   # nothing would delete them later
        log.info("service stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nanokvm_mcp.service")
    ap.add_argument("--foreground", action="store_true", help="also log to the console")
    args = ap.parse_args(argv)
    return serve(Config.load(), foreground=args.foreground)


if __name__ == "__main__":
    sys.exit(main())
