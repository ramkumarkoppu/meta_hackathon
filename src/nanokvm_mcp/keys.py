"""Key names -> HID usages, combo parsing ("ctrl+alt+delete") and text -> keystrokes.

HID usages are the keyboard page (0x07) of the USB HID Usage Tables, checked against Sipeed's
NanoKVM-USB app (v1.1.4). Typing text follows the ``keyboard_layout`` setting ("us" or "uk"; HID
usages are physical key positions; the target's layout decides which character a key produces).

The service turns /api/act's keys and type requests into these strokes for hid.py. acting.py and editing.py
check a step's keys and an edit's text with the same tables, so what is checked is what gets sent.
"""
from __future__ import annotations

import string
from dataclasses import dataclass

from .protocol import (MOD_LALT, MOD_LCTRL, MOD_LSHIFT, MOD_LWIN, MOD_RALT, MOD_RCTRL,
                       MOD_RSHIFT, MOD_RWIN)

MODIFIERS: dict[str, int] = {
    "ctrl": MOD_LCTRL, "control": MOD_LCTRL, "lctrl": MOD_LCTRL,
    "shift": MOD_LSHIFT, "lshift": MOD_LSHIFT,
    "alt": MOD_LALT, "lalt": MOD_LALT, "option": MOD_LALT,
    "win": MOD_LWIN, "windows": MOD_LWIN, "lwin": MOD_LWIN, "meta": MOD_LWIN,
    "super": MOD_LWIN, "cmd": MOD_LWIN, "command": MOD_LWIN, "start": MOD_LWIN,
    "rctrl": MOD_RCTRL, "rshift": MOD_RSHIFT,
    "ralt": MOD_RALT, "altgr": MOD_RALT,
    "rwin": MOD_RWIN,
}

KEYS: dict[str, int] = {
    "enter": 0x28, "return": 0x28,
    "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A, "bksp": 0x2A,
    "tab": 0x2B,
    "space": 0x2C, "spacebar": 0x2C,
    "minus": 0x2D, "-": 0x2D,
    "equal": 0x2E, "equals": 0x2E, "=": 0x2E,
    "[": 0x2F, "leftbracket": 0x2F, "bracketleft": 0x2F,
    "]": 0x30, "rightbracket": 0x30, "bracketright": 0x30,
    "\\": 0x31, "backslash": 0x31,
    ";": 0x33, "semicolon": 0x33,
    "'": 0x34, "quote": 0x34, "apostrophe": 0x34,
    "`": 0x35, "backquote": 0x35, "backtick": 0x35, "grave": 0x35,
    ",": 0x36, "comma": 0x36,
    ".": 0x37, "period": 0x37, "dot": 0x37,
    "/": 0x38, "slash": 0x38,
    "capslock": 0x39, "caps": 0x39,
    "printscreen": 0x46, "prtsc": 0x46, "prtscn": 0x46, "print": 0x46,
    "scrolllock": 0x47,
    "pause": 0x48, "break": 0x48,
    "insert": 0x49, "ins": 0x49,
    "home": 0x4A,
    "pageup": 0x4B, "pgup": 0x4B,
    "delete": 0x4C, "del": 0x4C,
    "end": 0x4D,
    "pagedown": 0x4E, "pgdn": 0x4E,
    "right": 0x4F, "arrowright": 0x4F,
    "left": 0x50, "arrowleft": 0x50,
    "down": 0x51, "arrowdown": 0x51,
    "up": 0x52, "arrowup": 0x52,
    "numlock": 0x53,
    "num/": 0x54, "numdivide": 0x54,
    "num*": 0x55, "nummultiply": 0x55,
    "num-": 0x56, "numsubtract": 0x56,
    "num+": 0x57, "numadd": 0x57,
    "numenter": 0x58,
    "num.": 0x63, "numdecimal": 0x63,
    "menu": 0x65, "contextmenu": 0x65, "apps": 0x65, "application": 0x65,
}
KEYS.update({c: 0x04 + i for i, c in enumerate(string.ascii_lowercase)})        # a..z
KEYS.update({str(d): 0x1E + (d - 1) for d in range(1, 10)})                     # 1..9
KEYS["0"] = 0x27
KEYS.update({f"f{n}": 0x3A + (n - 1) for n in range(1, 13)})                    # F1..F12
KEYS.update({f"f{n}": 0x68 + (n - 13) for n in range(13, 25)})                  # F13..F24
KEYS.update({f"num{d}": 0x59 + (d - 1) for d in range(1, 10)})                  # keypad 1..9
KEYS["num0"] = 0x62

# Printable ASCII -> (usage, needs_shift) for a US layout.
_SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
    '"': "'", "~": "`", "<": ",", ">": ".", "?": "/",
}
CHARS: dict[str, tuple[int, bool]] = {}
for _c in string.ascii_lowercase:
    CHARS[_c] = (KEYS[_c], False)
    CHARS[_c.upper()] = (KEYS[_c], True)
for _c in string.digits + "-=[]\\;'`,./":
    CHARS[_c] = (KEYS[_c], False)
for _c, _base in _SHIFTED.items():
    CHARS[_c] = (KEYS[_base], True)
CHARS[" "] = (KEYS["space"], False)
CHARS["\n"] = (KEYS["enter"], False)
CHARS["\t"] = (KEYS["tab"], False)

# ISO keys that exist on UK/European keyboards (HID page 0x07).
KEYS["nonushash"] = 0x32      # UK: '#' and '~' (left of Enter)
KEYS["intlbackslash"] = 0x64  # UK: '\' and '|' (left of Z)

# Character -> (usage, modifiers) per keyboard layout set on the locked computer. These are PC layouts
# (Linux's English (UK) matches "uk"); a Mac's British layout differs, so a Mac uses its U.S. layout and "us".
LAYOUTS: dict[str, dict[str, tuple[int, int]]] = {
    "us": {ch: (usage, MOD_LSHIFT if shift else 0) for ch, (usage, shift) in CHARS.items()},
}
LAYOUTS["uk"] = {
    **LAYOUTS["us"],
    '"': (0x1F, MOD_LSHIFT),      # Shift+2
    "@": (0x34, MOD_LSHIFT),      # Shift+'
    "#": (0x32, 0),               # ISO key left of Enter
    "~": (0x32, MOD_LSHIFT),
    "\\": (0x64, 0),              # ISO key left of Z
    "|": (0x64, MOD_LSHIFT),
    "£": (0x20, MOD_LSHIFT),      # Shift+3
    "¬": (0x35, MOD_LSHIFT),      # Shift+`
    "€": (0x21, MOD_RALT),        # AltGr+4
}


def layout_table(layout: str) -> dict[str, tuple[int, int]]:
    """The character table for ``layout``. An unknown layout raises KeyError_."""
    table = LAYOUTS.get((layout or "us").strip().lower())
    if table is None:
        raise KeyError_(f"unknown keyboard_layout {layout!r} in nanokvm.toml; use one of: "
                        + ", ".join(sorted(LAYOUTS)))
    return table


class KeyError_(ValueError):
    """Unknown key name or malformed combo (the message goes back to the caller)."""


@dataclass(frozen=True)
class Combo:
    """One chord: the modifier bits, up to six key usages, and the text it was parsed from."""
    modifiers: int
    keys: tuple[int, ...]
    text: str

    def __str__(self) -> str:
        return self.text


def _key_names_hint() -> str:
    return ("Use names like: ctrl, shift, alt (option), win (cmd, super), enter, esc, tab, space, "
            "backspace, delete, home, end, pageup, pagedown, up, down, left, right, f1-f24, a-z, 0-9, "
            "or a single printable character such as '/' or '?'.")


def parse_combo(token: str, layout: str = "us") -> Combo:
    """'ctrl+shift+esc' -> Combo. A lone '+' or a trailing '++' means the '+' key.
    Single characters ('/', '#', '@') are looked up in the target's keyboard layout."""
    table = layout_table(layout)
    raw = token.strip()
    if not raw:
        raise KeyError_("empty key combo")
    if raw == "+":
        parts = ["+"]
    elif raw.endswith("++"):
        parts = [p for p in raw[:-2].split("+")] + ["+"]
    else:
        parts = raw.split("+")
    mods = 0
    keys: list[int] = []
    for part in parts:
        name = part.strip()
        low = name.lower()
        if not name:
            raise KeyError_(f"malformed key combo {token!r}. " + _key_names_hint())
        if low in MODIFIERS:
            mods |= MODIFIERS[low]
        elif len(name) == 1 and name in table and not name.isalnum():
            usage, cmods = table[name]                 # punctuation: depends on the layout
            keys.append(usage)
            mods |= cmods
        elif low in KEYS:
            keys.append(KEYS[low])
        elif len(name) == 1 and name in table:
            usage, cmods = table[name]
            keys.append(usage)
            mods |= cmods
        else:
            raise KeyError_(f"unknown key {name!r} in {token!r}. " + _key_names_hint())
    if len(keys) > 6:
        raise KeyError_(f"too many keys in one combo {token!r} (max 6 plus modifiers)")
    return Combo(mods, tuple(keys), raw)


def parse_sequence(keys: str, layout: str = "us") -> list[Combo]:
    """Whitespace-separated combos: 'ctrl+a backspace' -> [ctrl+a, backspace]."""
    tokens = keys.split()
    if not tokens:
        raise KeyError_("no keys given. " + _key_names_hint())
    return [parse_combo(t, layout) for t in tokens]


@dataclass
class Keystrokes:
    """The strokes for some text, and the characters the layout can't type."""
    strokes: list[tuple[int, int]]   # (modifiers, usage)
    skipped: list[str]               # characters that cannot be typed (non-ASCII etc.)


def text_to_keystrokes(text: str, caps_lock: bool = False, layout: str = "us") -> Keystrokes:
    """Convert text to (modifiers, usage) strokes for the target's keyboard layout. CRLF/CR become
    Enter. When ``caps_lock`` is known to be on, Shift is inverted for letters so case comes out right."""
    table = layout_table(layout)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    strokes: list[tuple[int, int]] = []
    skipped: list[str] = []
    for ch in text:
        hit = table.get(ch)
        if hit is None:
            skipped.append(ch)
            continue
        usage, mods = hit
        if caps_lock and ch.isascii() and ch.isalpha():
            mods ^= MOD_LSHIFT
        strokes.append((mods, usage))
    return Keystrokes(strokes, skipped)
