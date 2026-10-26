"""Byte-exact packets; expected values are the worked examples verified against
sipeed/NanoKVM-USB v1.1.4 and the WCH CH9329 protocol document."""
from nanokvm_mcp import protocol as P


def hx(s: str) -> bytes:
    return bytes.fromhex(s)


def test_press_a():
    assert P.keyboard_frame(0, [0x04]) == hx("57 AB 00 02 08 00 00 04 00 00 00 00 00 10")


def test_release_all_keys():
    assert P.RELEASE_KEYBOARD == hx("57 AB 00 02 08 00 00 00 00 00 00 00 00 0C")


def test_ctrl_alt_delete():
    assert P.keyboard_frame(P.MOD_LCTRL | P.MOD_LALT, [0x4C]) == hx("57 AB 00 02 08 05 00 4C 00 00 00 00 00 5D")


def test_abs_centre_no_buttons():
    assert P.mouse_abs_frame(0, 2048, 2048) == hx("57 AB 00 04 07 02 00 00 08 00 08 00 1F")


def test_left_down_at_centre():
    assert P.mouse_abs_frame(P.BTN_LEFT, 2048, 2048) == hx("57 AB 00 04 07 02 01 00 08 00 08 00 20")


def test_wheel_down_one_notch():
    assert P.mouse_abs_frame(0, 2048, 2048, wheel=-1) == hx("57 AB 00 04 07 02 00 00 08 00 08 FF 1E")


def test_get_info_request():
    assert P.get_info_frame() == hx("57 AB 00 01 00 03")


def test_decode_get_info_reply():
    info = P.decode_info(hx("57 AB 00 81 08 30 01 02 00 00 00 00 00 BE"))
    assert info is not None
    assert info.chip_version == "V1.0"
    assert info.target_connected is True
    assert info.caps_lock is True and info.num_lock is False and info.scroll_lock is False


def test_decode_rejects_other_commands():
    assert P.decode_info(hx("57 AB 00 82 01 00 85")) is None


def test_abs_coordinates_clamped():
    p = P.mouse_abs_payload(0, 5000, -3)
    assert p[2] | (p[3] << 8) == P.ABS_MAX
    assert p[4] | (p[5] << 8) == 0


def test_keyboard_payload_max_six_keys():
    assert P.keyboard_payload(0, list(range(4, 14))) == bytes([0, 0, 4, 5, 6, 7, 8, 9])


def test_relative_payload_signed():
    assert P.mouse_rel_payload(0, -1, 200, -5) == bytes([0x01, 0, 0xFF, 0x7F, 0xFB])


def test_parse_frames_resync_and_split():
    good = P.info_reply_frame(leds=0x02)
    buf = bytearray(b"\x00\x13" + good[:4])
    assert P.parse_frames(buf) == []
    buf += good[4:] + b"\x57\xab\x00\x81\x01\x00\x99" + good   # bad checksum frame in the middle
    frames = P.parse_frames(buf)
    assert frames == [good, good]
    assert buf == bytearray()


def test_parse_frames_keeps_partial_header():
    buf = bytearray(b"\x11\x22\x57")
    assert P.parse_frames(buf) == []
    assert buf == bytearray(b"\x57")


def test_info_reply_roundtrip():
    info = P.decode_info(P.info_reply_frame(target_connected=False, leds=0x05))
    assert info.target_connected is False and info.num_lock and info.scroll_lock and not info.caps_lock
