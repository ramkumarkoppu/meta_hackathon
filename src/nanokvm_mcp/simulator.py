"""A simulated locked computer: synthetic HDMI frames plus a fake serial link that reacts to the
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
from .keys import CHARS, KEYS

# HID usage (+shift) -> character, inverted from the US typing table.
_REVERSE: dict[tuple[int, bool], str] = {}
for _ch, (_usage, _shift) in CHARS.items():
    _REVERSE.setdefault((_usage, _shift), _ch)

BUTTON_RECT = (1500, 160, 300, 90)  # x, y, w, h in desktop pixels
_CTRL = P.MOD_LCTRL | P.MOD_RCTRL
_SHIFT = P.MOD_LSHIFT | P.MOD_RSHIFT


class SimEditor:
    """A tiny stand-in for VS Code, for the edit tests: numbered lines, a cursor, Ctrl+G (Go to Line, cursor at
    column 1), Ctrl+L (expand line selection), Ctrl+S (save), Backspace, Escape, Enter and typing (US layout), a
    status bar like VS Code's ("Ln 25, Col 1 (61 selected)") and the file's tab, with a dot while there are
    unsaved changes. Typing replaces a selection; auto-indent and auto-closing are off, as README section 7
    asks."""

    VISIBLE = 30                                   # rows on the screen

    def __init__(self, lines: list[str], name: str = "main.cpp") -> None:
        self.lines = list(lines) or [""]
        self.name = name
        self.dirty = False                         # unsaved changes
        self.saves = 0
        self.row, self.col = 0, 0                  # cursor, 0-based
        self.anchor: tuple[int, int] | None = None # selection start
        self.goto: str | None = None               # digits in the Go to Line box while it is open
        self.top = 0                               # first row on the screen

    # ---- what the screen shows
    def selection(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        if self.anchor is None or self.anchor == (self.row, self.col):
            return None
        a, b = sorted([self.anchor, (self.row, self.col)])
        return a, b

    def selected_chars(self) -> int:
        s = self.selection()
        if s is None:
            return 0
        (r1, c1), (r2, c2) = s
        if r1 == r2:
            return c2 - c1
        return len(self.lines[r1]) - c1 + 1 + sum(len(self.lines[r]) + 1 for r in range(r1 + 1, r2)) + c2

    def status(self) -> str:
        n = self.selected_chars()
        return f"Ln {self.row + 1}, Col {self.col + 1}" + (f" ({n} selected)" if n else "")

    def visible(self) -> list[tuple[int, str]]:
        return [(r + 1, self.lines[r]) for r in range(self.top, min(len(self.lines), self.top + self.VISIBLE))]

    def transcription(self) -> str:
        return "\n".join(f"{n} {line}" for n, line in self.visible())

    def box_text(self) -> str:
        """The quick-input box as Glimmer would read it: Go to Line opens with ':' typed."""
        if self.goto is None:
            return "NONE"
        return f":{self.goto}\nType a line number between 1 and {len(self.lines)} to navigate to."

    def tab_text(self) -> str:
        """The selected tab as Glimmer would read it: the file name, then whether it shows the unsaved-changes dot."""
        return f"{self.name}\n{'UNSAVED' if self.dirty else 'SAVED'}"

    # ---- input
    def key(self, mods: int, usage: int) -> None:
        ctrl, shift = bool(mods & _CTRL), bool(mods & _SHIFT)
        ch = _REVERSE.get((usage, shift))
        if self.goto is not None:                  # the Go to Line box has the keyboard
            if usage == KEYS["enter"]:
                if self.goto:
                    self.row, self.col = min(int(self.goto), len(self.lines)) - 1, 0
                    self.anchor = None
                    self._reveal(centre=True)
                self.goto = None
            elif usage == KEYS["esc"]:
                self.goto = None
            elif ctrl and usage == KEYS["g"]:
                self.goto = ""                     # VS Code selects the old digits: new ones replace them
            elif usage == KEYS["backspace"]:
                self.goto = self.goto[:-1]
            elif not ctrl and ch is not None and ch.isdigit():
                self.goto += ch
            return
        if ctrl:
            if usage == KEYS["g"]:
                self.goto = ""
            elif usage == KEYS["l"]:
                self._expand_line()
            elif usage == KEYS["s"]:
                self.dirty = False
                self.saves += 1
            return
        if usage == KEYS["esc"]:
            self.anchor = None
        elif usage == KEYS["backspace"]:
            self._backspace()
        elif usage == KEYS["enter"]:
            self._insert("\n")
        elif ch is not None and ch not in "\n\t":
            self._insert(ch)

    def _reveal(self, centre: bool = False) -> None:
        if centre:                                 # Go to Line always centres the line, as VS Code does
            self.top = max(0, self.row - self.VISIBLE // 2)
        elif not self.top <= self.row < self.top + self.VISIBLE:
            self.top = max(0, self.row - (self.VISIBLE - 1))

    def _expand_line(self) -> None:
        if self.selection() is None:
            self.anchor = (self.row, 0)
        if self.row + 1 < len(self.lines):
            self.row, self.col = self.row + 1, 0
        else:
            self.col = len(self.lines[self.row])
        self._reveal()

    def _delete_selection(self) -> bool:
        s = self.selection()
        self.anchor = None
        if s is None:
            return False
        (r1, c1), (r2, c2) = s
        self.lines[r1:r2 + 1] = [self.lines[r1][:c1] + self.lines[r2][c2:]]
        self.row, self.col = r1, c1
        self.dirty = True
        return True

    def _insert(self, text: str) -> None:
        self._delete_selection()
        self.dirty = True
        line = self.lines[self.row]
        parts = text.split("\n")
        if len(parts) == 1:
            self.lines[self.row] = line[:self.col] + text + line[self.col:]
            self.col += len(text)
        else:
            new = [line[:self.col] + parts[0], *parts[1:-1], parts[-1] + line[self.col:]]
            self.lines[self.row:self.row + 1] = new
            self.row, self.col = self.row + len(parts) - 1, len(parts[-1])
        self._reveal()

    def _backspace(self) -> None:
        if self._delete_selection():
            return
        if self.col:
            line = self.lines[self.row]
            self.lines[self.row] = line[:self.col - 1] + line[self.col:]
            self.col -= 1
            self.dirty = True
        elif self.row:
            prev = self.lines[self.row - 1]
            self.lines[self.row - 1:self.row + 1] = [prev + self.lines[self.row]]
            self.row, self.col = self.row - 1, len(prev)
            self.dirty = True
        self._reveal()


class SimDesktop:
    def __init__(self, width: int = 1920, height: int = 1080, bars: tuple[int, int] = (0, 0),
                 editor_lines: list[str] | None = None) -> None:
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
        self.locks = {0x53: False, 0x39: False, 0x47: False}   # Num, Caps and Scroll Lock, as the OS would set them
        self.version = 0
        self.editor = SimEditor(editor_lines) if editor_lines is not None else None   # VS Code instead of Notepad

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
                    if k in self.locks:                     # the OS toggles the lock and reports its light back
                        self.locks[k] = not self.locks[k]
                    if self.editor is not None:
                        self.editor.key(mods, k)
                    elif k == 0x2A and plain:
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
            title = "Simulated locked computer - " + ("VS Code" if self.editor else "Notepad")
            cv2.putText(desk, title, (100, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
            if self.editor is None:
                y = 180
                for line in (self.text.split("\n")[-20:] or [""]):
                    cv2.putText(desk, line, (100, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2, cv2.LINE_AA)
                    y += 36
            else:
                ed = self.editor
                cv2.putText(desk, ed.name, (1100, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
                if ed.dirty:                                                         # the tab's unsaved-changes dot
                    cv2.circle(desk, (1100 + 14 * len(ed.name) + 16, 108), 6, (255, 255, 255), -1)
                y = 160
                for n, line in ed.visible():
                    cv2.putText(desk, f"{n:>4}", (90, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (140, 140, 140), 1, cv2.LINE_AA)
                    cv2.putText(desk, line, (160, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 1, cv2.LINE_AA)
                    y += 24
                cv2.rectangle(desk, (80, 870), (1400, 900), (200, 120, 0), -1)        # status bar
                cv2.putText(desk, ed.status(), (1000, 892), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
                            cv2.LINE_AA)
                if ed.goto is not None:                                              # the Go to Line box
                    cv2.rectangle(desk, (440, 135), (1040, 205), (60, 60, 60), -1)
                    for i, line in enumerate(ed.box_text().splitlines()):
                        cv2.putText(desk, line, (455, 162 + 30 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                    (255, 255, 255), 1, cv2.LINE_AA)
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
        desktop.locks[0x39] = caps_lock
        self.frames: list[bytes] = []

    def write(self, frame: bytes) -> None:
        self.frames.append(frame)
        self.desktop.apply(frame)

    def transact(self, frame: bytes, timeout: float = 0.5) -> bytes | None:
        self.frames.append(frame)
        if frame[3] == P.CMD_GET_INFO:
            locks = self.desktop.locks
            return P.info_reply_frame(leds=(0x01 if locks[0x53] else 0) | (0x02 if locks[0x39] else 0)
                                      | (0x04 if locks[0x47] else 0))
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
