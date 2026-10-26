"""Keyboard/mouse output to the Work laptop through the NanoKVM-USB serial port.

Safety rules:
  * every public operation ends with all keys up and all buttons up (also on errors); buttons
    are released with a zero-motion relative report so the pointer is not moved;
  * one lock serialises device access; an action that cannot get the lock within
    ``busy_timeout`` fails with HidBusy instead of queueing (a queued action would run later,
    after the caller already gave up);
  * ``release_all`` bumps a generation counter: running actions stop between keystrokes and
    actions that were already waiting are cancelled (HidAborted).
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from typing import Iterator, Protocol

from . import protocol as P
from .keys import Combo, text_to_keystrokes

log = logging.getLogger(__name__)


class HidError(RuntimeError):
    """Keyboard/mouse problem; the message is shown to Claude."""


class HidBusy(HidError):
    pass


class HidAborted(HidError):
    pass


class Link(Protocol):
    def write(self, frame: bytes) -> None: ...
    def transact(self, frame: bytes, timeout: float = 0.5) -> bytes | None: ...
    def describe(self) -> str: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------- serial link
_WINERR_RE = re.compile(r", None, (\d+)\)\s*$")  # tail of pyserial's "...PermissionError(13, 'Access is denied.', None, 5)"


def _winerror(exc: Exception) -> int | None:
    m = _WINERR_RE.search(str(exc))
    return int(m.group(1)) if m else None


def list_serial_ports() -> list[dict]:
    from serial.tools import list_ports
    return [{"device": p.device, "description": p.description, "hwid": p.hwid,
             "vid": p.vid, "pid": p.pid} for p in list_ports.comports()]


def find_serial_port(ids: list[tuple[int, int]]) -> str:
    ports = list_serial_ports()
    hits = [p for p in ports if (p["vid"], p["pid"]) in ids]
    if not hits:
        seen = "; ".join(f"{p['device']} ({p['description']})" for p in ports) or "none"
        want = ", ".join(f"{v:04X}:{p:04X}" for v, p in ids)
        raise HidError(
            f"NanoKVM keyboard/mouse COM port not found (looking for USB VID:PID {want}; ports present: "
            f"{seen}). Check the HOST-end USB-C cable. If the video works but no CH340 COM port "
            "appears, Windows has not installed the WCH CH340 driver yet: open Device Manager, find the "
            "unknown 'USB Serial' device and choose Update driver > Search automatically (Windows Update).")
    if len(hits) > 1:
        raise HidError("several CH340 COM ports found (" + ", ".join(p["device"] for p in hits)
                       + "); set serial_port in nanokvm.toml (e.g. serial_port = \"COM5\").")
    return hits[0]["device"]


class SerialLink:
    def __init__(self, port: str | None, ids: list[tuple[int, int]], baud: int = 57600,
                 video_ids: list[tuple[int, int]] | None = None):
        self._fixed = port
        self._ids = ids
        self._baud = baud
        self._video_ids = video_ids
        self._ser = None
        self._lock = threading.Lock()

    def describe(self) -> str:
        if self._ser is not None:
            return f"{self._ser.port} @ {self._baud} baud"
        return "not open"

    def _pick_port(self) -> str:
        if self._fixed:
            return self._fixed
        # 1A86:7523 is the generic CH340 ID (Arduinos, printers, ...): only auto-select it when the
        # NanoKVM itself is attached, and never guess between several.
        if self._video_ids:
            from .capture import find_capture_device
            if find_capture_device(self._video_ids) is None:
                raise HidError("the NanoKVM is not detected (its video device is missing), so no COM port "
                               "was auto-selected. Plug its HOST-end USB-C into this laptop, or set "
                               "serial_port in nanokvm.toml.")
        return find_serial_port(self._ids)

    def _open(self):
        import serial
        dev = self._pick_port()
        ser = serial.Serial()
        ser.port, ser.baudrate = dev, self._baud      # 8N1, no flow control (pyserial defaults)
        ser.timeout, ser.write_timeout = 0.05, 1.0
        ser.dtr = False                               # don't pulse DTR/RTS (resets Arduino-style boards)
        ser.rts = False
        try:
            ser.open()
        except serial.SerialException as e:
            code = _winerror(e)
            if code == 5:
                raise HidError(
                    f"{dev} is in use by another program. Close the NanoKVM-USB desktop app, any "
                    "usbkvm.sipeed.com browser tab, or serial terminal, then retry.") from e
            if code == 2:
                raise HidError(f"{dev} does not exist (NanoKVM unplugged?)") from e
            raise HidError(f"cannot open {dev}: {e}") from e
        try:   # a previous session may have died mid-keypress: start from a clean state
            ser.write(P.RELEASE_KEYBOARD + P.RELEASE_MOUSE_REL)
        except Exception as e:  # noqa: BLE001
            log.warning("initial release on %s failed: %s", dev, e)
        log.info("serial opened %s @ %d", dev, self._baud)
        return ser

    def _ensure(self):
        if self._ser is None or not self._ser.is_open:
            self._ser = self._open()
        return self._ser

    def _drop(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
        self._ser = None

    def write(self, frame: bytes) -> None:
        import serial
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure().write(frame)
                    return
                except (serial.SerialException, OSError) as e:
                    log.warning("serial write failed (%s), reopening", e)
                    self._drop()
                    if attempt:
                        raise HidError(f"serial link failed: {e} (NanoKVM unplugged?)") from e
                    time.sleep(0.3)

    def transact(self, frame: bytes, timeout: float = 0.5) -> bytes | None:
        import serial
        with self._lock:
            try:
                ser = self._ensure()
                ser.reset_input_buffer()          # discard stale acks
                ser.write(frame)
                buf = bytearray()
                deadline = time.monotonic() + timeout
                ok, err = frame[3] | 0x80, frame[3] | 0xC0
                while time.monotonic() < deadline:
                    buf += ser.read(max(1, ser.in_waiting))
                    for pkt in P.parse_frames(buf):
                        if pkt[3] in (ok, err):
                            return pkt
                return None
            except (serial.SerialException, OSError) as e:
                self._drop()
                raise HidError(f"serial link failed: {e} (NanoKVM unplugged?)") from e

    def close(self) -> None:
        with self._lock:
            self._drop()


# ---------------------------------------------------------------- controller
class Hid:
    def __init__(self, link: Link, key_hold_ms: int = 25, key_gap_ms: int = 15,
                 click_hold_ms: int = 60, busy_timeout: float = 3.0):
        self.link = link
        self.hold = key_hold_ms / 1000.0
        self.gap = key_gap_ms / 1000.0
        self.click_hold = click_hold_ms / 1000.0
        self.busy_timeout = busy_timeout
        self._lock = threading.RLock()
        self._gen = 0                      # bumped by release_all
        self._gen_lock = threading.Lock()
        self._pos = (P.ABS_RANGE // 2, P.ABS_RANGE // 2)
        self._buttons = 0
        self._info: P.DeviceInfo | None = None
        self._info_t = float("-inf")

    # ---- locking / cancellation
    @contextlib.contextmanager
    def _acquire(self, gen: int) -> Iterator[None]:
        if not self._lock.acquire(timeout=self.busy_timeout):
            raise HidBusy("keyboard/mouse busy: another action (for example typing requested by another "
                          "Claude conversation) is still running. Retry in a moment, or call release_all to "
                          "stop it.")
        try:
            if self._gen != gen:
                raise HidAborted("cancelled by release_all before it started")
            yield
        finally:
            self._lock.release()

    def _stopped(self, gen: int) -> bool:
        return self._gen != gen

    # ---- low level
    def _send(self, frame: bytes) -> None:
        self.link.write(frame)

    def _kb(self, mods: int = 0, keys: tuple[int, ...] | list[int] = ()) -> None:
        self._send(P.keyboard_frame(mods, keys))

    def _mouse(self, buttons: int | None = None, pos: tuple[int, int] | None = None, wheel: int = 0) -> None:
        if buttons is not None:
            self._buttons = buttons
        if pos is not None:
            self._pos = pos
        self._send(P.mouse_abs_frame(self._buttons, self._pos[0], self._pos[1], wheel))

    def _release(self) -> None:
        """All keys up, all buttons up, without moving the pointer (best effort; never raises)."""
        try:
            self._kb(0, ())
        except Exception as e:  # noqa: BLE001
            log.warning("keyboard release failed: %s", e)
        try:
            if self._buttons:              # a button we pressed through the absolute interface
                self._mouse(0)             # (same position it was pressed at)
            self._send(P.RELEASE_MOUSE_REL)
        except Exception as e:  # noqa: BLE001
            log.warning("mouse release failed: %s", e)

    @staticmethod
    def _sleep(seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    # ---- public API
    def info(self, max_age: float = 2.0) -> P.DeviceInfo | None:
        """GET_INFO (cached). A device that does not answer is remembered for 30 s."""
        with self._lock:
            age = time.monotonic() - self._info_t
            if age < (max_age if self._info is not None else 30.0):
                return self._info
            pkt = self.link.transact(P.get_info_frame(), timeout=0.5)
            self._info = P.decode_info(pkt) if pkt else None
            self._info_t = time.monotonic()
            return self._info

    def info_nowait(self, max_age: float = 5.0) -> tuple[P.DeviceInfo | None, bool]:
        """(info, busy). Never waits for a running action: returns the cached value when busy."""
        if not self._lock.acquire(blocking=False):
            return self._info, True
        try:
            return self.info(max_age), False
        finally:
            self._lock.release()

    def release_all(self) -> None:
        with self._gen_lock:
            self._gen += 1                 # running actions stop between keystrokes
        if not self._lock.acquire(timeout=10.0):
            raise HidBusy("could not get the keyboard/mouse within 10 s to release keys")
        try:
            self._release()
        finally:
            self._lock.release()

    def press(self, combos: list[Combo], repeat: int = 1) -> int:
        """Tap each combo in order, ``repeat`` times. Returns the number of combos sent."""
        gen = self._gen
        sent = 0
        with self._acquire(gen):
            try:
                for _ in range(max(1, repeat)):
                    for c in combos:
                        if self._stopped(gen):
                            return sent
                        if c.modifiers and c.keys:
                            self._kb(c.modifiers, ())       # modifiers first, like a human
                            self._sleep(self.gap)
                        self._kb(c.modifiers, c.keys)
                        self._sleep(self.hold if c.keys else self.hold * 2)
                        if c.modifiers and c.keys:
                            self._kb(c.modifiers, ())       # key up, modifiers still down
                            self._sleep(self.gap)
                        self._kb(0, ())
                        self._sleep(self.gap)
                        sent += 1
            finally:
                self._release()
        return sent

    def type_text(self, text: str, layout: str = "us") -> tuple[int, list[str], bool]:
        """Type text for the target's keyboard layout. Returns (typed, skipped_chars, stopped_by_release_all)."""
        gen = self._gen
        typed = 0
        with self._acquire(gen):
            info = None
            try:
                info = self.info()
            except HidError:
                pass
            ks = text_to_keystrokes(text, caps_lock=bool(info and info.caps_lock), layout=layout)
            try:
                prev_usage = None
                for mods, usage in ks.strokes:
                    if self._stopped(gen):
                        return typed, ks.skipped, True
                    if mods:
                        self._kb(mods, ())
                        self._sleep(self.gap / 2)
                    self._kb(mods, (usage,))
                    self._sleep(self.hold)
                    self._kb(0, ())
                    # same key twice needs a clear gap so the target sees two presses
                    self._sleep(self.gap * (2 if usage == prev_usage else 1))
                    prev_usage = usage
                    typed += 1
            finally:
                self._release()
        return typed, ks.skipped, False

    def move(self, ax: int, ay: int) -> None:
        gen = self._gen
        with self._acquire(gen):
            try:
                self._mouse(0, (ax, ay))
            finally:
                self._release()

    def click(self, ax: int, ay: int, button: str = "left", count: int = 1, modifiers: int = 0) -> None:
        bit = P.BUTTONS.get(button)
        if bit is None:
            raise HidError(f"unknown mouse button {button!r} (use left, right or middle)")
        gen = self._gen
        with self._acquire(gen):
            try:
                self._mouse(0, (ax, ay))
                self._sleep(0.03)                   # let hover state settle
                if modifiers:
                    self._kb(modifiers, ())
                    self._sleep(self.gap)
                for i in range(max(1, count)):
                    if self._stopped(gen):
                        raise HidAborted("click stopped by release_all")
                    self._mouse(bit)
                    self._sleep(self.click_hold)
                    self._mouse(0)
                    if i + 1 < count:
                        self._sleep(0.08)           # well inside Windows' 500 ms double-click time
            finally:
                self._release()

    def drag(self, start: tuple[int, int], end: tuple[int, int], button: str = "left",
             steps: int = 20, step_ms: int = 15) -> None:
        bit = P.BUTTONS.get(button)
        if bit is None:
            raise HidError(f"unknown mouse button {button!r} (use left, right or middle)")
        gen = self._gen
        with self._acquire(gen):
            try:
                self._mouse(0, start)
                self._sleep(0.05)
                if self._stopped(gen):
                    raise HidAborted("drag stopped by release_all")
                self._mouse(bit)
                self._sleep(0.1)
                for i in range(1, steps + 1):
                    if self._stopped(gen):
                        raise HidAborted("drag stopped by release_all")
                    t = i / steps
                    self._mouse(pos=(round(start[0] + (end[0] - start[0]) * t),
                                     round(start[1] + (end[1] - start[1]) * t)))
                    self._sleep(step_ms / 1000.0)
                self._sleep(0.1)
                self._mouse(0)
            finally:
                self._release()

    def scroll(self, ax: int, ay: int, notches: int, horizontal: bool = False) -> None:
        """Positive notches scroll up (or left when horizontal, which uses Shift+wheel)."""
        gen = self._gen
        with self._acquire(gen):
            try:
                self._mouse(0, (ax, ay))
                self._sleep(0.03)
                if horizontal:
                    self._kb(P.MOD_LSHIFT, ())
                    self._sleep(self.gap)
                step = 1 if notches > 0 else -1
                for _ in range(abs(notches)):
                    if self._stopped(gen):
                        raise HidAborted("scroll stopped by release_all")
                    self._mouse(wheel=step)
                    self._sleep(0.03)
            finally:
                self._release()

    def close(self) -> None:
        try:
            self.release_all()
        except HidError as e:
            log.warning("release on close failed: %s", e)
        finally:
            self.link.close()
