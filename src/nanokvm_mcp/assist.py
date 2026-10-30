"""The live viewer's prompt box: ask about the locked computer's screen, then, if you want, fix it from the window.

A question is answered by Muse Spark from Glimmer's redacted reading of the screen (Glimmer is asked to describe
the window in front, or to transcribe the code with its line numbers), through the same outbound gate as ``nanokvm
ask``, or by Glimmer alone when "Local only" is ticked, with nothing sent. "Fix it" runs ``nanokvm fix``'s loop,
with the last question that went to Spark as a hint.

Nothing is read, sent or typed until a question is asked or Fix it is pressed. Every approval the terminal
commands ask for at the keyboard is asked in the window instead, as an Approval the window shows and the person
answers with a click: sharing the screen with Glimmer (for SHARE_MINUTES, asked again when the service's share
runs low or is gone), the free-credit test-content confirmation (with every share that can lead to Spark), the
exact text before it is sent (outbound = "approve", or ticked in the window), each edit and the save (or both at
once with Auto, in one approval that names them). Choices in the window can make nanokvm.toml's settings
stricter, never looser. Work runs on one worker thread, so the live picture keeps moving; Stop, and closing the
window, release every key.
"""
from __future__ import annotations

import dataclasses
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import editing as E
from .client import ServiceError
from .config import Config
from .glimmer import GlimmerError
from .outbound import OutboundBlocked
from .pipeline import AskResult, EditError, EditResult, Pipeline
from .spark import SparkError

SHARE_MINUTES = 30              # one share approval covers this long
ASK_MARGIN_S = 60               # a question shares again when less than this is left...
FIX_MARGIN_S = 10 * 60          # ...and Fix it when less than this (nanokvm fix's own share is 10 min)
READ_KINDS = {"window": "the window in front", "code": "the code, with its line numbers"}


class NotApproved(Exception):
    """The person said no before anything was read or sent."""


@dataclass
class Approval:
    """A yes/no the person gives in the window. The worker waits for it; closing the window or Stop is a no."""
    kind: str                   # share, test, send, auto, edit or save
    title: str
    text: str                   # what is approved, in full
    yes: str                    # the label of the button that approves
    no: str
    mono: bool = False          # show the text in a fixed-width font (diffs, the exact text sent)
    answer: bool | None = None
    done: threading.Event = field(default_factory=threading.Event, repr=False)


def fix_blocked_reason(cfg: Config) -> str | None:
    """Why Fix it can't run with these nanokvm.toml settings, or None."""
    if not cfg.control:
        return "keyboard/mouse control is off (control = false in nanokvm.toml)"
    if cfg.outbound == "local_only":
        return "it needs Muse Spark, but outbound = local_only in nanokvm.toml"
    return None


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
    save, checked on the screen) or "err". Only nanokvm's own lines get a colour, never Spark's text."""
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
    rechecked = f"; Spark checked the fix on {res.checks} fresh reading(s) of the screen" if res.checks else ""
    lines.append((f"({typed} edit(s) typed, {checked} checked on the screen{rechecked}; {res.turns} request(s) to "
                  f"{cfg.spark_model}, store=false, cost ${res.cost_usd:.4f}; logged in "
                  f"{cfg.run_dir / 'outbound.log'})", ""))
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
    shown in full), report ((text, tag) lines), info, error, approve (an Approval to show), idle (the work is over).
    The window answers an Approval with ``answer``."""

    def __init__(self, cfg: Config, post: Callable[[tuple[str, object]], None], *,
                 make_pipeline: Callable[..., Pipeline] | None = None) -> None:
        self.base_cfg = cfg
        self.post = post
        self._make = make_pipeline or (lambda c, **kw: Pipeline(c, **kw))
        self.pipe: Pipeline | None = None
        self.shared_until = 0.0              # monotonic time the share ends, as the service said at the last job
        self.test_confirmed = False          # free credits: confirmed with the current share
        self.used_keys = False               # typing was approved at some point: release every key on close
        self.job_typed = False               # this job's typing was approved: Stop says to check the file
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

    def can_fix(self) -> str | None:
        """Why Fix it can't run with these settings, or None."""
        return fix_blocked_reason(self.base_cfg)

    def shared_minutes_left(self) -> int:
        """For the window's status line: the clock's estimate, so it never waits on the service."""
        return max(0, int((self.shared_until - time.monotonic()) // 60)) if self.shared_until else 0

    def ask(self, question: str, kind: str = "window", local_only: bool = False, show_first: bool = False) -> bool:
        """Start answering ``question``; False if work is already running or the question is empty."""
        question = (question or "").strip()
        if not question or kind not in READ_KINDS:
            return False
        return self._start(lambda: self._ask(question, kind, local_only, show_first))

    def fix(self, hint: str = "", auto: bool = False, show_first: bool = False) -> bool:
        """Start the fix loop, with ``hint`` (the person's question) added to Spark's request."""
        return self._start(lambda: self._fix((hint or "").strip(), auto, show_first))

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
        """The window is closing: stop, release every key if typing was approved, and end the share. Nothing here
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
            self._worker = threading.Thread(target=self._run, args=(job,), name="viewer-assist", daemon=True)
            self._worker.start()
            return True

    def _run(self, job: Callable[[], None]) -> None:
        try:
            job()
        except NotApproved:
            self.post(("info", "Not approved: nothing was read, sent or typed."))
        except KeyboardInterrupt:
            self.post(("info", "Stopped: every key was released. Check the file on the locked computer."
                       if self.job_typed else "Stopped."))
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

    # ---- the fix loop
    def _fix(self, hint: str, auto: bool, show_first: bool) -> None:
        why = self.can_fix()
        if why:
            raise EditError(why)
        cfg = self._cfg_for(False, show_first)
        self.post(("question", "Fix it" + (f", with your question: {E.safe_text(hint, multiline=True)}" if hint else "")
                   + ("\n(Auto: edits typed and the file saved without asking for each)" if auto else "")))
        extra = (["Type Muse Spark's edits into VS Code on the locked computer without asking for each one, and save "
                  "the file once Spark has checked the fix on the screen. Each edit is still shown and checked on the "
                  "screen; Stop releases every key."] if auto else [])
        pipe = self._prepare(cfg, extra, margin_s=FIX_MARGIN_S)
        res = pipe.edit(None, approve_edit=self._approve_edit(auto), on_event=self._say,
                        approve_save=self._approve_save(auto), hint=hint or None, stopped=self.stop_event.is_set)
        self.post(("report", fix_report(res, cfg)))

    def _typing_allowed(self) -> bool:
        self.used_keys = self.job_typed = True
        return True

    def _approve_edit(self, auto: bool) -> Callable[[E.EditPlan], bool]:
        def approve(plan: E.EditPlan) -> bool:
            shown = "\n".join([plan.diff(), "", "Keys sent to the locked computer:",
                               *(f"  {i}. {s}" for i, s in enumerate(plan.steps(), 1)),
                               f"Spark's note: {plan.note or '(none)'}", "",
                               "VS Code on the locked computer must have the file focused, with its status bar "
                               "showing. Don't use its keyboard or mouse while the edit is typed; Stop releases "
                               "every key."])
            if auto:
                self.post(("edit", f"Spark's edit, {plan.span()} (Auto: typing it without asking):\n{shown}"))
                return self._typing_allowed()
            if self._confirm(Approval("edit", f"Type Spark's edit of {plan.span()}?", shown, "Type this edit",
                                      "Don't type", mono=True)):
                return self._typing_allowed()
            return False
        return approve

    def _approve_save(self, auto: bool) -> Callable[[str], bool]:
        def approve(name: str) -> bool:
            if auto:
                self._say(f"Auto: Spark checked the fix on the screen, so saving {name} without asking")
                return self._typing_allowed()
            if self._confirm(Approval("save", f"Save {name}?", f"Spark checked the fix on the screen. Save {name} "
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
