import threading
import time

import pytest

from nanokvm_mcp import protocol as P
from nanokvm_mcp.hid import Hid, HidAborted, HidBusy, HidError
from nanokvm_mcp.keys import parse_sequence


class RecordingLink:
    def __init__(self, fail_after=None, info=None):
        self.frames = []
        self.fail_after = fail_after
        self.info = info

    def write(self, frame):
        if self.fail_after is not None and len(self.frames) >= self.fail_after:
            self.frames.append(("FAILED", frame))
            raise HidError("simulated unplug")
        self.frames.append(frame)

    def transact(self, frame, timeout=0.5):
        return self.info

    def describe(self):
        return "recording"

    def close(self):
        pass


def kb(frames):
    return [f for f in frames if isinstance(f, bytes) and f[3] == P.CMD_KEYBOARD]


def mouse(frames):
    return [f for f in frames if isinstance(f, bytes) and f[3] == P.CMD_MOUSE_ABS]


def fast(link):
    return Hid(link, key_hold_ms=0, key_gap_ms=0, click_hold_ms=0)


def test_press_combo_sequence_and_release():
    link = RecordingLink()
    fast(link).press(parse_sequence("ctrl+alt+delete"))
    k = kb(link.frames)
    assert k[0] == P.keyboard_frame(P.MOD_LCTRL | P.MOD_LALT, ())
    assert k[1] == bytes.fromhex("57 AB 00 02 08 05 00 4C 00 00 00 00 00 5D")
    assert k[-1] == P.RELEASE_KEYBOARD
    assert link.frames[-1] == P.RELEASE_MOUSE_REL   # buttons released at the end ...
    assert mouse(link.frames) == []                 # ... without an absolute report (no pointer yank)


def test_typing_never_moves_the_pointer():
    link = RecordingLink()
    h = fast(link)
    h.click(100, 100)
    n_abs = len(mouse(link.frames))
    h.type_text("hello")
    h.press(parse_sequence("enter"))
    assert len(mouse(link.frames)) == n_abs         # no absolute reports while typing


def test_release_all_releases_a_held_button_in_place():
    link = RecordingLink()
    h = fast(link)
    h._mouse(P.BTN_LEFT, (300, 400))                # simulate a button left down
    h.release_all()
    last_abs = mouse(link.frames)[-1]
    assert last_abs[6] == 0 and (last_abs[7] | last_abs[8] << 8, last_abs[9] | last_abs[10] << 8) == (300, 400)


def test_busy_instead_of_queueing():
    link = RecordingLink()
    h = Hid(link, key_hold_ms=0, key_gap_ms=0, click_hold_ms=0, busy_timeout=0.2)
    h._lock.acquire()                               # another action holds the device
    errs = []
    t = threading.Thread(target=lambda: errs.append(pytest.raises(HidBusy, h.type_text, "SECRET")))
    t.start()
    t.join(5)
    h._lock.release()
    assert errs and kb(link.frames) == []           # nothing typed later


def test_waiting_action_cancelled_by_release_all():
    link = RecordingLink()
    h = Hid(link, key_hold_ms=0, key_gap_ms=0, click_hold_ms=0, busy_timeout=5)
    h._lock.acquire()
    out = {}

    def queued_drag():
        try:
            h.drag((500, 500), (900, 900), steps=3, step_ms=0)
        except HidAborted as e:
            out["err"] = e

    t = threading.Thread(target=queued_drag)
    t.start()
    time.sleep(0.1)
    rel = threading.Thread(target=h.release_all)
    rel.start()
    time.sleep(0.1)
    h._lock.release()
    t.join(5)
    rel.join(5)
    assert "err" in out
    assert not [f for f in mouse(link.frames) if f[6]]   # never pressed a button


def test_missing_get_info_reply_is_cached():
    calls = []

    class NoReply(RecordingLink):
        def transact(self, frame, timeout=0.5):
            calls.append(frame)
            return None

    h = fast(NoReply())
    assert h.info() is None and h.info() is None and h.info_nowait() == (None, False)
    assert len(calls) == 1


def test_info_nowait_does_not_block_while_busy():
    h = fast(RecordingLink(info=P.info_reply_frame()))
    held, done = threading.Event(), threading.Event()

    def other_action():                 # another thread is in the middle of an action
        with h._lock:
            held.set()
            done.wait(5)

    t = threading.Thread(target=other_action)
    t.start()
    held.wait(5)
    try:
        t0 = time.monotonic()
        info, busy = h.info_nowait()
        assert busy and time.monotonic() - t0 < 0.1
    finally:
        done.set()
        t.join(5)
    assert h.info_nowait()[1] is False


def test_type_text_releases_between_keys():
    link = RecordingLink()
    typed, skipped, aborted = fast(link).type_text("aa")
    assert (typed, skipped, aborted) == (2, [], False)
    k = kb(link.frames)
    a = P.keyboard_frame(0, [0x04])
    assert k[:4] == [a, P.RELEASE_KEYBOARD, a, P.RELEASE_KEYBOARD]


def test_type_text_uses_caps_lock_state():
    link = RecordingLink(info=P.info_reply_frame(leds=0x02))
    fast(link).type_text("a")
    assert P.keyboard_frame(P.MOD_LSHIFT, [0x04]) in kb(link.frames)


def test_click_double_with_modifier():
    link = RecordingLink()
    fast(link).click(100, 200, "left", count=2, modifiers=P.MOD_LCTRL)
    m = mouse(link.frames)
    downs = [f for f in m if f[6] == P.BTN_LEFT]
    assert len(downs) == 2
    assert all((f[7] | f[8] << 8, f[9] | f[10] << 8) == (100, 200) for f in m)
    assert P.keyboard_frame(P.MOD_LCTRL, ()) in kb(link.frames)
    assert kb(link.frames)[-1] == P.RELEASE_KEYBOARD


def test_scroll_down_sends_signed_notches():
    link = RecordingLink()
    fast(link).scroll(2048, 2048, -3)
    wheels = [f[11] for f in mouse(link.frames) if f[11]]
    assert wheels == [0xFF, 0xFF, 0xFF]


def test_drag_moves_with_button_held():
    link = RecordingLink()
    fast(link).drag((0, 0), (4000, 4000), steps=5, step_ms=0)
    m = mouse(link.frames)
    held = [f for f in m if f[6] == P.BTN_LEFT]
    assert len(held) >= 6
    last_held = held[-1]
    assert (last_held[7] | last_held[8] << 8) == 4000
    assert m[-1][6] == 0 and (m[-1][7] | m[-1][8] << 8) == 4000   # released at the end point


def test_errors_still_attempt_release():
    link = RecordingLink(fail_after=3)
    with pytest.raises(HidError):
        fast(link).type_text("hello")
    # the release attempts after the failure were made (and also failed, without masking the error)
    assert sum(1 for f in link.frames if isinstance(f, tuple)) >= 2


def test_release_all_interrupts_typing():
    link = RecordingLink()
    hid = Hid(link, key_hold_ms=5, key_gap_ms=5, click_hold_ms=0)
    result = {}

    def run():
        result["r"] = hid.type_text("x" * 400)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.15)
    hid.release_all()
    t.join(timeout=5)
    typed, _skipped, aborted = result["r"]
    assert aborted and 0 < typed < 400
    assert kb(link.frames)[-1] == P.RELEASE_KEYBOARD


def test_unknown_button():
    with pytest.raises(HidError):
        fast(RecordingLink()).click(0, 0, "fourth")
