"""The USB KVM's keyboard/mouse serial protocol (CH9329-compatible). hid.py sends these frames over the serial
link, and the simulator decodes them.

Verified against sipeed/NanoKVM-USB v1.1.4 (browser/src/libs/device/proto.ts, libs/mouse/index.ts)
and the WCH "CH9329 serial communication protocol V1.0":

    frame = 57 AB | ADDR(00) | CMD | LEN | DATA[LEN] | SUM      SUM = sum(all previous bytes) & 0xFF
    serial: 57600 8N1, no flow control (the official apps; the bare chip defaults to 9600)

Replies (per the WCH doc) are CMD|0x80 on success or CMD|0xC0 on error. The official apps never
read replies for keyboard/mouse commands, and neither do we; only GET_INFO's reply is parsed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

HEAD = b"\x57\xab"
ADDR = 0x00

CMD_GET_INFO = 0x01
CMD_KEYBOARD = 0x02
CMD_MEDIA = 0x03
CMD_MOUSE_ABS = 0x04
CMD_MOUSE_REL = 0x05

# Absolute mouse: 4096 x 4096 grid, valid values 0..4095 (WCH formula 4096 * x / X_MAX).
ABS_RANGE = 4096
ABS_MAX = ABS_RANGE - 1

# Mouse button bits (byte 1 of mouse reports).
BTN_LEFT = 0x01
BTN_RIGHT = 0x02
BTN_MIDDLE = 0x04
BUTTONS = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}

# Keyboard modifier bits (byte 0 of the keyboard report).
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LWIN = 0x08
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RWIN = 0x80


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def build_frame(cmd: int, payload: bytes = b"", addr: int = ADDR) -> bytes:
    """One frame: header, address, command, length, payload, checksum."""
    if len(payload) > 255:
        raise ValueError("payload too long")
    body = HEAD + bytes([addr & 0xFF, cmd & 0xFF, len(payload)]) + bytes(payload)
    return body + bytes([checksum(body)])


def parse_frames(buf: bytearray) -> list[bytes]:
    """Extract complete, checksum-valid frames from ``buf`` (consumed in place); resyncs on 57 AB."""
    out: list[bytes] = []
    while True:
        i = buf.find(HEAD)
        if i < 0:
            # keep a trailing 0x57 that may be the first half of the next header
            keep = buf[-1:] if buf[-1:] == HEAD[:1] else b""
            buf[:] = keep
            return out
        if i:
            del buf[:i]
        if len(buf) < 5:
            return out
        total = 5 + buf[4] + 1
        if len(buf) < total:
            return out
        pkt = bytes(buf[:total])
        if checksum(pkt[:-1]) == pkt[-1]:
            out.append(pkt)
            del buf[:total]
        else:
            del buf[:2]  # bad checksum: drop this header and resync


# ---------------------------------------------------------------- report payloads
def keyboard_payload(modifiers: int = 0, keys: Sequence[int] = ()) -> bytes:
    """8-byte boot-keyboard report: [modifiers, 0x00, k1..k6]. Extra keys beyond 6 are dropped."""
    ks = [k & 0xFF for k in keys][:6]
    return bytes([modifiers & 0xFF, 0x00, *ks, *([0x00] * (6 - len(ks)))])


def mouse_abs_payload(buttons: int, x: int, y: int, wheel: int = 0) -> bytes:
    """[0x02, buttons, Xlo, Xhi, Ylo, Yhi, wheel]; x/y clamped to 0..4095, wheel +1 = up, -1 = down."""
    x = max(0, min(ABS_MAX, int(x)))
    y = max(0, min(ABS_MAX, int(y)))
    w = max(-127, min(127, int(wheel))) & 0xFF
    return bytes([0x02, buttons & 0xFF, x & 0xFF, x >> 8, y & 0xFF, y >> 8, w])


def mouse_rel_payload(buttons: int, dx: int, dy: int, wheel: int = 0) -> bytes:
    """[0x01, buttons, dx, dy, wheel] as signed bytes clamped to +-127 (positive dy = down)."""
    def s8(v: int) -> int:
        return max(-127, min(127, int(v))) & 0xFF
    return bytes([0x01, buttons & 0xFF, s8(dx), s8(dy), s8(wheel)])


def keyboard_frame(modifiers: int = 0, keys: Sequence[int] = ()) -> bytes:
    return build_frame(CMD_KEYBOARD, keyboard_payload(modifiers, keys))


def mouse_abs_frame(buttons: int, x: int, y: int, wheel: int = 0) -> bytes:
    return build_frame(CMD_MOUSE_ABS, mouse_abs_payload(buttons, x, y, wheel))


def mouse_rel_frame(buttons: int, dx: int, dy: int, wheel: int = 0) -> bytes:
    return build_frame(CMD_MOUSE_REL, mouse_rel_payload(buttons, dx, dy, wheel))


def get_info_frame() -> bytes:
    return build_frame(CMD_GET_INFO)


RELEASE_KEYBOARD = keyboard_frame(0, ())
# Zero-motion relative report: releases buttons WITHOUT moving the pointer (an absolute report
# always carries a position and would yank the pointer).
RELEASE_MOUSE_REL = mouse_rel_frame(0, 0, 0)


# ---------------------------------------------------------------- GET_INFO reply
@dataclass(frozen=True)
class DeviceInfo:
    """The chip's GET_INFO reply: its version, whether the locked computer enumerated it, and the lock lights that
    the locked computer's operating system reports to it."""
    chip_version: str
    target_connected: bool
    num_lock: bool
    caps_lock: bool
    scroll_lock: bool
    raw: str

    def as_dict(self) -> dict:
        return {
            "chip_version": self.chip_version,
            "target_usb_connected": self.target_connected,
            "num_lock": self.num_lock,
            "caps_lock": self.caps_lock,
            "scroll_lock": self.scroll_lock,
        }


def decode_info(pkt: bytes) -> DeviceInfo | None:
    """Decode a GET_INFO reply (CMD 0x81): data[0] version (0x30 = V1.0), data[1] target USB
    enumerated, data[2] LED bits (bit0 Num, bit1 Caps, bit2 Scroll)."""
    if len(pkt) < 6 or pkt[:2] != HEAD or pkt[3] != (CMD_GET_INFO | 0x80):
        return None
    n = pkt[4]
    data = pkt[5:5 + n]
    if n < 3 or len(data) < 3 or data[0] < 0x30:
        return None
    leds = data[2]
    return DeviceInfo(
        chip_version=f"V{1.0 + (data[0] - 0x30) / 10:.1f}",
        target_connected=data[1] != 0,
        num_lock=bool(leds & 0x01),
        caps_lock=bool(leds & 0x02),
        scroll_lock=bool(leds & 0x04),
        raw=pkt.hex(" "),
    )


def info_reply_frame(version: int = 0x30, target_connected: bool = True, leds: int = 0) -> bytes:
    """Build a GET_INFO reply (used by the simulator and tests)."""
    return build_frame(CMD_GET_INFO | 0x80, bytes([version, int(target_connected), leds, 0, 0, 0, 0, 0]))
