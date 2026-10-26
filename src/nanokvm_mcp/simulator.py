"""A simulated Work laptop: synthetic HDMI frames plus a fake serial link that reacts to the
same keyboard/mouse packets the real NanoKVM receives. Used by the tests and by
``NANOKVM_SIMULATE=1`` so the whole chain can be exercised without hardware. Keystrokes are
decoded with the US layout, so with keyboard_layout = "uk" symbols such as " @ # £ come out wrong."""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from . import protocol as P
from .capture import Frame, FrameStore
from .keys import CHARS

# HID usage (+shift) -> character, inverted from the US typing table.
_REVERSE: dict[tuple[int, bool], str] = {}
for _ch, (_usage, _shift) in CHARS.items():
    _REVERSE.setdefault((_usage, _shift), _ch)

BUTTON_RECT = (1500, 160, 300, 90)  # x, y, w, h in desktop pixels


class SimDesktop:
    def __init__(self, width: int = 1920, height: int = 1080, bars: tuple[int, int] = (0, 0)) -> None:
        self.frame_w, self.frame_h = width, height
        self.bar_x, self.bar_y = bars               # simulated GPU pillarbox/letterbox
        self.desk_w = width - 2 * self.bar_x
        self.desk_h = height - 2 * self.bar_y
        self.lock = threading.Lock()
        self.abs_pos = (P.ABS_RANGE // 2, P.ABS_RANGE // 2)
        self.buttons = 0
        self.text = ""
        self.clicks: list[tuple[int, int, int]] = []     # (x, y, button) in desktop px
        self.button_presses = 0
        self.scroll_total = 0
        self.keys_down: tuple[int, ...] = ()
        self.combos: list[tuple[int, tuple[int, ...]]] = []
        self.version = 0

    def cursor_px(self) -> tuple[int, int]:
        ax, ay = self.abs_pos
        return ax * self.desk_w // P.ABS_RANGE, ay * self.desk_h // P.ABS_RANGE

    # ---- input
    def apply(self, frame: bytes) -> None:
        cmd, data = frame[3], frame[5:5 + frame[4]]
        with self.lock:
            if cmd == P.CMD_KEYBOARD:
                mods, keys = data[0], tuple(k for k in data[2:8] if k)
                new = [k for k in keys if k not in self.keys_down]
                if new:
                    self.combos.append((mods, tuple(new)))
                shift = bool(mods & (P.MOD_LSHIFT | P.MOD_RSHIFT))
                plain = not (mods & ~(P.MOD_LSHIFT | P.MOD_RSHIFT))
                for k in new:
                    if k == 0x2A and plain:
                        self.text = self.text[:-1]
                    elif plain and (k, shift) in _REVERSE:
                        self.text += _REVERSE[(k, shift)]
                self.keys_down = keys
            elif cmd == P.CMD_MOUSE_ABS:
                buttons = data[1]
                x = data[2] | (data[3] << 8)
                y = data[4] | (data[5] << 8)
                wheel = data[6] - 256 if data[6] > 127 else data[6]
                self.abs_pos = (x, y)
                pressed = buttons & ~self.buttons
                if pressed:
                    cx, cy = self.cursor_px()
                    self.clicks.append((cx, cy, pressed))
                    bx, by, bw, bh = BUTTON_RECT
                    if pressed & P.BTN_LEFT and bx <= cx < bx + bw and by <= cy < by + bh:
                        self.button_presses += 1
                self.buttons = buttons
                self.scroll_total += wheel
            self.version += 1

    # ---- output
    def render(self) -> np.ndarray:
        with self.lock:
            frame = np.zeros((self.frame_h, self.frame_w, 3), np.uint8)
            desk = np.zeros((self.desk_h, self.desk_w, 3), np.uint8)
            desk[:] = (110, 80, 40)                                         # desktop blue
            cv2.rectangle(desk, (80, 80), (1400, 900), (245, 245, 245), -1)  # window
            cv2.rectangle(desk, (80, 80), (1400, 130), (60, 60, 60), -1)
            cv2.putText(desk, "Simulated Work laptop - Notepad", (100, 115),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
            y = 180
            for line in (self.text.split("\n")[-20:] or [""]):
                cv2.putText(desk, line, (100, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2, cv2.LINE_AA)
                y += 36
            bx, by, bw, bh = BUTTON_RECT
            colour = (60, 160, 60) if self.button_presses % 2 else (60, 60, 200)
            cv2.rectangle(desk, (bx, by), (bx + bw, by + bh), colour, -1)
            cv2.putText(desk, f"Button x{self.button_presses}", (bx + 20, by + 58),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(desk, f"scroll {self.scroll_total:+d}", (1500, 320),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
            cx, cy = self.cursor_px()
            arrow = np.array([[cx, cy], [cx, cy + 22], [cx + 6, cy + 17], [cx + 10, cy + 26],
                              [cx + 14, cy + 24], [cx + 10, cy + 15], [cx + 17, cy + 15]], np.int32)
            cv2.fillPoly(desk, [arrow], (255, 255, 255))
            cv2.polylines(desk, [arrow], True, (0, 0, 0), 1)
            frame[self.bar_y:self.bar_y + self.desk_h, self.bar_x:self.bar_x + self.desk_w] = desk
            return frame


class SimLink:
    """Fake serial link: applies packets to the SimDesktop and answers GET_INFO."""

    def __init__(self, desktop: SimDesktop, caps_lock: bool = False) -> None:
        self.desktop = desktop
        self.caps_lock = caps_lock
        self.frames: list[bytes] = []

    def write(self, frame: bytes) -> None:
        self.frames.append(frame)
        self.desktop.apply(frame)

    def transact(self, frame: bytes, timeout: float = 0.5) -> bytes | None:
        self.frames.append(frame)
        if frame[3] == P.CMD_GET_INFO:
            return P.info_reply_frame(leds=0x02 if self.caps_lock else 0)
        return None

    def describe(self) -> str:
        return "simulated NanoKVM serial link"

    def close(self) -> None:
        pass


class SimCapture:
    """Renders the SimDesktop into a FrameStore at a fixed rate, like the real capture thread."""

    def __init__(self, desktop: SimDesktop, fps: float = 20.0) -> None:
        self.desktop = desktop
        self.store = FrameStore()
        self._period = 1.0 / fps
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sim-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        cached_version, cached = -1, None
        while not self._stop.is_set():
            v = self.desktop.version
            if v != cached_version or cached is None:
                cached, cached_version = self.desktop.render(), v
            self.store.put(cached.copy())
            self._stop.wait(self._period)

    def latest(self) -> Frame | None:
        return self.store.latest()

    def wait_frame(self, after_seq: int = 0, timeout: float = 3.0) -> Frame | None:
        return self.store.wait_frame(after_seq, timeout)

    def status(self) -> dict:
        f = self.store.latest()
        return {"state": "streaming", "error": None, "device": "simulator", "mode": "simulated",
                "fps": round(self.store.fps, 1),
                "frame_size": None if f is None else [int(f.image.shape[1]), int(f.image.shape[0])],
                "last_frame_age_ms": None if f is None else int((time.monotonic() - f.t) * 1000)}

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
