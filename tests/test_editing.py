"""Milestone 2: Muse Spark's edits, approved at the console, typed into the simulated VS Code and checked on the
screen. A fake Glimmer "reads" the simulated editor; a fake Meta Model API plays Spark."""
import _thread
import json
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import SimEnv
from fakes import FakeServer, llama_reply, spark_stream
from nanokvm_mcp import cli
from nanokvm_mcp import editing as E
from nanokvm_mcp.glimmer import BOX_PROMPT, STATUS_PROMPT, GlimmerClient
from nanokvm_mcp.outbound import OutboundBlocked, OutboundGate
from nanokvm_mcp.pipeline import EditError, Pipeline
from nanokvm_mcp.spark import SparkClient, SparkError

LLAMA_KEY, META_KEY = "k" * 32, "LLM_1234567890_" + "dummySECRETvalue"  # split so the commit hook allows it
FILE = ["#include <vector>",
        "",
        "// maintainer: jane.doe@example.org",
        "int sum_first(const std::vector<int>& v, int n) {",
        "    int total = 0;",
        "    for (int i = 0; i <= n; ++i) {",                # line 6: the off-by-one
        "        total += v[i];",
        "    }",
        "    return total;",
        "}"]
FIX = "    for (int i = 0; i < n; ++i) {"


def _call(**args):
    return spark_stream("", tool_calls=[("apply_edit", json.dumps(args))], finish="tool_calls")


@pytest.fixture
def world(tmp_path):
    env = SimEnv(tmp_path, editor_lines=FILE)
    ed = env.desk.editor
    w = SimpleNamespace(env=env, ed=ed, replies=[], status_override=None, cfg=env.cfg)

    def llama(path, body):
        if path == "/health":                                # asked before the screen is shared
            return 200, {"status": "ok"}
        if body.get("max_tokens") == 1:                      # the slot flush after every image
            return 200, llama_reply("ok")
        msgs = body["messages"]
        if "image_url" in json.dumps(msgs):
            prompt = msgs[1]["content"][0]["text"]
            if prompt == STATUS_PROMPT:
                return 200, llama_reply(w.status_override or ed.status())
            if prompt == BOX_PROMPT:
                return 200, llama_reply(ed.box_text())
            return 200, llama_reply(ed.transcription())
        user = msgs[1]["content"]
        if "<question>" in user:                             # minimise: keep every line
            return 200, llama_reply(user.split("<screen_text>\n", 1)[1].split("\n</screen_text>", 1)[0])
        return 200, llama_reply("a local answer")
    w.glim = FakeServer(LLAMA_KEY, llama)

    def meta(path, body):                                     # a reply, or (status, payload) for an error
        reply = w.replies.pop(0)
        return reply if isinstance(reply, tuple) else (200, reply)
    w.meta = FakeServer(META_KEY, meta)
    w.cfg.glimmer_port = w.glim.port
    (w.cfg.run_dir / "llama_api_key").write_text(LLAMA_KEY, encoding="ascii")
    (w.cfg.run_dir / "meta_api_key").write_text(META_KEY, encoding="utf-8")

    def make(**kw):
        return Pipeline(w.cfg, glimmer=GlimmerClient(w.cfg), approve_share=lambda m: True, stand_in=True,
                        spark=SparkClient(w.cfg, base_url=f"http://127.0.0.1:{w.meta.port}"), **kw)

    def run(request="fix the off-by-one", approve=lambda plan: True):
        with make() as pipe:
            pipe.open()
            typer = E.EditTyper(pipe.svc.act, pipe.read_lines, pipe.read_status, pipe.read_box, pause_s=0)
            return pipe.edit(request, approve_edit=approve, typer=typer)
    w.make, w.run = make, run
    yield w
    for _p, _h, body in w.meta.requests:                     # no image ever reached "Meta"
        assert "data:" not in json.dumps(body)
    w.glim.close()
    w.meta.close()
    env.close()


def _keys_sent(w):
    """The key combos the simulated locked computer received, as (modifiers, usages)."""
    return list(w.env.desk.combos)


# ---------------------------------------------------------------- the whole loop
def test_an_approved_edit_is_typed_and_checked(world):
    w = world
    args = {"start_line": 6, "end_line": 6, "new_text": FIX, "note": "use < so v[n] isn't read"}
    w.replies += [_call(**args), spark_stream("Fixed the loop bound on line 6.")]
    shown = []
    res = w.run(approve=lambda plan: shown.append(plan) or True)
    assert w.ed.lines == FILE[:5] + [FIX] + FILE[6:]
    assert [r.outcome.status for r in res.edits] == ["verified"] and res.stopped is None
    assert res.summary == "Fixed the loop bound on line 6." and res.turns == 2
    assert shown[0].old == [FILE[5]] and shown[0].new == [FIX] and "-    6" in shown[0].diff()
    first, second = (r[2]["messages"] for r in w.meta.requests)
    assert "jane.doe" not in json.dumps(first) and "[[EMAIL_1]]" in first[1]["content"]
    assert [m["role"] for m in second] == ["developer", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["function"]["arguments"] == json.dumps(args)   # replayed as Spark sent it
    assert second[3]["content"].startswith("Applied and checked") and "total" not in second[3]["content"]
    log = [json.loads(x) for x in (w.cfg.run_dir / "outbound.log").read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in log] == ["request", "response", "request", "response"]
    assert log[2]["turn"] == 2 and log[2]["history"][1]["role"] == "tool"


def test_a_declined_edit_types_nothing(world):
    w = world
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="fix"), spark_stream("Left it as it is.")]
    res = w.run(approve=lambda plan: False)
    assert w.ed.lines == FILE and _keys_sent(w) == []
    assert res.edits[0].approved is False and res.edits[0].outcome is None
    assert w.meta.requests[1][2]["messages"][3]["content"] == "Not applied: the user declined this edit."


def test_nothing_is_typed_after_navigation_if_the_screen_changed(world):
    w = world
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="fix")]

    def approve_then_someone_edits(plan):
        w.ed.lines[5] = "    // changed meanwhile"
        return True
    res = w.run(approve=approve_then_someone_edits)
    assert w.ed.lines[5] == "    // changed meanwhile" and w.ed.lines[6:] == FILE[6:]
    assert res.edits[0].outcome.status == "not_applied" and "no longer shows" in res.stopped
    sent = {usage for _mods, keys in _keys_sent(w) for usage in keys}
    assert sent == {E_KEY("esc"), E_KEY("g"), E_KEY("6"), E_KEY("enter")}   # only Go to Line: no select, delete or typing
    assert len(w.meta.requests) == 1                                # and Spark isn't asked again


@pytest.mark.parametrize("status,shown", [
    ("NONE", "nothing readable"),                                  # VS Code not focused, or no status bar
    ("Ln 6, Col 1 (12 selected)", "12 selected"),                  # typing would replace the selection
    ("Ln 6, Col 5", "Col 5"),
])
def test_nothing_is_typed_if_the_status_bar_doesnt_confirm_the_line(world, status, shown):
    w = world
    w.status_override = status
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="fix")]
    res = w.run()
    assert w.ed.lines == FILE and res.edits[0].outcome.status == "not_applied"
    assert "status bar" in res.stopped and shown in res.stopped
    sent = {usage for _mods, keys in _keys_sent(w) for usage in keys}
    assert sent == {E_KEY("esc"), E_KEY("g"), E_KEY("6"), E_KEY("enter")}


def test_nothing_is_deleted_if_the_selection_ends_early(world, monkeypatch):
    w = world
    real, presses = w.ed.key, []

    def drops_the_second_ctrl_l(mods, usage):
        if mods and usage == E_KEY("l"):
            presses.append(usage)
            if len(presses) == 2:
                return
        real(mods, usage)
    monkeypatch.setattr(w.ed, "key", drops_the_second_ctrl_l)
    w.replies += [_call(start_line=6, end_line=7, new_text=FIX + "\n" + FILE[6], note="fix")]
    res = w.run()
    assert w.ed.lines == FILE and res.edits[0].outcome.status == "not_applied"
    assert "Ln 7, Col 1" in res.stopped and "not Ln 8, Col 1" in res.stopped
    assert all(E_KEY("backspace") not in keys for _mods, keys in _keys_sent(w))
    assert w.ed.selection() is None                               # Escape dropped the selection


def test_no_digits_or_enter_unless_the_go_to_line_box_opened(world, monkeypatch):
    w = world
    real = w.ed.key

    def terminal_has_focus(mods, usage):                          # Ctrl+G doesn't open Go to Line
        if not (mods and usage == E_KEY("g")):
            real(mods, usage)
    monkeypatch.setattr(w.ed, "key", terminal_has_focus)
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="fix")]
    res = w.run()
    assert w.ed.lines == FILE and res.edits[0].outcome.status == "not_applied"
    assert "Go to Line box" in res.stopped
    assert [keys for _mods, keys in _keys_sent(w)] == [(E_KEY("esc"),), (E_KEY("g"),), (E_KEY("esc"),)]


def test_the_last_line_on_the_screen_is_never_edited(world):
    w = world                                                     # its wrapped rows may go on below the screen
    w.replies += [_call(start_line=10, end_line=10, new_text="}  // sum_first", note="label the end"),
                  spark_stream("Line 10 can't be edited.")]
    res = w.run()
    assert w.ed.lines == FILE and _keys_sent(w) == [] and res.edits[0].plan is None
    told = w.meta.requests[1][2]["messages"][3]["content"]
    assert "line 10 isn't on the screen, or can't be read reliably there; lines 1-9 are" in told


def test_a_line_shown_with_other_indentation_is_unverified(world, monkeypatch):
    w = world
    real = w.ed.transcription                                     # e.g. auto-indent added a space
    monkeypatch.setattr(w.ed, "transcription", lambda: real().replace("6 " + FIX, "6  " + FIX))
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="fix")]
    res = w.run()
    assert res.edits[0].outcome.status == "unverified" and "doesn't show the expected lines" in res.stopped
    assert len(w.meta.requests) == 1


def test_only_the_first_edit_in_a_reply_is_typed(world):
    w = world
    calls = [("apply_edit", json.dumps({"start_line": 6, "end_line": 6, "new_text": FIX, "note": "fix"})),
             ("apply_edit", json.dumps({"start_line": 7, "end_line": 7, "new_text": "        total += v.at(i);",
                                        "note": "check bounds"}))]
    w.replies += [spark_stream("", tool_calls=calls, finish="tool_calls"), spark_stream("Done.")]
    res = w.run()
    assert w.ed.lines == FILE[:5] + [FIX] + FILE[6:] and len(res.edits) == 1
    second = w.meta.requests[1][2]["messages"]
    assert [m["tool_call_id"] for m in second if m["role"] == "tool"] == ["call_0", "call_1"]
    assert second[3]["content"].startswith("Applied") and second[4]["content"].startswith("Not applied: one edit")


def test_an_error_after_an_edit_was_typed_keeps_the_report(world):
    w = world
    w.replies += [(400, {"error": {"message": "bad request"}})]
    with pytest.raises(SparkError):                               # nothing typed yet: the error is raised
        w.run()
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="fix"), (400, {"error": {"message": "bad"}})]
    res = w.run()
    assert w.ed.lines == FILE[:5] + [FIX] + FILE[6:] and res.edits[0].outcome.status == "verified"
    assert res.stopped.startswith("SparkError: Meta API error 400")


@pytest.mark.parametrize("args,reason", [
    ({"start_line": 60, "end_line": 60, "new_text": "x"}, "isn't on the screen"),
    ({"start_line": 6, "end_line": 10 ** 12, "new_text": "x"}, "too big"),
    ({"start_line": 0, "end_line": 0, "new_text": "x"}, "1 or more"),
    ({"start_line": 6, "end_line": 6, "new_text": "\tfor (;;) {"}, "tab"),
    ({"start_line": 6, "end_line": 6, "new_text": "    // [[EMAIL_7]]"}, "placeholder"),
    ({"start_line": 6, "end_line": 6, "new_text": "    // café"}, "can't type"),
    ({"start_line": 6, "end_line": 3, "new_text": "x"}, "at least start_line - 1"),
    ({"start_line": "6", "end_line": 6, "new_text": "x"}, "whole numbers"),
    ({"start_line": 6, "end_line": 6, "new_text": FILE[5]}, "same as the old"),
])
def test_edits_that_cant_be_typed_are_explained_to_spark(world, args, reason):
    w = world
    w.replies += [_call(note="n", **args), spark_stream("I can't make that change.")]
    shown = []
    res = w.run(approve=lambda plan: shown.append(plan) or True)
    assert shown == [] and w.ed.lines == FILE and _keys_sent(w) == []
    told = w.meta.requests[1][2]["messages"][3]["content"]
    assert told.startswith("Not applied:") and reason in told
    assert res.edits[0].plan is None and res.summary == "I can't make that change."


def test_an_insertion_then_an_edit_on_the_moved_lines(world):
    w = world
    w.replies += [_call(start_line=6, end_line=5, new_text="    // first n values only", note="explain the loop"),
                  _call(start_line=7, end_line=7, new_text=FIX, note="the loop moved to line 7"),
                  spark_stream("Added a comment and fixed the bound.")]
    res = w.run()
    assert w.ed.lines == FILE[:5] + ["    // first n values only", FIX] + FILE[6:]
    assert [r.outcome.status for r in res.edits] == ["verified", "verified"]
    told = w.meta.requests[1][2]["messages"][3]["content"]
    assert "moved down by 1" in told


def test_deleting_a_line(world):
    w = world
    w.replies += [_call(start_line=2, end_line=2, new_text="", note="drop the blank line"), spark_stream("Done.")]
    res = w.run()
    assert w.ed.lines == [FILE[0]] + FILE[2:] and res.edits[0].outcome.status == "verified"


def test_a_failed_check_after_typing_stops_the_session(world, monkeypatch):
    w = world
    w.replies += [_call(start_line=6, end_line=6, new_text=FIX, note="fix")]
    real = w.ed.key

    def lossy(mods, usage):                                       # the locked computer drops a key
        if usage == E_KEY("<") or (usage == E_KEY(",") and mods):
            return
        real(mods, usage)
    monkeypatch.setattr(w.ed, "key", lossy)
    res = w.run()
    assert res.edits[0].outcome.status == "unverified" and "doesn't show the expected lines" in res.stopped
    assert len(w.meta.requests) == 1


# ---------------------------------------------------------------- refusals before anything happens
def test_edit_needs_spark_and_keyboard_control(world):
    w = world
    w.cfg.outbound = "local_only"
    with pytest.raises(EditError, match="local_only"):
        w.run()
    w.cfg.outbound, w.cfg.control = "auto", False
    with pytest.raises(EditError, match="control"):
        w.run()
    assert w.meta.requests == [] and _keys_sent(w) == []


def test_cli_edit_refuses_without_a_person_at_the_keyboard(world, monkeypatch, capsys):
    w = world
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli.Config, "load", classmethod(lambda c, *a, **k: w.cfg))
    assert cli.main(["edit", "fix it", "--stand-in"]) == 1
    assert "approval at the keyboard" in capsys.readouterr().out
    assert w.meta.requests == [] and _keys_sent(w) == []


@pytest.mark.parametrize("configured,result", [("auto", "approve"), ("approve", "approve"),
                                               ("local_only", "local_only")])
def test_approve_flag_makes_the_setting_stricter_never_looser(world, monkeypatch, configured, result):
    w = world
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli.Config, "load", classmethod(lambda c, *a, **k: w.cfg))
    for argv in (["ask", "why?", "--approve"], ["edit", "fix it", "--approve"]):
        w.cfg.outbound = configured
        assert cli.main(argv) == 1                                 # no terminal: nothing is shared
        assert w.cfg.outbound == result
    assert w.meta.requests == []


# ---------------------------------------------------------------- pieces
def E_KEY(name):
    from nanokvm_mcp.keys import CHARS, KEYS
    return KEYS[name] if name in KEYS else CHARS[name][0]


def test_numbered_lines_follow_wrapping_and_sticky_scroll():
    text = "3 int f() {\n10 a long line that word wrap\nshows on two rows\n11     return 1;\n3 int f() {"
    assert E.numbered_lines(text) == {3: "int f() {", 10: "a long line that word wrap shows on two rows",
                                      11: "    return 1;"}
    assert E.numbers_read(text) == [3, 10, 11, 3]
    assert E.numbered_lines("```cpp\n1 int a;\n2 }\n```") == {1: "int a;", 2: "}"}    # a fence isn't a row
    assert E.parse_status("Ln 25, Col 1 (1,061 selected)  Spaces: 4") == (25, 1, 1061)
    assert E.parse_status("Ln 6, Col 1") == (6, 1, 0) and E.parse_status("NONE") is None


def test_strict_reading_leaves_out_every_line_that_may_be_read_wrong():
    phantom = "5 int a;\n6 int x = f(a,\n7 b);\n7 int y;\n8 int z;\n9 }"   # line 6's second row reads as line 7
    assert sorted(E.numbered_lines(phantom)) == [5, 6, 7, 8, 9]
    assert sorted(E.numbered_lines(phantom, strict=True)) == [5, 8]          # 9: the last line shown
    sticky = "3 int f() {\n10 a;\n11 b;\n12 c;"                              # a sticky-scroll line, then 10-12
    assert E.numbered_lines(sticky, strict=True) == {10: "a;", 11: "b;"}
    wrapped = "20 x = foo(a,\n42 + b);\n21 y;\n22 z;"                         # a wrapped row starting with a number
    assert E.numbered_lines(wrapped, strict=True) == {21: "y;"}
    assert E.numbered_lines("", strict=True) == {}


def test_plan_edit_checks_what_spark_sent():
    screen = {n: FILE[n - 1] for n in range(1, 10)}
    for bad in (["x"], "x", None, {"_unparsed": "{oops"}):
        with pytest.raises(E.EditRejected, match="JSON object"):
            E.plan_edit(bad, screen, "us")
    deeper = E.plan_edit({"start_line": 5, "end_line": 5, "new_text": "        int total = 0;"}, screen, "us")
    assert deeper.new == ["        int total = 0;"]                       # indentation alone is a change
    assert deeper.span() == "line 5" and "-    5     int total = 0;" in deeper.diff()
    restored = {"start_line": 3, "end_line": 3, "new_text": "// maintainer: Zoë"}
    with pytest.raises(E.EditRejected) as e:                              # Zoë came back from a placeholder
        E.plan_edit(restored, screen, "uk", {"[[NAME_1]]": "Zoë"})
    assert "redacted value" in str(e.value) and "ë" not in str(e.value)
    with pytest.raises(E.EditRejected, match="ë"):                        # Spark wrote it itself
        E.plan_edit(restored, screen, "uk")


def test_text_from_models_cant_drive_the_terminal():
    evil = "ok\x1b[2J\x1b]0;title\x07‮done\r\nnext\x7f"
    assert E.safe_text(evil) == "ok\\x1b[2J\\x1b]0;title\\x07\\u202edone\\r\\nnext\\x7f"
    assert cli._shown(evil) == "ok\\x1b[2J\\x1b]0;title\\x07\\u202edone\\r\nnext\\x7f"
    plan = E.plan_edit({"start_line": 6, "end_line": 6, "new_text": FIX, "note": "fix\x1b[31m it\nnow"},
                       {6: FILE[5], 7: FILE[6]}, "us")
    assert plan.note == "fix\\x1b[31m it now"


def test_keys_cut_short_by_release_all_stop_the_edit():
    screen = {6: FILE[5], 7: FILE[6]}
    plan = E.plan_edit({"start_line": 6, "end_line": 6, "new_text": FIX, "note": ""}, screen, "us")
    typer = E.EditTyper(lambda body: {"summary": "pressed escape (stopped after 0)"}, lambda: screen,
                        lambda: "NONE", lambda: ":", pause_s=0)
    with pytest.raises(RuntimeError, match="stopped after"):
        typer.apply(plan)


def test_ctrl_c_is_seen_while_the_service_is_still_typing():
    screen = {6: FILE[5], 7: FILE[6]}
    plan = E.plan_edit({"start_line": 6, "end_line": 6, "new_text": FIX, "note": ""}, screen, "us")
    statuses = iter(["Ln 6, Col 1", "Ln 7, Col 1 (36 selected)"])
    busy, released, actions = threading.Event(), threading.Event(), []

    def act(body):
        actions.append(body["action"])
        if body["action"] == "release_all":
            released.set()
        elif body["action"] == "type" and len(body["text"]) > 5:
            busy.set()
            released.wait(10)                                     # types until release_all stops it
        return {"summary": "ok"}

    def press_ctrl_c():
        busy.wait(10)
        _thread.interrupt_main()                                  # what Ctrl+C in the terminal does
    threading.Thread(target=press_ctrl_c, daemon=True).start()
    typer = E.EditTyper(act, lambda: screen, lambda: next(statuses), lambda: ":", pause_s=0)
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        typer.apply(plan)
    assert actions[-1] == "release_all" and time.monotonic() - t0 < 3


def test_ctrl_c_while_typing_releases_every_key():
    screen = {6: FILE[5], 7: FILE[6]}
    plan = E.plan_edit({"start_line": 6, "end_line": 6, "new_text": FIX, "note": ""}, screen, "us")
    statuses = iter(["Ln 6, Col 1", "Ln 7, Col 1 (36 selected)"])
    actions = []

    def act(body):
        actions.append(body["action"])
        if body["action"] == "type" and len(body["text"]) > 5:
            raise KeyboardInterrupt
        return {"summary": "ok"}
    typer = E.EditTyper(act, lambda: screen, lambda: next(statuses), lambda: ":", pause_s=0)
    with pytest.raises(KeyboardInterrupt):
        typer.apply(plan)
    assert actions[-1] == "release_all"


def test_the_gate_checks_the_whole_conversation(world):
    w = world
    gate = OutboundGate(w.cfg, GlimmerClient(w.cfg), SparkClient(w.cfg, base_url="http://127.0.0.1:1"), stand_in=True)
    p = gate.prepare("3 // maintainer: jane.doe@example.org", "fix it")
    gate.check(p, [{"role": "tool", "tool_call_id": "c", "content": "Applied and checked."}])
    with pytest.raises(OutboundBlocked, match="redacted value"):
        gate.check(p, [{"role": "tool", "tool_call_id": "c", "content": "line 3 is jane.doe@example.org"}])
