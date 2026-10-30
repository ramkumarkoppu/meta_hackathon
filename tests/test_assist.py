"""The live viewer's prompt box (assist.py): questions about the screen and Fix it, with every approval asked in the
window. Uses the simulated VS Code, a fake Glimmer and a fake Meta API from test_editing."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from fakes import spark_stream
from nanokvm_mcp import editing as E
from nanokvm_mcp.assist import AssistSession, EventQueue
from nanokvm_mcp.glimmer import WINDOW_PROMPT, GlimmerClient
from nanokvm_mcp.pipeline import Pipeline
from nanokvm_mcp.spark import SparkClient
from test_editing import E_KEY, FILE, FIX, _call, world  # noqa: F401  (world is a fixture)


@pytest.fixture(autouse=True)
def _no_pauses(monkeypatch):
    monkeypatch.setattr(E.EditTyper, "_pause", lambda self, factor=1.0: None)


def _session(w, wrap_act=None):
    events = EventQueue()
    made = []

    def make(cfg, **kw):
        pipe = Pipeline(cfg, glimmer=GlimmerClient(cfg), spark=SparkClient(cfg, base_url=f"http://127.0.0.1:{w.meta.port}"),
                        **kw)
        if wrap_act:
            pipe.svc.act = wrap_act(pipe.svc.act)
        made.append(pipe)
        return pipe
    s = AssistSession(w.cfg, events.put, make_pipeline=make)
    return s, events, made


def _drive(s, events, answer, timeout=60.0):
    """Answer the session's approvals with ``answer(approval)`` until its work is over; returns every event."""
    seen, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        for kind, payload in events.take():
            seen.append((kind, payload))
            if kind == "approve":
                s.answer(payload, answer(payload))
            elif kind == "idle":
                return seen
        time.sleep(0.02)
    raise AssertionError(f"the work didn't end; events so far: {seen}")


def _approvals(seen):
    return [p for k, p in seen if k == "approve"]


def _texts(seen, kind):
    return [p for k, p in seen if k == kind]


def _image_reads(w):
    return [b for b in w.glim.bodies("/v1/chat/completions") if "image_url" in json.dumps(b)]


# ---------------------------------------------------------------- questions
def test_a_question_reads_the_window_in_front_and_spark_answers_from_redacted_text(world):
    w = world
    w.replies += [spark_stream("Line 6 reads v[n] when i == n: use i < n.")]
    s, events, _ = _session(w)
    assert s.ask("why does sum_first read past the end?")             # the window in front, by default
    seen = _drive(s, events, lambda a: True)
    (share,) = _approvals(seen)
    assert share.kind == "share" and "for 30 min" in share.text and "test or dummy content" in share.text
    (text, report) = _texts(seen, "answer")[0]
    assert text == "Line 6 reads v[n] when i == n: use i < n." and "store=false" in report and "EMAIL x1" in report
    prompts = [b["messages"][1]["content"][0]["text"] for b in _image_reads(w)]
    assert prompts == [WINDOW_PROMPT]                                  # one reading, of the window in front
    sent = json.dumps(w.meta.requests[0][2])
    assert "jane.doe" not in sent and "[[EMAIL_1]]" in sent and "why does sum_first read past the end?" in sent
    assert any("asking Muse Spark" in m for m in _texts(seen, "say"))
    assert s.shared_minutes_left() >= 29 and s.test_confirmed and s.pipe.stand_in
    w.replies += [spark_stream("It sums the first n values.")]
    assert s.ask("what does sum_first do?", kind="code")
    assert _approvals(_drive(s, events, lambda a: True)) == []         # shared and confirmed: nothing asked again
    s.close()
    assert not w.env.service.share_active(s.pipe.client_id)           # closing the window ends the share
    assert w.env.service._hid is None                                 # questions never open the keyboard link


def test_local_only_sends_nothing_and_needs_no_test_content_answer(world):
    w = world
    s, events, _ = _session(w)
    assert s.ask("what is on line 6?", local_only=True)
    seen = _drive(s, events, lambda a: True)
    (share,) = _approvals(seen)
    assert "test or dummy" not in share.text
    assert _texts(seen, "answer")[0] == ("a local answer", "(answered by Muse Glimmer on this laptop; nothing was sent)")
    assert w.meta.requests == []


def test_a_no_reads_and_sends_nothing(world):
    w = world
    s, events, _ = _session(w)
    assert s.ask("why does it crash?")
    seen = _drive(s, events, lambda a: False)
    assert "Not approved: nothing was read, sent or typed." in _texts(seen, "info")
    assert _image_reads(w) == [] and w.meta.requests == [] and not s.test_confirmed
    assert not w.env.service.share_active(s.pipe.client_id)


def test_a_share_the_service_lost_is_asked_for_again_with_the_test_content_question(world):
    w = world
    w.replies += [spark_stream("One."), spark_stream("Two.")]
    s, events, _ = _session(w)
    assert s.ask("first?")
    assert [a.kind for a in _approvals(_drive(s, events, lambda a: True))] == ["share"]
    w.env.service.unshare(s.pipe.client_id)                       # as when the service restarts, or the share ends
    assert s.ask("second?")
    (again,) = _approvals(_drive(s, events, lambda a: True))
    assert again.kind == "share" and "test or dummy content" in again.text   # asked with every share
    assert len(w.meta.requests) == 2 and w.env.service.share_active(s.pipe.client_id)


def test_a_local_only_share_leaves_the_test_content_question_for_the_first_send(world):
    w = world
    w.replies += [spark_stream("Sent.")]
    s, events, _ = _session(w)
    assert s.ask("local?", local_only=True)
    (share,) = _approvals(_drive(s, events, lambda a: True))
    assert "test or dummy" not in share.text and not s.test_confirmed
    assert s.ask("to Spark?")
    (test,) = _approvals(_drive(s, events, lambda a: True))
    assert test.kind == "test" and "test or dummy content" in test.text and len(w.meta.requests) == 1


def test_fix_it_shares_again_when_less_than_ten_minutes_are_left(world, monkeypatch):
    w = world
    w.replies += [spark_stream("Fine."), spark_stream("Nothing needs fixing.")]
    s, events, made = _session(w)
    assert s.ask("anything?")
    _drive(s, events, lambda a: True)
    monkeypatch.setattr(made[0], "share_seconds_left", lambda: 5 * 60.0)   # 5 min left: fine for a question...
    w.replies.insert(0, spark_stream("Still fine."))
    assert s.ask("again?")
    assert _approvals(_drive(s, events, lambda a: True)) == []
    assert s.fix()                                                         # ...but not for Fix it
    (share,) = _approvals(_drive(s, events, lambda a: True))
    assert share.kind == "share"


def test_the_window_can_make_the_settings_stricter_never_looser(world):
    w = world
    w.cfg.outbound = "local_only"
    s, events, _ = _session(w)
    assert s.ask("anything?", local_only=False)                       # nanokvm.toml says local only: it stays so
    _drive(s, events, lambda a: True)
    assert w.meta.requests == [] and s.can_fix() and "local_only" in s.can_fix()
    w.cfg.outbound = "auto"
    w.replies += [spark_stream("Fine.")]
    s2, events2, _ = _session(w)
    assert s2.ask("anything?", show_first=True)                        # ticked: the exact text is shown first
    seen = _drive(s2, events2, lambda a: True)
    send = [a for a in _approvals(seen) if a.kind == "send"]
    assert len(send) == 1 and "[[EMAIL_1]]" in send[0].text and send[0].mono and len(w.meta.requests) == 1


# ---------------------------------------------------------------- Fix it
def test_fix_it_types_each_approved_edit_then_saves_with_the_question_as_a_hint(world):
    w = world
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="use < so v[n] isn't read"),
                  spark_stream("Done."), spark_stream("FIXED: the loop stops before v[n].")]
    s, events, _ = _session(w)
    assert s.fix("why does sum_first read past the end?")
    seen = _drive(s, events, lambda a: True)
    assert [a.kind for a in _approvals(seen)] == ["share", "edit", "save"]
    edit = _approvals(seen)[1]
    assert edit.mono and "-    6" in edit.text and "+    6" in edit.text and "Keys sent to the locked computer" in edit.text
    assert w.ed.lines == FILE[:5] + [FIX] + FILE[6:] and w.ed.saves == 1 and not w.ed.dirty
    first = w.meta.requests[0][2]["messages"][1]["content"]
    assert first.startswith(E.FIX_REQUEST + "\n\nThe user's own question about the screen: why does sum_first read "
                                            "past the end?")
    report = _texts(seen, "report")[0]
    assert [tag for line, tag in report if line.startswith("Saved on the locked computer")] == ["ok"]
    assert any("1 edit(s) typed, 1 checked on the screen" in line for line, _tag in report)
    assert [tag for line, tag in report if line.startswith("FIXED")] == [""]   # Spark's own text is never coloured


def test_fix_it_with_auto_asks_once_naming_the_typing_and_the_save(world):
    w = world
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="loop bound"), spark_stream("Done."),
                  spark_stream("FIXED: the loop bound.")]
    s, events, _ = _session(w)
    assert s.fix(auto=True)
    seen = _drive(s, events, lambda a: True)
    (only,) = _approvals(seen)
    assert only.kind == "share" and "without asking for each one, and save the file" in only.text
    assert "test or dummy content" in only.text
    assert any("Auto: typing it without asking" in e for e in _texts(seen, "edit"))
    assert w.ed.lines == FILE[:5] + [FIX] + FILE[6:] and w.ed.saves == 1


def test_a_declined_edit_or_save_is_respected(world):
    w = world
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="loop bound"), spark_stream("Left as it is.")]
    s, events, _ = _session(w)
    assert s.fix()
    seen = _drive(s, events, lambda a: a.kind != "edit")
    assert w.ed.lines == FILE and _keys_typed(w) == [] and w.ed.saves == 0
    assert any(line.endswith(": declined") for line, _tag in _texts(seen, "report")[0])
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="loop bound"), spark_stream("Done."),
                  spark_stream("FIXED: the loop bound.")]
    assert s.fix()
    seen = _drive(s, events, lambda a: a.kind != "save")              # the edit yes, the save no
    assert w.ed.lines == FILE[:5] + [FIX] + FILE[6:] and w.ed.saves == 0 and w.ed.dirty
    assert ("Not saved (you said no). Review the changes in VS Code before you save. Ctrl+Z, or File: Revert File, "
            "undoes them.", "") in _texts(seen, "report")[0]


def test_auto_after_a_question_asks_once_more_naming_the_save_and_a_no_types_nothing(world):
    w = world
    w.replies += [spark_stream("Line 6 reads v[n].")]
    s, events, _ = _session(w)
    assert s.ask("what is wrong?")
    _drive(s, events, lambda a: True)
    assert s.fix(auto=True)
    seen = _drive(s, events, lambda a: False)
    (auto,) = _approvals(seen)
    assert auto.kind == "auto" and "save the file once Spark has checked the fix" in auto.text
    assert w.ed.lines == FILE and _keys_typed(w) == [] and len(w.meta.requests) == 1


def test_stop_while_typing_stops_the_typing_and_releases_every_key(world):
    w = world
    w.cfg.key_hold_ms, w.cfg.key_gap_ms = 40, 20                       # about 3 s to type the new line
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="loop bound")]
    actions, summaries = [], []
    holder = SimpleNamespace(session=None)

    def wrap(act):
        def recorded(body):
            actions.append(body["action"])
            if body["action"] == "type" and len(body["text"]) > 5:
                threading.Timer(0.4, holder.session.stop).start()     # Stop pressed while the line is typed
            reply = act(body)
            summaries.append(str(reply.get("summary", "")))
            return reply
        return recorded
    s, events, _ = _session(w, wrap_act=wrap)
    holder.session = s
    assert s.fix(auto=True)
    seen = _drive(s, events, lambda a: True)
    assert "Stopped: every key was released. Check the file on the locked computer." in _texts(seen, "info")
    assert "release_all" in actions and w.ed.lines[5] != FIX                 # the typing really stopped part-way
    _wait_until(lambda: any("STOPPED early by release_all" in x for x in summaries))
    assert w.env.desk.keys_down == ()
    s.close()
    assert actions[-1] == "release_all"                                     # closing the window releases them again


def test_the_hint_prefix_doesnt_trip_the_leak_check(world):
    w = world
    w.ed.lines[2] = '    const char* password = "question";'            # redacted, and a word of HINT_INTRO only
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="loop bound"), spark_stream("Done."),
                  spark_stream("FIXED.")]
    s, events, _ = _session(w)
    assert s.fix("why does it read past the end?")
    seen = _drive(s, events, lambda a: True)
    assert _texts(seen, "error") == [] and w.ed.lines[5] == FIX
    assert '\\"question\\"' not in json.dumps([r[2] for r in w.meta.requests])


def _wait_until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_fix_it_is_off_without_keyboard_control(world):
    w = world
    w.cfg.control = False
    s, events, _ = _session(w)
    assert s.fix()
    seen = _drive(s, events, lambda a: True)
    assert any("keyboard/mouse control is off" in e for e in _texts(seen, "error"))
    assert _approvals(seen) == [] and w.meta.requests == []


def test_one_piece_of_work_at_a_time(world):
    w = world
    s, events, _ = _session(w)
    assert s.ask("first?")
    assert not s.ask("second?") and not s.fix()                      # busy: the window keeps the second question
    _drive(s, events, lambda a: False)
    assert not s.ask("   ")                                          # nothing to ask


def _keys_typed(w):
    return [keys for mods, keys in w.env.desk.combos if E_KEY("enter") in keys or E_KEY("backspace") in keys]
