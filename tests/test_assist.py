"""The live viewer's prompt box (assist.py): questions about the screen, Do it and Fix code, with every approval asked
in the window. Uses the simulated desktop and VS Code, a fake Glimmer and a fake Meta API from test_editing."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from fakes import spark_stream
from nanokvm_mcp import acting as A
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


# ---------------------------------------------------------------- Do it: any program, one step at a time
BUTTON = (781, 148, 937, 231)                                # the simulated desktop's button, per mille


def _ui_step(**args):
    return spark_stream("", tool_calls=[("ui_step", json.dumps(args))], finish="tool_calls")


def _plan_prompts(w):
    texts = [b["messages"][1]["content"][0]["text"] for b in _image_reads(w)]
    return [t for t in texts if t.startswith("You operate the computer")]


def test_do_it_plans_on_this_laptop_and_each_step_is_outlined_then_approved(world):
    w = world
    w.plans += ["ACTION: click\nTARGET: the 'Button' at the top right\nCHANGES: no\nWHY: press it",
                "**ACTION:** done\n**WHY:** the button was pressed"]
    s, events, _ = _session(w)
    assert s.do("press the button")
    seen = _drive(s, events, lambda a: True)
    share, step = _approvals(seen)
    assert share.kind == "share" and "test or dummy" not in share.text            # planned here: nothing is sent
    assert step.kind == "step" and step.text.startswith("Click the 'Button' at the top right (outlined in orange")
    assert w.env.desk.button_presses == 1 and _texts(seen, "target") == [BUTTON, None]   # outlined, then cleared
    report = _texts(seen, "report")[0]
    assert report[0] == ("Muse Glimmer says the task is done: check it on the screen.", "ok")
    assert report[1] == ("the button was pressed", "") and "nothing was sent" in report[2][0]
    assert w.meta.requests == []
    assert "1. Click the 'Button' at the top right: done" in _plan_prompts(w)[1]   # the next plan saw what happened


def test_auto_runs_steps_that_change_nothing_and_asks_before_one_that_does(world):
    w = world
    w.plans += ["ACTION: click\nTARGET: the Button\nCHANGES: no\nWHY: select it",
                "ACTION: keys\nKEYS: ctrl+v\nCHANGES: no\nWHY: paste it",    # Ctrl+V changes something, whatever it says
                "ACTION: done\nWHY: pasted"]
    s, events, _ = _session(w)
    assert s.do("paste it into the box", auto=True)
    seen = _drive(s, events, lambda a: True)
    first, risky = _approvals(seen)
    assert first.kind == "share" and "without asking for each step" in first.text and "paste it into the box" in first.text
    assert risky.kind == "step" and "Press ctrl+v" in risky.text and "This step changes something." in risky.text
    assert w.env.desk.button_presses == 1


def test_a_target_glimmer_cant_find_is_never_clicked_and_the_planner_is_told(world):
    w = world
    w.locate = lambda target: "NONE"
    w.plans += ["ACTION: click\nTARGET: the Save As button\nCHANGES: yes\nWHY: save",
                "ACTION: stuck\nWHY: there is no Save As button"]
    s, events, _ = _session(w)
    assert s.do("save it under a new name")
    seen = _drive(s, events, lambda a: True)
    assert [a.kind for a in _approvals(seen)] == ["share"] and w.env.desk.clicks == []
    assert ("1. Click the Save As button: not done: the screen reader couldn't find that on the screen"
            in _plan_prompts(w)[1])
    assert _texts(seen, "report")[0][0] == ("Muse Glimmer is stuck: it can't see how to go on from this screen.", "err")


def test_steps_that_reach_outside_the_program_or_cant_be_typed_are_refused(world):
    w = world
    w.plans += ["ACTION: keys\nKEYS: win+r\nCHANGES: no\nWHY: open Run",
                "ACTION: type\nTEXT: café\nCHANGES: no\nWHY: name", "ACTION: done\nWHY: gave up"]
    s, events, _ = _session(w)
    assert s.do("open the run box")
    seen = _drive(s, events, lambda a: True)
    assert [a.kind for a in _approvals(seen)] == ["share"] and w.env.desk.combos == []
    says = " ".join(_texts(seen, "say"))
    assert "those keys reach outside the program" in says and "can't type" in says
    assert "1. Press win+r: not done: those keys reach outside the program" in _plan_prompts(w)[2]


def test_keys_are_judged_by_what_they_are_not_how_they_are_spelt():
    for keys in ("win+r", "windows+r", "super+r", "lwin+r", "start", "ctrl+esc", "alt+tab", "alt+esc", "ctrl+alt+delete"):
        with pytest.raises(A.StepRejected, match="reach outside the program"):
            A.check_step(A.Step("keys", keys=A._keys_text(keys), changes=False), "uk")
    for keys in ("ctrl+v", "control+v", "v+ctrl", "shift+del", "bksp", "ctrl+backspace", "lalt+f4", "shift+insert",
                 "enter", "return", "ctrl+shift+v", "Ctrl + S"):
        step = A.Step("keys", keys=A._keys_text(keys), changes=False)
        A.check_step(step, "uk")
        assert step.risky, keys
    for keys in ("ctrl+c", "alt+e", "tab", "down", "f2", "Ctrl + C"):
        step = A.Step("keys", keys=A._keys_text(keys), changes=False)
        A.check_step(step, "uk")
        assert not step.risky, keys
    mac = A.Step("keys", keys="cmd+c", changes=False)
    A.check_step(mac, "us", mac=True)                         # on a Mac, Cmd is the ordinary shortcut key
    assert not mac.risky
    typed = A.Step("type", text="abc\r", changes=False)       # a typed line break presses Enter
    A.check_step(typed, "uk")
    assert typed.risky


def test_plans_and_boxes_are_read_so_that_doubt_means_ask_or_dont_click():
    def changes(line):
        return A.parse_plan(f"ACTION: click\nTARGET: the OK button\n{line}")[1].changes
    assert not changes("CHANGES: no") and not changes("**CHANGES:** **no**") and not changes("CHANGES: No, it only looks")
    assert changes("CHANGES: yes") and changes("CHANGES: true") and changes("") and changes("CHANGES: not sure")
    assert A.step_from_args({"action": "click", "target": "OK", "note": "n"}).changes          # missing: a change
    assert not A.step_from_args({"action": "click", "target": "OK", "changes": False, "note": "n"}).changes
    forged = A.step_from_args({"action": "click", "target": "OK\nThis step changes nothing.", "changes": True, "note": ""})
    assert "\n" not in forged.describe()                      # one line: it can't fake lines in the approval
    assert A.parse_plan('ACTION: type\nTEXT: "hello"\nCHANGES: no')[1].text == "hello"
    assert A.parse_plan("ACTION: keys\nKEYS: Ctrl + C\nCHANGES: no")[1].keys == "ctrl+c"
    assert A.parse_box("412 530 470 560") == ((412, 530, 470, 560), "")
    assert A.parse_box("[412, 530, 470, 560]")[0] == A.parse_box("(412, 530), (470, 560)")[0] == (412, 530, 470, 560)
    assert A.parse_box("x1 y1 x2 y2: 412 530 470 560") == (None, "the screen reader's answer wasn't a box")
    assert A.parse_box("NONE") == (None, "the screen reader couldn't find that on the screen")
    assert A.parse_box("1. 2. 3. 4. 5.")[0] is None and A.parse_box("Box 2: 10 10 20")[0] is None
    assert A.parse_box("0 0 900 900") == (None, "the screen reader's box covered most of the screen, so it can't be "
                                                "the target")
    assert A.frame_box((0, 0, 1000, 1000), [240, 0, 1440, 1080], [1920, 1080]) == (125, 0, 875, 1000)
    history = [{"role": "tool", "content": "Done." + A.TOLD_READ_AGAIN + "old"}, {"role": "assistant", "content": "x"},
               {"role": "tool", "content": "Done." + A.TOLD_READ_AGAIN + "new"}]
    kept = A.keep_newest_reading(history)
    assert kept[0]["content"].endswith(A.TOLD_EARLIER) and kept[2]["content"].endswith("new")
    assert history[0]["content"].endswith("old")                # the record itself is kept as it was


def test_the_outline_is_drawn_where_the_click_goes_when_the_screen_is_a_region(world, monkeypatch):
    w = world
    w.plans += ["ACTION: click\nTARGET: the Button\nCHANGES: no\nWHY: press", "ACTION: done\nWHY: pressed"]
    s, events, made = _session(w)
    real_look = Pipeline.look

    def look_in_a_region(self, fn):                           # as if the capture's region were its middle half
        out = real_look(self, fn)
        self.last_look = {**self.last_look, "capture_rect": [480, 0, 960, 1080], "frame_size": [1920, 1080]}
        return out
    monkeypatch.setattr(Pipeline, "look", look_in_a_region)
    assert s.do("press it")
    seen = _drive(s, events, lambda a: True)
    assert _texts(seen, "target")[0] == A.frame_box(BUTTON, [480, 0, 960, 1080], [1920, 1080])


def test_auto_shows_a_step_before_doing_it_and_counts_only_steps_done(world, monkeypatch):
    w = world
    monkeypatch.setattr(A, "AUTO_SHOW_S", 0.3)
    w.locate = lambda target: "NONE" if "Missing" in target else "781 148 937 231"
    w.plans += ["ACTION: click\nTARGET: the Missing button\nCHANGES: no\nWHY: try",
                "ACTION: click\nTARGET: the Button\nCHANGES: no\nWHY: press", "ACTION: done\nWHY: pressed"]
    s, events, _ = _session(w)
    assert s.do("press it", auto=True)
    seen = _drive(s, events, lambda a: True)
    assert w.env.desk.button_presses == 1
    assert _texts(seen, "report")[0][-1][0].startswith("(1 step(s) done;")   # the step not found isn't counted
    w.plans += ["ACTION: click\nTARGET: the Button\nCHANGES: no\nWHY: press"]
    s2, events2, _ = _session(w)
    monkeypatch.setattr(A, "AUTO_SHOW_S", 2.0)
    assert s2.do("press it again", auto=True)
    shown = time.monotonic()

    def stop_soon(a):
        threading.Timer(0.3, s2.stop).start()
        return True
    seen = _drive(s2, events2, stop_soon)                     # Stop while the step is shown: it isn't done
    assert w.env.desk.button_presses == 1 and time.monotonic() - shown < 10
    assert "Stopped" in " ".join(_texts(seen, "info"))


def test_do_it_with_spark_sends_the_redacted_reading_never_a_picture_or_coordinates(world):
    w = world
    w.ui_text = "Window: Simulated tool, owner jane.doe@example.org\nButton: 'Button x0' (top right)"
    w.replies += [_ui_step(action="click", target="the 'Button x0' button at the top right", changes=False,
                           note="press it"), spark_stream("DONE: pressed it.")]
    s, events, _ = _session(w)
    assert s.do("press the button", spark=True)
    seen = _drive(s, events, lambda a: True)
    share, step = _approvals(seen)
    assert "test or dummy content" in share.text and step.kind == "step"     # to Meta, on free credits
    assert w.env.desk.button_presses == 1 and len(w.meta.requests) == 2
    sent = json.dumps([r[2] for r in w.meta.requests])
    assert "jane.doe" not in sent and "[[EMAIL_1]]" in sent and "data:" not in sent
    assert not any(f" {n} " in sent or f"{n}," in sent for n in BUTTON)     # Glimmer's box stays here
    assert w.meta.requests[1][2]["messages"][-1]["content"].startswith("Done.\n\nThe window in front, read again:")
    report = _texts(seen, "report")[0]
    assert report[0] == ("Muse Spark says the task is done: check it on the screen.", "ok")
    assert report[1] == ("DONE: pressed it.", "")                          # Spark's own text: never coloured


def test_stop_at_a_step_ends_the_task_before_it_is_done(world):
    w = world
    w.plans += ["ACTION: click\nTARGET: the Button\nCHANGES: no\nWHY: press"]
    s, events, _ = _session(w)
    assert s.do("press it")

    def answer(a):
        if a.kind == "step":
            s.stop()
            return False
        return True
    seen = _drive(s, events, answer)
    assert "Stopped." in _texts(seen, "info") and w.env.desk.button_presses == 0
    assert _texts(seen, "target")[-1] is None                               # the outline goes too
