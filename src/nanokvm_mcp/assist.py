"""The live viewer's prompt box: ask about the locked computer's screen, then, if you want, have the task done there.

A question is answered by Muse Spark from Glimmer's redacted reading of the screen (Glimmer is asked to describe
the window in front, or to transcribe the code with its line numbers), through the same outbound gate as ``nanokvm
ask``, or by Glimmer alone when "Local only" is ticked, with nothing sent. Fix it does a task one step at a time
(acting.py), and hands a change to code over to ``nanokvm fix``'s loop (acting.FIX_CODE), with its planner's model
as the coder: Glimmer on this laptop by default (localcoder.py), or Spark with "Fix it: Spark plans and fixes code"
ticked.

Nothing is read, sent or typed until a question is asked or Fix it is pressed. Every approval the terminal
commands ask for at the keyboard is asked in the window instead, as an Approval the window shows and the person
answers with a click: sharing the screen with Glimmer (for SHARE_MINUTES, asked again when the service's share
runs low or is gone), the free-credit test-content confirmation (with every share that can lead to Spark), the
exact text before it is sent (outbound = "approve", or ticked in the window), each step, each edit and the save
(with Auto: the steps that don't type or change anything, then a fix's edits and its save in one approval that
names them). Choices in the window can make nanokvm.toml's settings stricter, never looser. Work runs on one worker
thread, so the live picture keeps moving; Stop, and closing the window, release every key.
"""
from __future__ import annotations

import dataclasses
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import acting as A
from . import editing as E
from .client import ServiceError
from .config import Config
from .glimmer import GlimmerError
from .outbound import OutboundBlocked, OutboundGate
from .pipeline import AskResult, EditError, EditResult, Pipeline
from .spark import SparkError

SHARE_MINUTES = 30              # one share approval covers this long
ASK_MARGIN_S = 60               # a question shares again when less than this is left...
TASK_MARGIN_S = 10 * 60         # ...and Fix it when less than this (a task takes minutes; nanokvm fix shares 10 min)
READ_KINDS = {"window": "the window in front", "code": "the code, with its line numbers"}


class NotApproved(Exception):
    """The person said no before anything was read or sent."""


@dataclass
class Approval:
    """A yes/no the person gives in the window. The worker waits for it; closing the window or Stop is a no."""
    kind: str                   # share, test, send, auto, step, edit or save
    title: str
    text: str                   # what is approved, in full
    yes: str                    # the label of the button that approves
    no: str
    mono: bool = False          # show the text in a fixed-width font (diffs, the exact text sent)
    answer: bool | None = None
    done: threading.Event = field(default_factory=threading.Event, repr=False)


def ask_report(res: AskResult, cfg: Config) -> str:
    """The line under an answer: what was sent, redacted and paid, as ``nanokvm ask`` prints it."""
    if res.local_only:
        return "(answered by Muse Glimmer on this laptop; nothing was sent)"
    p, sent = res.prepared, res.sent
    found = ", ".join(f"{k} x{v}" for k, v in sorted(p.findings.items())) or "nothing found"
    return (f"(sent {len(p.text)} characters to {sent.reply.model}, store=false; redacted: {found}; "
            f"{'trimmed by Glimmer to the lines needed' if p.trimmed else 'not trimmed'}; "
            f"cost ${sent.reply.cost_usd:.4f}; logged in {cfg.run_dir / 'outbound.log'})")


def fix_report(res: EditResult, cfg: Config) -> list[tuple[str, str]]:
    """The closing lines of a fix, as ``nanokvm fix`` prints them, each with how to show it: "" (plain), "ok" (the
    save, checked on the screen) or "err". Only nanokvm's own lines get a colour, never the coder's text."""
    lines: list[tuple[str, str]] = []
    for rec in res.edits:
        if rec.plan is None:
            lines.append((f"- not shown: {rec.told_spark}", ""))
        elif not rec.approved:
            lines.append((f"- {rec.plan.span()}: declined", ""))
        else:
            lines.append((f"- {rec.plan.span()}: {rec.outcome.status} ({rec.outcome.detail})", ""))
    if res.summary:
        lines.append((E.safe_text(res.summary, multiline=True), ""))
    if res.stopped:
        lines.append((f"Stopped early: {E.safe_text(res.stopped)}", "err"))
    typed = sum(1 for r in res.edits if r.outcome is not None and r.outcome.status != "not_applied")
    checked = sum(1 for r in res.edits if r.outcome and r.outcome.status == "verified")
    who = res.coder.removeprefix("Muse ")
    rechecked = f"; {who} checked the fix on {res.checks} fresh reading(s) of the screen" if res.checks else ""
    sent = (f"{res.turns} request(s) to Muse Glimmer on this laptop; nothing was sent" if res.local else
            f"{res.turns} request(s) to {cfg.spark_model}, store=false, cost ${res.cost_usd:.4f}; logged in "
            f"{cfg.run_dir / 'outbound.log'}")
    lines.append((f"({typed} edit(s) typed, {checked} checked on the screen{rechecked}; {sent})", ""))
    if res.save is not None:
        lines.append((res.save.report(), "ok" if res.save.status == "saved" else
                      "" if res.save.status in ("declined", "skipped") else "err"))
    elif typed:
        lines.append(("nanokvm saved nothing (keep VS Code's auto-save off): review the changes in VS Code before you "
                      "save. Ctrl+Z, or File: Revert File, undoes them.", ""))
    return lines


class AssistSession:
    """The prompt box's work, without the window: ``post`` hands the window events as (kind, payload) tuples, from
    the worker thread. Kinds: say (progress), question, answer (text, report line), edit (an edit typed with Auto,
    shown in full), report ((text, tag) lines), info, error, approve (an Approval to show), target (a step's target
    box to outline on the picture, or None to clear it), idle (the work is over). The window answers an Approval
    with ``answer``."""

    def __init__(self, cfg: Config, post: Callable[[tuple[str, object]], None], *,
                 make_pipeline: Callable[..., Pipeline] | None = None) -> None:
        self.base_cfg = cfg
        self.post = post
        self._make = make_pipeline or (lambda c, **kw: Pipeline(c, **kw))
        self.pipe: Pipeline | None = None
        self.shared_until = 0.0              # monotonic time the share ends, as the service said at the last job
        self.test_confirmed = False          # free credits: confirmed with the current share
        self.used_keys = False               # keys were allowed at some point: release every key on close
        self.job_typed = False               # this job's keys were allowed: Stop says stop_note, not just "Stopped."
        self.stop_note = ""                  # what Stop says once keys went out (each job sets its own)
        self.steps_done = 0                  # Fix it: steps actually done in this task
        self.stop_event = threading.Event()
        self.closed = False
        self.pending: Approval | None = None
        self._share_granted = False          # set by the person's yes, used up by the pipeline's share check
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- what the window can do
    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def shared_minutes_left(self) -> int:
        """For the window's status line: the clock's estimate, so it never waits on the service."""
        return max(0, int((self.shared_until - time.monotonic()) // 60)) if self.shared_until else 0

    def ask(self, question: str, kind: str = "window", local_only: bool = False, show_first: bool = False) -> bool:
        """Start answering ``question``; False if work is already running or the question is empty."""
        question = (question or "").strip()
        if not question or kind not in READ_KINDS:
            return False
        return self._start(lambda: self._ask(question, kind, local_only, show_first))

    def do(self, task: str, auto: bool = False, spark: bool = False, show_first: bool = False) -> bool:
        """Start Fix it: ``task`` in any program, one step at a time (acting.py). Glimmer plans unless ``spark``."""
        task = (task or "").strip()
        if not task:
            return False
        return self._start(lambda: self._do(task, auto, spark, show_first))

    def answer(self, approval: Approval, yes: bool) -> None:
        approval.answer = bool(yes)
        approval.done.set()

    def stop(self) -> None:
        """Stop the work at the next step; typing stops between keys, with every key released."""
        self.stop_event.set()
        pending = self.pending
        if pending is not None:
            self.answer(pending, False)

    def close(self, wait: float = 3.0) -> None:
        """The window is closing: stop, release every key if any keys were allowed, and end the share. Nothing here
        starts a stopped service (it would hold no keys and no share) or waits long for one."""
        self.closed = True
        self.stop()
        try:
            worker = self._worker
            if worker is not None and worker.is_alive():
                worker.join(wait)
        finally:
            pipe = self.pipe
            if pipe is not None:
                if self.used_keys:
                    try:
                        pipe.svc.request("POST", "/api/act", {"action": "release_all"}, timeout=5.0)
                    except Exception:  # noqa: BLE001  (best effort: the service may be gone)
                        pass
                try:
                    pipe.close(quick=True)
                except Exception:  # noqa: BLE001
                    pass

    # ---- the worker
    def _start(self, job: Callable[[], None]) -> bool:
        with self._lock:
            if self.closed or self.busy:
                return False
            self.stop_event.clear()
            self.job_typed = False
            self.steps_done = 0
            self._worker = threading.Thread(target=self._run, args=(job,), name="viewer-assist", daemon=True)
            self._worker.start()
            return True

    def _run(self, job: Callable[[], None]) -> None:
        try:
            job()
        except NotApproved:
            self.post(("info", "Not approved: nothing was read, sent or typed."))
        except KeyboardInterrupt:
            self.post(("info", self.stop_note if self.job_typed else "Stopped."))
        except EditError as e:
            self.post(("error", f"Nothing was sent or typed: {e}"))
        except OutboundBlocked as e:
            self.post(("error", f"Stopped, nothing more was sent: {e}"))
        except (GlimmerError, SparkError, ServiceError) as e:
            self.post(("error", f"{type(e).__name__}: {e}"))
        except Exception as e:  # noqa: BLE001  (the window must hear about anything else too)
            self.post(("error", f"{type(e).__name__}: {e}"))
        finally:
            self.pending = None
            self.post(("idle", None))

    def _say(self, message: str) -> None:
        """Progress for the window; also where Stop takes effect between steps."""
        if self.stop_event.is_set():
            raise KeyboardInterrupt
        self.post(("say", E.safe_text(message, multiline=True)))

    def _confirm(self, approval: Approval) -> bool:
        """Show ``approval`` in the window and wait for the click. Stop or closing the window is a no."""
        if self.stop_event.is_set() or self.closed:
            raise KeyboardInterrupt
        self.pending = approval
        self.post(("approve", approval))
        while not approval.done.wait(0.2):
            if self.stop_event.is_set() or self.closed:
                approval.answer = False
                break
        self.pending = None
        if self.stop_event.is_set():
            raise KeyboardInterrupt
        return bool(approval.answer)

    def _cfg_for(self, local_only: bool, show_first: bool) -> Config:
        """This job's settings: the window's choices can only make nanokvm.toml's stricter."""
        mode = self.base_cfg.outbound
        if local_only:
            mode = "local_only"
        elif show_first and mode == "auto":
            mode = "approve"
        return dataclasses.replace(self.base_cfg, outbound=mode)

    def _pipeline(self) -> Pipeline:
        if self.pipe is None:
            self.pipe = self._make(self.base_cfg, approve_share=self._use_share_grant, approve_send=self._approve_send,
                                   stand_in=False)
        return self.pipe

    def _use_share_grant(self, _minutes: int) -> bool:
        """The pipeline's own share check: yes only right after the person's yes in the window, once."""
        granted, self._share_granted = self._share_granted, False
        return granted

    def _prepare(self, cfg: Config, extra: list[str], margin_s: float = ASK_MARGIN_S) -> Pipeline:
        """Glimmer must answer; then one approval covers whatever is still needed: the share (when the service's
        share for this window has less than ``margin_s`` left, or is gone, as after a restart of the service), the
        free-credit test-content confirmation (with every share, before anything goes to Spark) and ``extra``
        (what Auto will do)."""
        pipe = self._pipeline()
        if not pipe.glimmer.health():
            raise GlimmerError(f"Muse Glimmer isn't ready at {pipe.glimmer.url}: start tools\\run-glimmer.ps1 and "
                               "wait until it has loaded. Nothing was shared.")
        left = pipe.share_seconds_left()
        self.shared_until = time.monotonic() + left if left else 0.0
        need_share = left < margin_s
        if need_share:                                  # a new share: the test-content answer goes with it
            self.test_confirmed = False
            pipe.stand_in = False
        spark = cfg.outbound != "local_only"
        need_test = spark and cfg.meta_billing == "free_credits" and not self.test_confirmed
        parts = []
        if need_share:
            parts.append(f"Let Muse Glimmer on this laptop read the locked computer's screen for {SHARE_MINUTES} min, "
                         "for what you ask in this window.")
        if need_test:
            parts.append("Meta billing is on free credits and Meta may use what is sent, so say yes only if the screen "
                         "shows test or dummy content.")
        parts += extra
        if parts:
            ok = self._confirm(Approval("share" if need_share else "test" if need_test else "auto",
                                        "Before anything is read" if need_share else "Before this goes ahead",
                                        "\n\n".join(parts), "Yes, go ahead", "No"))
            if not ok:
                raise NotApproved
        if need_share:
            pipe.close(quick=True)                      # a share running low, or lost: start afresh
            self._share_granted = True
            pipe.open(SHARE_MINUTES)
            self.shared_until = time.monotonic() + SHARE_MINUTES * 60
        if need_test:
            self.test_confirmed = True
            pipe.stand_in = True
        pipe.cfg = cfg
        return pipe

    def _approve_send(self, text: str) -> bool:
        return self._confirm(Approval("send", "Send exactly this to Muse Spark (Meta)?", E.safe_text(text, multiline=True),
                                      "Send", "Don't send", mono=True))

    # ---- a question
    def _ask(self, question: str, kind: str, local_only: bool, show_first: bool) -> None:
        cfg = self._cfg_for(local_only, show_first)
        self.post(("question", f"{E.safe_text(question, multiline=True)}\n(reading {READ_KINDS[kind]}; "
                               f"{'Glimmer answers, nothing is sent' if cfg.outbound == 'local_only' else 'Spark answers from the redacted reading'})"))
        pipe = self._prepare(cfg, [])
        res = pipe.ask(question, None, kind, on_event=self._say)
        frame_warnings = res.reading.frame.get("warnings") or []
        self.post(("answer", (E.safe_text(res.answer, multiline=True),
                              "\n".join([*("WARNING: " + w for w in frame_warnings), ask_report(res, cfg)]))))

    def _typing_allowed(self) -> bool:
        """An edit, or the save, was allowed: from now on Stop says to check the file (not before: a Stop at the
        hand-over leaves the file as it was)."""
        self.used_keys = self.job_typed = True
        self.stop_note = "Stopped: every key was released. Check the file on the locked computer."
        return True

    # ---- Fix it: a task in any program
    def _do(self, task: str, auto: bool, spark: bool, show_first: bool) -> None:
        if not self.base_cfg.control:
            raise EditError("keyboard/mouse control is off (control = false in nanokvm.toml)")
        use_spark = spark and self.base_cfg.outbound != "local_only"
        cfg = self._cfg_for(not use_spark, show_first)
        planner = ("Muse Spark plans from the redacted reading" if use_spark else
                   "Muse Glimmer plans on this laptop; nothing leaves it")
        self.post(("question", f"Fix it: {E.safe_text(task, multiline=True)}\n({planner}"
                               + ("; Auto: a step that types or changes something still asks" if auto else "") + ")"))
        self.stop_note = "Stopped: every key was released. Check what the last step did on the locked computer."
        extra = ([f"Do this task on the locked computer without asking for each step: {E.safe_text(task)!r}. Each step "
                  "is still shown, with its target outlined on the picture, a moment before it is done; a step that "
                  "types or changes something still asks, and so does a fix to code, once, before typing its edits; "
                  "Stop ends it."] if auto else [])
        pipe = self._prepare(cfg, extra, margin_s=TASK_MARGIN_S)
        width, height = pipe.screen_size()
        self.steps_done = 0
        if use_spark:
            self._do_with_spark(pipe, cfg, task, auto, width, height)
        else:
            self._do_locally(pipe, task, auto, width, height)

    def _steps_line(self, sent: str) -> tuple[str, str]:
        return (f"({self.steps_done} step(s) done; {sent})", "")

    def _do_locally(self, pipe: Pipeline, task: str, auto: bool, width: int, height: int) -> None:
        """Glimmer looks at the screen, plans the next step and finds its target: nothing leaves this laptop."""
        history: list[str] = []
        nothing = "planned on this laptop, nothing was sent"
        for n in range(1, A.MAX_STEPS + 1):
            self._say(f"Muse Glimmer looks at the screen and plans step {n}")

            def plan_and_find(b64: str, fmt: str):
                reply = pipe.glimmer.plan_step(b64, fmt, A.PLAN_PROMPT.format(
                    task=task, history="\n".join(history) or "none yet"))
                kind, step = A.parse_plan(reply.content)
                if kind == "step" and step.action in A.POINTED and step.target:
                    step.box, step.missing = A.parse_box(pipe.glimmer.locate(b64, fmt, A.GROUND_PROMPT.format(
                        target=step.target)).content)
                return kind, step
            try:
                kind, step = pipe.look(plan_and_find)
            except (GlimmerError, ServiceError) as e:
                if not self.steps_done:
                    raise
                self.post(("report", [(f"Stopped early: {type(e).__name__}: {e}", "err"), self._steps_line(nothing)]))
                return
            if kind == A.FIX_CODE:
                self._do_fix(pipe, task, step, auto, local=True)
                return
            if kind in ("done", "stuck"):
                verdict = ("Muse Glimmer says the task is done: check it on the screen." if kind == "done" else
                           "Muse Glimmer is stuck: it can't see how to go on from this screen.")
                self.post(("report", [(verdict, "ok" if kind == "done" else "err"),
                                      (E.safe_text(step) or "(no reason given)", ""), self._steps_line(nothing)]))
                return
            result = self._one_step(pipe, step, auto, n, width, height)
            history.append(A.history_line(n, step, result))
            if result == "declined":
                self.post(("report", [(f"Stopped: you said no to step {n}.", ""), self._steps_line(nothing)]))
                return
        self.post(("report", [(f"Stopped after {A.MAX_STEPS} steps: the task isn't done yet.", "err"),
                              self._steps_line(nothing)]))

    def _do_with_spark(self, pipe: Pipeline, cfg: Config, task: str, auto: bool, width: int, height: int) -> None:
        """Spark plans from Glimmer's redacted description of the window; Glimmer still finds every target here."""
        self._say("Muse Glimmer describes the window in front")
        reading = pipe.read("ui")
        gate = OutboundGate(cfg, pipe.glimmer, pipe.spark(), approve=self._approve_send, stand_in=pipe.stand_in,
                            trusted=A.TOLD_TRUSTED)
        prepared = gate.prepare(reading.text, task, reading.frame.get("image_sha256"), trim=False)
        history: list[dict] = []
        cost, turns, n = 0.0, 0, 0

        def report(*first: tuple[str, str]) -> None:
            self.post(("report", [*first, self._steps_line(f"{turns} request(s) to {cfg.spark_model}, store=false, "
                                                           f"cost ${cost:.4f}; logged in {cfg.run_dir / 'outbound.log'}")]))
        try:
            while turns < A.MAX_ACT_TURNS:
                self._say("asking Muse Spark for the next step")
                answer = gate.ask(prepared, A.ACT_SYSTEM, tools=[A.UI_STEP], parallel_tool_calls=False,
                                  history=A.keep_newest_reading(history))
                turns += 1
                cost += answer.reply.cost_usd
                if not answer.tool_calls:
                    text = E.safe_text(answer.content or "", multiline=True).strip()
                    reply = answer.reply
                    if reply.refusal:
                        report((f"Muse Spark refused: {E.safe_text(reply.refusal)}", "err"))
                    elif reply.finish_reason in ("length", "content_filter"):
                        report((f"Muse Spark's reply was cut off (finish_reason {reply.finish_reason!r})", "err"))
                    elif text.upper().startswith("DONE"):         # nanokvm's own line gets the colour, never Spark's
                        report(("Muse Spark says the task is done: check it on the screen.", "ok"), (text, ""))
                    else:
                        report((text or "Muse Spark sent an empty reply", ""))
                    return
                history.append({"role": "assistant", "content": answer.reply.content or None,
                                "tool_calls": [{"id": c["id"], "type": "function",
                                                "function": {"name": c["name"], "arguments": c["arguments"]}}
                                               for c in answer.reply.tool_calls]})
                call = answer.tool_calls[0]
                told = A.TOLD_UNKNOWN_TOOL
                args = call["arguments"] if isinstance(call["arguments"], dict) else {}
                if call["name"] == "ui_step" and A.action_name(args.get("action")) == A.FIX_CODE:
                    self._do_fix(pipe, task, str(args.get("note") or ""), auto, local=False,
                                 planned=f"planning: {turns} request(s) to {cfg.spark_model}, store=false, cost "
                                         f"${cost:.4f}; logged in {cfg.run_dir / 'outbound.log'}")
                    return
                if call["name"] == "ui_step":
                    try:
                        step = A.step_from_args(call["arguments"])
                    except A.StepRejected as e:
                        told = f"{A.TOLD_NOT_DONE}{e}."
                    else:
                        if step.action in A.POINTED and step.target:
                            step.box, step.missing = A.parse_box(pipe.look(lambda b64, fmt: pipe.glimmer.locate(
                                b64, fmt, A.GROUND_PROMPT.format(target=step.target))).content)
                        n += 1
                        result = self._one_step(pipe, step, auto, n, width, height)
                        if result == "declined":
                            report((f"Stopped: you said no to step {n}.", ""))
                            return
                        told = A.TOLD_DONE if result == "done" else f"{A.TOLD_NOT_DONE}{result.removeprefix('not done: ')}."
                        self._say("Muse Glimmer describes the window in front again")
                        told += A.TOLD_READ_AGAIN + gate.screen_blocks(prepared, pipe.read("ui").text)
                history.append({"role": "tool", "tool_call_id": call["id"], "content": told})
                for other in answer.tool_calls[1:]:
                    history.append({"role": "tool", "tool_call_id": other["id"], "content": A.TOLD_ONE_STEP})
                if n >= A.MAX_STEPS:
                    report((f"Stopped after {A.MAX_STEPS} steps: the task isn't done yet.", "err"))
                    return
            report((f"Stopped after {A.MAX_ACT_TURNS} requests to Spark.", "err"))
        except (OutboundBlocked, SparkError, GlimmerError, ServiceError) as e:
            if not self.steps_done:
                raise                                        # nothing was done: the usual error line
            report((f"Stopped early: {type(e).__name__}: {e}", "err"))

    def _do_fix(self, pipe: Pipeline, task: str, why: str, auto: bool, local: bool, planned: str = "") -> None:
        """Fix it's planner says the task is a change to code in an editor with line numbers: nanokvm fix's loop does
        it, with the task as its request (E.TASK_RULES) and the planner's model as the coder, and that ends the task.
        Code is edited by line number and each edit checked on the screen, not clicked into and typed. Each edit and
        the save ask; with Auto, one approval naming them both covers them instead (a no means asking for each).
        ``planned``: what planning sent (Spark), for the closing line."""
        who = "Glimmer" if local else "Spark"
        self._say(f"Muse {who} says this is a change to code" + (f" ({E.safe_text(why)})" if why else "")
                  + f": Muse {who} edits it by line number, each edit shown first and checked on the screen. The task "
                    "ends there: ask again for anything after the change to code")
        if auto:
            auto = self._confirm(Approval(
                "auto", "Type the fix without asking for each edit?",
                f"Type Muse {who}'s edits into VS Code on the locked computer without asking for each one, and save "
                f"the file once {who} has checked the fix on a fresh reading of the screen. Each edit is still shown "
                "and checked on the screen; Stop releases every key.", "Type and save without asking",
                "Ask for each edit"))
        steps = planned or "planned on this laptop, nothing was sent"
        try:
            res = pipe.edit(None, approve_edit=self._approve_edit(auto, who), on_event=self._say,
                            approve_save=self._approve_save(auto, who), task=task, stopped=self.stop_event.is_set,
                            local=local)
        except (EditError, OutboundBlocked, GlimmerError, SparkError, ServiceError) as e:
            if not (self.steps_done or self.job_typed or planned):
                raise                                        # nothing was done, typed or sent: the usual error line
            self.post(("report", [(f"Stopped early: the fix loop: {type(e).__name__}: {e}", "err"),
                                  self._steps_line(steps)]))
            return
        lines = fix_report(res, pipe.cfg)
        if self.steps_done or planned:
            lines.append(self._steps_line(steps))
        self.post(("report", lines))

    def _one_step(self, pipe: Pipeline, step: A.Step, auto: bool, n: int, width: int, height: int) -> str:
        """Check one step, show it (its target outlined on the picture), get the yes (Auto: only for a step that types
        or changes something; the others are shown AUTO_SHOW_S before they are done) and do it. What happened, in
        words without screen text: done, declined, or not done."""
        mac = (self.base_cfg.locked_os or "").strip().lower() == "macos"
        try:
            A.check_step(step, self.base_cfg.keyboard_layout, mac)
        except A.StepRejected as e:
            self._say(f"step {n} can't be done: {e}")
            return f"not done: {e}"
        if step.action in A.POINTED and step.box is None:
            self._say(f"step {n}: {E.safe_text(step.describe())}: {step.missing or 'no target found'}")
            return f"not done: {step.missing or 'the screen reader could not find that on the screen'}"
        shown = E.safe_text(step.describe()) + (" (outlined in orange on the picture)" if step.box else "")
        if step.why:
            shown += f"\nWhy: {E.safe_text(step.why)}"
        if step.changes_something:
            shown += "\nThis step changes something."
        if step.action == "type":                            # always asked: say where it will go
            shown += "\n" + self._cursor_note(pipe)
        if step.box:
            look = pipe.last_look or {}
            outline = (A.frame_box(step.box, look["capture_rect"], look["frame_size"])
                       if look.get("capture_rect") and look.get("frame_size") else step.box)
            self.post(("target", outline))
        try:
            if auto and not step.risky:
                self._say(f"Auto: step {n}: {E.safe_text(step.describe())}")
                if self.stop_event.wait(A.AUTO_SHOW_S):      # seen before it is done; Stop still works
                    raise KeyboardInterrupt
            elif not self._confirm(Approval("step", f"Step {n}: do this?", shown, "Do this step", "Stop here")):
                return "declined"
            self.used_keys = self.job_typed = True
            reply = self._act(pipe, A.act_body(step, width, height))
            summary = str(reply.get("summary", ""))
            if "(note:" in summary:                          # the keyboard wake-up couldn't confirm the keys
                self._say(summary[summary.index("(note:"):])
            time.sleep(1.5)                                  # let the screen settle before the next look
            if "STOPPED" in summary or "stopped after" in summary:
                self._say(f"step {n} not done: the keys didn't all go through")
                return "not done: the keys didn't all go through"
            self.steps_done += 1
            self._say(f"step {n} done: {E.safe_text(step.describe())}")    # the log keeps what was done
            return "done"
        finally:
            if step.box:
                self.post(("target", None))

    def _cursor_note(self, pipe: Pipeline) -> str:
        """Where typed text goes: the cursor position from VS Code's status bar (Glimmer reads the bottom of the
        screen), as on 1 Nov, when 'using namespace std;' went to line 8 instead of line 2; or a reminder to look."""
        try:
            st = E.parse_status(pipe.read_status())
        except (GlimmerError, ServiceError):
            st = None
        if st is None:
            return "It types wherever the cursor is: check where that is on the picture."
        sel = f", with {st[2]} characters selected (they will be replaced)" if st[2] else ""
        return f"It types where the cursor is: the editor's status bar shows Ln {st[0]}, Col {st[1]}{sel}."

    def _act(self, pipe: Pipeline, body: dict) -> dict:
        """One request to the service on a worker thread, so Stop is seen at once, even while it types."""
        box: dict = {}

        def run() -> None:
            try:
                box["reply"] = pipe.svc.act(body)
            except BaseException as e:  # noqa: BLE001  (handed back to the worker)
                box["error"] = e
        worker = threading.Thread(target=run, name="viewer-act", daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.2)
            if self.stop_event.is_set():
                pipe.svc.act({"action": "release_all"})     # stops typing between keystrokes, every key up
                raise KeyboardInterrupt
        if "error" in box:
            raise box["error"]
        return box.get("reply") or {}

    def _approve_edit(self, auto: bool, who: str = "Spark") -> Callable[[E.EditPlan], bool]:
        """Each edit the coder (``who``) proposes, shown in full: typed after a yes, or with Auto without asking."""
        def approve(plan: E.EditPlan) -> bool:
            shown = "\n".join([plan.diff(), "", "Keys sent to the locked computer:",
                               *(f"  {i}. {s}" for i, s in enumerate(plan.steps(), 1)),
                               f"{who}'s note: {plan.note or '(none)'}", "",
                               "VS Code on the locked computer must have the file focused, with its status bar "
                               "showing. Don't use its keyboard or mouse while the edit is typed; Stop releases "
                               "every key."])
            if auto:
                self.post(("edit", f"{who}'s edit, {plan.span()} (Auto: typing it without asking):\n{shown}"))
                return self._typing_allowed()
            if self._confirm(Approval("edit", f"Type {who}'s edit of {plan.span()}?", shown, "Type this edit",
                                      "Don't type", mono=True)):
                return self._typing_allowed()
            return False
        return approve

    def _approve_save(self, auto: bool, who: str = "Spark") -> Callable[[str], bool]:
        def approve(name: str) -> bool:
            if auto:
                self._say(f"Auto: {who} checked the fix on the screen, so saving {name} without asking")
                return self._typing_allowed()
            if self._confirm(Approval("save", f"Save {name}?", f"{who} checked the fix on the screen. Save {name} "
                                      "in VS Code on the locked computer?", "Save", "Don't save")):
                return self._typing_allowed()
            return False
        return approve


class EventQueue:
    """The worker's events for the window: put from any thread, taken on the window's own thread."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()

    def put(self, event: tuple[str, object]) -> None:
        self._q.put(event)

    def take(self) -> list[tuple[str, object]]:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out
