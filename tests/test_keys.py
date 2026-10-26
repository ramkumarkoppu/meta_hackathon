import pytest

from nanokvm_mcp import protocol as P
from nanokvm_mcp.keys import KeyError_, parse_combo, parse_sequence, text_to_keystrokes


@pytest.mark.parametrize("text,mods,keys", [
    ("ctrl+alt+delete", P.MOD_LCTRL | P.MOD_LALT, (0x4C,)),
    ("Ctrl+Alt+Del", P.MOD_LCTRL | P.MOD_LALT, (0x4C,)),
    ("win+r", P.MOD_LWIN, (0x15,)),
    ("ctrl+shift+esc", P.MOD_LCTRL | P.MOD_LSHIFT, (0x29,)),
    ("alt+tab", P.MOD_LALT, (0x2B,)),
    ("shift+f10", P.MOD_LSHIFT, (0x43,)),
    ("f24", 0, (0x73,)),
    ("enter", 0, (0x28,)),
    ("win", P.MOD_LWIN, ()),
    ("ctrl++", P.MOD_LCTRL | P.MOD_LSHIFT, (0x2E,)),   # '+' = shift+'='
    ("+", P.MOD_LSHIFT, (0x2E,)),
    ("ctrl+/", P.MOD_LCTRL, (0x38,)),
    ("?", P.MOD_LSHIFT, (0x38,)),
    ("altgr+e", P.MOD_RALT, (0x08,)),
    ("pagedown", 0, (0x4E,)),
    ("num5", 0, (0x5D,)),
])
def test_parse_combo(text, mods, keys):
    c = parse_combo(text)
    assert (c.modifiers, c.keys) == (mods, keys)


def test_parse_sequence():
    seq = parse_sequence("ctrl+a  backspace\tdown")
    assert [(c.modifiers, c.keys) for c in seq] == [(P.MOD_LCTRL, (0x04,)), (0, (0x2A,)), (0, (0x51,))]


@pytest.mark.parametrize("bad", ["ctrl+", "ctrl+bogus", "", "hyper+x", "a+b+c+d+e+f+g"])
def test_parse_errors_are_helpful(bad):
    with pytest.raises(KeyError_) as ei:
        parse_sequence(bad) if not bad else parse_sequence(bad)
    assert "key" in str(ei.value).lower()


def test_text_to_keystrokes_basic():
    ks = text_to_keystrokes("Hi!\r\n")
    assert ks.strokes == [(P.MOD_LSHIFT, 0x0B), (0, 0x0C), (P.MOD_LSHIFT, 0x1E), (0, 0x28)]
    assert ks.skipped == []


def test_text_to_keystrokes_all_printable_ascii():
    text = "".join(chr(c) for c in range(32, 127)) + "\t\n"
    ks = text_to_keystrokes(text)
    assert ks.skipped == []
    assert len(ks.strokes) == len(text)


def test_text_skips_non_ascii():
    ks = text_to_keystrokes("café ✓")
    assert ks.skipped == ["é", "✓"]
    assert len(ks.strokes) == 4


def test_uk_layout_symbols():
    ks = text_to_keystrokes('"@#~\\|£€x', layout="uk")
    assert ks.skipped == []
    assert ks.strokes == [
        (P.MOD_LSHIFT, 0x1F),   # " = Shift+2
        (P.MOD_LSHIFT, 0x34),   # @ = Shift+'
        (0, 0x32),              # # = ISO key left of Enter
        (P.MOD_LSHIFT, 0x32),   # ~
        (0, 0x64),              # \ = ISO key left of Z
        (P.MOD_LSHIFT, 0x64),   # |
        (P.MOD_LSHIFT, 0x20),   # £ = Shift+3
        (P.MOD_RALT, 0x21),     # € = AltGr+4
        (0, 0x1B),              # x unchanged
    ]


def test_us_layout_symbols_unchanged_and_pound_skipped():
    ks = text_to_keystrokes('"@#£', layout="us")
    assert ks.strokes == [(P.MOD_LSHIFT, 0x34), (P.MOD_LSHIFT, 0x1F), (P.MOD_LSHIFT, 0x20)]
    assert ks.skipped == ["£"]


def test_uk_combos_use_layout_for_punctuation():
    assert parse_combo("ctrl+#", "uk").keys == (0x32,)
    assert parse_combo("ctrl+\\", "uk").keys == (0x64,)
    assert parse_combo("ctrl+\\", "us").keys == (0x31,)
    assert parse_combo("win+r", "uk").keys == (0x15,)


def test_unknown_layout_is_reported():
    with pytest.raises(KeyError_, match="keyboard_layout"):
        text_to_keystrokes("x", layout="fr")


def test_caps_lock_compensation():
    ks = text_to_keystrokes("aB1", caps_lock=True)
    assert ks.strokes == [(P.MOD_LSHIFT, 0x04), (0, 0x05), (0, 0x1E)]
