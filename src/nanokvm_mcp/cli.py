"""``nanokvm`` command line: diagnostics, service control, the live viewer, and the Glimmer/Spark commands.

    nanokvm diag            check the NanoKVM devices, the video and the service (saves nothing)
    nanokvm start | stop | status
    nanokvm keytest         check that the locked computer takes the NanoKVM's keystrokes (Num Lock, no picture)
    nanokvm viewer          watch the screen live in a window, and ask about it or have a task done there
    nanokvm serve           run the service in the foreground (debugging)
    nanokvm read            read the screen with Muse Glimmer on this laptop (nothing leaves)
    nanokvm ask "QUESTION"  read locally, send only redacted text to Muse Spark, print its answer
    nanokvm edit "REQUEST"  as ask, then type each edit Spark proposes into VS Code once you approve it
    nanokvm fix             as edit with no request: Spark finds the error on the screen itself
                            (edit and fix --local-only: Glimmer proposes the edits instead, and nothing is sent)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .client import ServiceClient, ServiceError
from .config import Config


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def cmd_status(cl: ServiceClient, args) -> int:
    if not cl.is_running():
        _out("NanoKVM service: not running (it starts automatically when a nanokvm command needs it, or "
             "run 'nanokvm start').")
        return 1
    st = cl.status()
    if args.json:
        _out(json.dumps(st, indent=2))
        return 0
    cap, ctl, scr = st["capture"], st["control"], st["screen"]
    _out(f"Service:   running, pid {st['pid']}, up {st['uptime_s']} s{' (SIMULATOR)' if st['simulated'] else ''}")
    _out(f"Video:     {cap['state']}  {cap.get('mode') or ''}  {cap['fps']} fps"
         + (f"\n           {cap['error']}" if cap.get("error") else ""))
    _out(f"Control:   {'disabled' if not ctl['enabled'] else ctl.get('state')}"
         + (f"  {ctl['link']}" if ctl.get("link") else "")
         + (f"\n           {ctl['error']}" if ctl.get("error") else "")
         + (f"\n           {ctl['device_info']}" if ctl.get("device_info") else ""))
    _out(f"Screen:    {scr['width']}x{scr['height']} coordinate space, region {scr['region']} ({scr['region_source']})")
    _out(f"Viewers:   {st['viewers']}")
    _out(f"Sharing:   {st.get('shares_active', '?')} active share(s) (nanokvm read, ask, edit or fix, or the "
         "viewer's prompt box)")
    return 0


def cmd_start(cl: ServiceClient, args) -> int:
    cl.ensure_running()
    return cmd_status(cl, args)


def cmd_stop(cl: ServiceClient, args) -> int:
    if not cl.is_running():
        _out("NanoKVM service was not running.")
        return 0
    if cl.stop():
        _out("NanoKVM service stopped.")
        return 0
    _out("NanoKVM service did not stop within 20 s: end its pythonw.exe process, then check with 'nanokvm status'.")
    return 1


KEYTEST_WAIT_S = 5.5       # the service re-reads the keyboard chip's Num Lock state at most every 5 s


def cmd_keytest(cl: ServiceClient, args) -> int:
    """Press Num Lock twice through the NanoKVM and check that the locked computer's Num Lock state follows each
    time: proof that its operating system takes the NanoKVM's keystrokes. No picture is taken, and Num Lock ends
    as it started."""
    def num_lock() -> bool | None:
        info = (cl.status().get("control") or {}).get("device_info")
        return info.get("num_lock") if isinstance(info, dict) else None

    cl.act({"action": "release_all"})            # opens the keyboard/mouse link: every key up
    time.sleep(KEYTEST_WAIT_S)
    states = [num_lock()]
    if states[0] is None:
        _out("No Num Lock state from the NanoKVM's keyboard chip: check cable 4, then 'nanokvm status'.")
        return 1
    for _ in range(2):
        cl.act({"action": "keys", "keys": "numlock"})
        time.sleep(KEYTEST_WAIT_S)
        states.append(num_lock())
    shown = ", then ".join("on" if s else "off" if s is not None else "unknown" for s in states)
    if states[1] == (not states[0]) and states[2] == states[0]:
        _out(f"Keyboard OK: the locked computer switched Num Lock at each press from the NanoKVM ({shown}).")
        return 0
    if states[0] == states[1] == states[2]:
        _out(f"The locked computer didn't react to the NanoKVM's Num Lock key ({shown}), so it isn't taking "
             "keystrokes from the NanoKVM. Check cable 4 (the NanoKVM's TARGET USB-C to a USB-A port of the "
             "locked computer). A managed laptop can also block new USB keyboards by policy.")
        return 1
    _out(f"Num Lock didn't change as expected ({shown}): check it on the locked computer.")
    return 1


def cmd_viewer(cl: ServiceClient, args) -> int:
    cl.ensure_running()
    from .viewer import run_viewer
    return run_viewer(cl, args.fps, args.close_after)


def _fps(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 60:
        raise argparse.ArgumentTypeError("must be 1 to 60")
    return value


def _seconds(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return value


def cmd_diag(cl: ServiceClient, args) -> int:
    from .capture import list_video_devices
    from .hid import list_serial_ports
    cfg = cl.cfg
    ok = True
    _out("== Locked computer ==")
    try:
        _out(f"  locked_os = {cfg.locked_os_name}, keyboard_layout = {cfg.keyboard_layout}")
    except ValueError as e:     # reported as a problem; the rest of diag still runs
        ok = False
        _out(f"  !! {e}")
    if cfg.layout_warning:
        _out(f"  note: {cfg.layout_warning}")
    _out("\n== Video capture devices (DirectShow; enumeration only) ==")
    vids = cfg.video_id_list
    found_video = False
    try:
        devices = list_video_devices()
    except RuntimeError as e:   # not Windows: no DirectShow to ask
        devices, ok = None, False
        _out(f"  !! {e}")
    for d in devices or []:
        tag = ""
        if (d["vid"], d["pid"]) in vids:
            tag, found_video = "  <-- NanoKVM-USB", True
        vp = f"{d['vid']:04X}:{d['pid']:04X}" if d["vid"] is not None else "?"
        _out(f"  [{d['index']}] {d['name']}  ({vp}){tag}")
    if devices is not None and not found_video:
        ok = False
        _out("  !! NanoKVM capture device not found: plug the NanoKVM's HOST-end USB-C into this laptop.")
    _out("\n== Serial ports ==")
    found_serial = False
    for p in list_serial_ports():
        tag = ""
        if (p["vid"], p["pid"]) in cfg.serial_id_list:
            tag, found_serial = "  <-- NanoKVM keyboard/mouse", True
        _out(f"  {p['device']}: {p['description']}{tag}")
    if cfg.control and not found_serial and not cfg.serial_port:
        ok = False
        if sys.platform == "win32":
            _out("  !! NanoKVM COM port not found. Check that serial_ids in nanokvm.toml lists its chip (CH343\n"
                 "     1A86:55D3, CH340 1A86:7523), then reseat the HOST-end USB-C cable: video and the serial\n"
                 "     chip use different wires. A CH343 needs no extra driver; a CH340 needs WCH's: Device\n"
                 "     Manager > the unknown 'USB Serial' device > Update driver > Search automatically.")
        else:
            _out("  !! NanoKVM serial port not found: check the HOST-end USB-C cable. If it is plugged in, set\n"
                 "     serial_port in nanokvm.toml (Linux: \"/dev/ttyACM0\"; macOS: \"/dev/cu.usbmodem...\").")
    _out("\n== Service ==")
    try:
        cl.ensure_running()
        deadline = time.monotonic() + (args.wait if sys.platform == "win32" else 0)   # no video off Windows
        st = cl.status()
        while st["capture"]["state"] != "streaming" and time.monotonic() < deadline:
            time.sleep(0.5)
            st = cl.status()
        cmd_status(cl, argparse.Namespace(json=False))
        cap = st["capture"]
        if cap["state"] != "streaming":
            ok = False
        else:                                     # checked from the status: no picture is taken or saved
            if cap.get("blank"):
                ok = False
                _out("WARNING: the picture is uniform - no HDMI signal, display asleep, or a blank screen")
            if (cap.get("last_frame_age_ms") or 0) > 2000:
                ok = False
                _out(f"WARNING: the newest frame is {cap['last_frame_age_ms'] / 1000:.1f} s old")
        if args.mouse_test and cfg.control:
            _out("\nMoving the locked computer's pointer in a small square around the centre...")
            sw, sh = st["screen"]["width"], st["screen"]["height"]
            for dx, dy in ((0, 0), (60, 0), (60, 60), (0, 60), (0, 0)):
                cl.act({"action": "move", "x": sw / 2 + dx, "y": sh / 2 + dy})
                time.sleep(0.3)
            _out("Done - did the pointer move on the locked computer?")
    except ServiceError as e:
        ok = False
        _out(f"!! {e}")
    _out("\nRESULT: " + ("OK" if ok else "problems found (see above)"))
    return 0 if ok else 1


def _drain_typeahead() -> None:
    """Throw away keys typed before a prompt appeared, so an earlier 'y' or Enter can't answer it."""
    try:
        if sys.platform == "win32":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:  # noqa: BLE001  (best effort: not a console)
        pass


def _console_yes(prompt: str) -> bool:
    """Approval from the person at the keyboard. There is deliberately no --yes, and nothing is approved
    without a terminal, so a script or a tool running commands can't approve by accident. This guards against
    mistakes; it isn't a security boundary (a program that drives a terminal can type 'y')."""
    if not sys.stdin.isatty():
        _out("This needs your approval at the keyboard: run it in a terminal.")
        return False
    _drain_typeahead()
    try:
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _shown(text: str) -> str:
    """Model or screen text for the terminal, with control characters shown as escapes."""
    from .editing import safe_text
    return safe_text(text, multiline=True)


def _approve_send(text: str) -> bool:
    _out("\n--- exactly this text would go to Muse Spark (Meta) ---\n" + _shown(text) + "\n--- end ---")
    return _console_yes("Send it?")


def _region(text: str | None) -> tuple[float, float, float, float] | None:
    if not text:
        return None
    parts = [float(p) for p in text.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise SystemExit("--region must be x,y,w,h, in the coordinate space nanokvm status shows as Screen")
    return parts[0], parts[1], parts[2], parts[3]


def _share_prompt(minutes: int, auto: bool = False, test_content: bool = False, save: bool = False,
                  who: str = "Spark") -> bool:
    """The one question before the screen is shared. ``auto`` (--auto): the same yes lets this run type the coder's
    (``who``'s) edits without asking for each one (each is still shown and checked on the screen), and with ``save``
    (nanokvm fix) save the file once the coder has checked the fix on a fresh reading of the screen.
    ``test_content``: Meta billing is on free credits and --stand-in wasn't given, so the yes also confirms the
    screen shows only test or dummy content, the only content that may go while Meta can use what is sent."""
    q = f"Share the locked computer's screen with Muse Glimmer on this laptop for {minutes} min"
    if auto and save:
        q += (f", type Muse {who}'s edits into VS Code without asking for each one, and save the file once {who} has "
              "checked the fix on the screen")
    elif auto:
        q += f", and type Muse {who}'s edits into VS Code without asking for each one"
    if test_content:
        q += (". Meta billing is on free credits and Meta may use what is sent, so say yes only if the screen shows "
              "test or dummy content. Go ahead")
    return _console_yes(q + "?")


def _asks_test_content(cfg: Config, stand_in: bool) -> bool:
    """Whether the share prompt must also confirm test content: the free-credit rule, which the outbound gate
    checks again before every send."""
    return cfg.outbound != "local_only" and cfg.meta_billing == "free_credits" and not stand_in


def _line_summary(text: str) -> str:
    """Which line numbers Glimmer read, and any gaps: numbers only, no screen text."""
    from .editing import numbered_lines
    lines = numbered_lines(text)
    if not lines:
        return "(no numbered lines: is a file shown in VS Code, with line numbers on?)"
    nums = sorted(lines)
    missing = [n for n in range(nums[0], nums[-1] + 1) if n not in lines]
    gaps = ("none missing" if not missing else
            f"{len(missing)} missing: " + ", ".join(map(str, missing[:10])) + (" ..." if len(missing) > 10 else ""))
    return f"({len(nums)} numbered lines read, {nums[0]}-{nums[-1]}, {gaps})"


def _check_against(text: str, path: Path) -> str:
    """Compare a code reading with the dummy file shown on the screen, line by line. Only line numbers and
    counts are printed, never the text read, so the result can't carry anything else that was on the screen."""
    from .editing import numbered_lines, numbers_read, same_line
    truth = path.read_text(encoding="utf-8").splitlines()
    got, order = numbered_lines(text, wrapped=False), numbers_read(text)
    exact, spaces, indent, differ, missing = [], [], [], [], []
    for n, want in enumerate(truth, 1):
        if n not in got:
            missing.append(n)
        elif got[n].rstrip() == want.rstrip():
            exact.append(n)
        elif same_line(got[n], want):
            spaces.append(n)
        elif " ".join(got[n].split()) == " ".join(want.split()):
            indent.append(n)
        else:
            differ.append(n)

    def nums(ns: list[int]) -> str:
        return ", ".join(map(str, ns)) or "none"

    def spaces_before(s: str) -> int:
        return len(s) - len(s.lstrip(" "))
    blank = [n for n in missing if not truth[n - 1].strip()]
    out = [f"check against {path.name} ({len(truth)} lines; line numbers only, not the text read):",
           f"  read exactly: {len(exact)} of {len(truth)}; as nanokvm edit compares lines (same indentation, "
           f"runs of spaces ignored): {len(exact) + len(spaces)} of {len(truth)}",
           f"  same apart from spaces inside the line: {nums(spaces)}",
           "  different indentation: " + (", ".join(f"{n} (read {spaces_before(got[n])} spaces, file "
                                                     f"{spaces_before(truth[n - 1])})" for n in indent) or "none"),
           f"  different text: {nums(differ)}",
           f"  not read: {nums(missing)}" + (f" (empty in the file: {nums(blank)})" if blank else "")]
    twice = sorted({n for n in order if order.count(n) > 1})
    beyond = sorted({n for n in order if n > len(truth)})
    rows = [r for r in text.splitlines() if r.strip() and not r.lstrip().startswith("```")]
    unnumbered = len(rows) - len(order)       # other screen text, or a wrapped row: not compared
    extra = ([f"read twice: {nums(twice)}"] if twice else []) + \
            ([f"{len(beyond)} numbered line(s) past line {len(truth)}"] if beyond else []) + \
            ([f"{unnumbered} row(s) without a line number, not compared"] if unnumbered else [])
    if extra:
        out.append("  also " + "; ".join(extra))
    return "\n".join(out)


def _check_file(name: str, cfg: Config) -> Path:
    """The --check file, as given or relative to the project folder (so the README's
    tools\\ab_screens\\live_check.cpp works from any folder). Checked before anything is shared."""
    for path in (Path(name), cfg.home / name):
        if path.is_file():
            return path
    raise SystemExit(f"--check: no file {name} (looked here and in {cfg.home}); nothing was shared")


def _glimmer_up(pipe) -> bool:
    """Whether the local Glimmer server answers, asked before the screen is shared."""
    if pipe.glimmer.health():
        return True
    _out(f"error: Muse Glimmer isn't ready at {pipe.glimmer.url}: start tools\\run-glimmer.ps1 and wait until it "
         "has loaded. Nothing was shared.")
    return False


def _frame_warnings(frame: dict) -> None:
    """The service's warnings about the frame Glimmer read (a uniform picture, an old frame): no screen text."""
    for w in frame.get("warnings") or []:
        _out("WARNING: " + w)


def cmd_read(cl: ServiceClient, args) -> int:
    from .glimmer import GlimmerError
    from .pipeline import Pipeline
    if args.check and args.kind != "code":
        raise SystemExit("--check compares a code reading: leave out --describe and --errors")
    check = _check_file(args.check, cl.cfg) if args.check else None
    try:
        with Pipeline(cl.cfg, approve_share=_share_prompt) as pipe:
            if not _glimmer_up(pipe):
                return 2
            pipe.open(args.minutes)
            r = pipe.read(args.kind, _region(args.region))
    except PermissionError:
        _out("Not shared: nothing was read.")
        return 1
    except GlimmerError as e:
        _out(f"error: {e}")
        return 2
    _out(_shown(r.text))
    f = r.frame
    _out(f"\n(read by Muse Glimmer on this laptop in {r.seconds:.1f} s from a {f['width']}x{f['height']} frame, "
         f"{f['visual_tokens']} image tokens; nothing left this laptop and the frame wasn't saved)")
    _frame_warnings(f)
    if args.kind == "code":
        _out(_line_summary(r.text))
        if check:
            _out(_check_against(r.text, check))
    return 0


def cmd_ask(cl: ServiceClient, args) -> int:
    from .glimmer import GlimmerError
    from .outbound import OutboundBlocked
    from .pipeline import Pipeline
    from .spark import SparkError
    cfg = cl.cfg
    if args.local_only:
        cfg.outbound = "local_only"
    elif args.approve and cfg.outbound == "auto":     # a flag can make nanokvm.toml stricter, never looser
        cfg.outbound = "approve"
    test = _asks_test_content(cfg, args.stand_in)
    try:
        with Pipeline(cfg, approve_share=lambda m: _share_prompt(m, test_content=test), approve_send=_approve_send,
                      stand_in=args.stand_in or test) as pipe:
            if not _glimmer_up(pipe):
                return 2
            pipe.open(args.minutes)
            res = pipe.ask(args.question, _region(args.region))
    except PermissionError:
        _out("Not shared: nothing was read or sent.")
        return 1
    except OutboundBlocked as e:
        _out(f"Nothing was sent: {e}")
        return 3
    except (GlimmerError, SparkError) as e:
        _out(f"error: {e}")
        return 2
    _out(_shown(res.answer))
    _frame_warnings(res.reading.frame)
    if res.local_only:
        _out("\n(answered by Muse Glimmer on this laptop; nothing was sent)")
    else:
        p, sent = res.prepared, res.sent
        found = ", ".join(f"{k} x{v}" for k, v in sorted(p.findings.items())) or "nothing found"
        _out(f"\n(sent {len(p.text)} characters to {sent.reply.model}, store=false; redacted: {found}; "
             f"{'trimmed by Glimmer to the lines needed' if p.trimmed else 'not trimmed'}; "
             f"cost ${sent.reply.cost_usd:.4f}; logged in {cfg.run_dir / 'outbound.log'})")
    return 0


def _show_edit(plan, who: str = "Spark") -> None:
    _out(f"\n--- Muse {who}'s edit: {plan.span()} ('-' lines go, '+' lines come in their place) ---")
    _out(plan.diff())
    _out("--- keys sent to the locked computer ---")
    for i, step in enumerate(plan.steps(), 1):
        _out(f"  {i}. {step}")
    _out(f"{who}'s note: {plan.note or '(none)'}")
    _out("VS Code on the locked computer must have the file focused, with its status bar showing. Don't use its "
         "keyboard or mouse while the edit is typed; Ctrl+C here stops it.")


def _approve_edit(plan, who: str = "Spark") -> bool:
    _show_edit(plan, who)
    return _console_yes("Type this edit?")


def _auto_edit(plan, who: str = "Spark") -> bool:
    """--auto: show the edit and type it without asking (allowed once, at the share prompt)."""
    _show_edit(plan, who)
    _out("--auto: typing it without asking.")
    return True


def _approve_save(name: str, who: str = "Spark") -> bool:
    return _console_yes(f"{who} checked the fix on the screen. Save {name} in VS Code on the locked computer?")


def _auto_save(name: str, who: str = "Spark") -> bool:
    """nanokvm fix --auto: save without asking (allowed once, at the share prompt)."""
    _out(f"--auto: {who} checked the fix on the screen, so saving {name} without asking.")
    return True


def cmd_edit(cl: ServiceClient, args) -> int:
    from .glimmer import GlimmerError
    from .outbound import OutboundBlocked
    from .pipeline import EditError, Pipeline
    from .spark import SparkError
    cfg = cl.cfg
    if args.local_only:                             # Glimmer is the coder too: nothing is sent
        cfg.outbound = "local_only"
    elif args.approve and cfg.outbound == "auto":   # a flag can make nanokvm.toml stricter, never looser
        cfg.outbound = "approve"
    who = "Glimmer" if cfg.outbound == "local_only" else "Spark"
    test = _asks_test_content(cfg, args.stand_in)
    fix = args.request is None                      # nanokvm fix: saves once the coder has checked the fix
    edit_fn, save_fn = (_auto_edit, _auto_save) if args.auto else (_approve_edit, _approve_save)
    try:
        with Pipeline(cfg, approve_share=lambda m: _share_prompt(m, auto=args.auto, test_content=test, save=fix,
                                                                 who=who),
                      approve_send=_approve_send, stand_in=args.stand_in or test) as pipe:
            if not _glimmer_up(pipe):
                return 2
            pipe.open(args.minutes)
            res = pipe.edit(args.request, _region(args.region), approve_edit=lambda plan: edit_fn(plan, who),
                            on_event=lambda m: _out(f"... {m}"),
                            approve_save=(lambda name: save_fn(name, who)) if fix else None)
    except PermissionError:
        _out("Not shared: nothing was read, sent or typed.")
        return 1
    except EditError as e:
        _out(f"Nothing was sent or typed: {e}")
        return 3
    except OutboundBlocked as e:
        _out(f"Stopped, nothing more was sent: {e}")
        return 3
    except (GlimmerError, SparkError) as e:
        _out(f"error: {e}")
        return 2
    except KeyboardInterrupt:
        _out("\nStopped (Ctrl+C): every key was released. Check the file on the locked computer.")
        return 130
    _frame_warnings(res.reading.frame)
    for rec in res.edits:
        if rec.plan is None:
            _out(f"- not shown: {rec.told_spark}")
        elif not rec.approved:
            _out(f"- {rec.plan.span()}: declined")
        else:
            _out(f"- {rec.plan.span()}: {rec.outcome.status} ({rec.outcome.detail})")
    if res.summary:
        _out("\n" + _shown(res.summary))
    if res.stopped:
        _out(f"\nStopped early: {_shown(res.stopped)}")
    typed = sum(1 for r in res.edits if r.outcome is not None and r.outcome.status != "not_applied")
    rechecked = f"; {who} checked the fix on {res.checks} fresh reading(s) of the screen" if res.checks else ""
    sent = (f"{res.turns} request(s) to Muse Glimmer on this laptop; nothing was sent" if res.local else
            f"{res.turns} request(s) to {cfg.spark_model}, store=false, cost ${res.cost_usd:.4f}; logged in "
            f"{cfg.run_dir / 'outbound.log'}")
    _out(f"\n({typed} edit(s) typed, {sum(1 for r in res.edits if r.outcome and r.outcome.status == 'verified')} "
         f"checked on the screen{rechecked}; {sent})")
    if res.save is not None:
        _out(res.save.report())
    elif typed:
        _out("nanokvm saved nothing (keep VS Code's auto-save off): review the changes in VS Code before you save. "
             "Ctrl+Z, or File: Revert File, undoes them.")
    unsaved = res.save is not None and res.save.status not in ("saved", "declined")
    return 4 if res.stopped or unsaved else 0


def cmd_serve(cl: ServiceClient, args) -> int:
    from .service import serve
    return serve(cl.cfg, foreground=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nanokvm", description="Locked Computer Assist: AI help for the locked "
                                                             "computer through a NanoKVM-USB")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="show service, video and keyboard/mouse status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("start", help="start the background service")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_start)
    sub.add_parser("stop", help="stop the background service").set_defaults(fn=cmd_stop)
    sub.add_parser("keytest", help="press Num Lock twice through the NanoKVM and check that the locked computer "
                                   "follows (no picture taken)").set_defaults(fn=cmd_keytest)
    p = sub.add_parser("viewer", help="watch the locked computer's screen live in a window, and ask about it (or have "
                                      "a task done there) from the prompt box beside the picture")
    p.add_argument("--fps", type=_fps, default=30, help="frames per second in the window, 1-60 (default 30)")
    p.add_argument("--close-after", type=_seconds, metavar="SECONDS", help="close the window after this long")
    p.set_defaults(fn=cmd_viewer)
    p = sub.add_parser("diag", help="check the NanoKVM hardware, the video and the service (saves nothing)")
    p.add_argument("--wait", type=float, default=10.0, help="seconds to wait for video (default 10)")
    p.add_argument("--mouse-test", action="store_true", help="also move the locked computer pointer in a small square")
    p.set_defaults(fn=cmd_diag)
    sub.add_parser("serve", help="run the service in the foreground").set_defaults(fn=cmd_serve)
    p = sub.add_parser("read", help="read the screen with Muse Glimmer on this laptop (nothing leaves the laptop)")
    p.add_argument("--describe", dest="kind", action="store_const", const="describe", default="code",
                   help="describe the screen instead of transcribing code")
    p.add_argument("--errors", dest="kind", action="store_const", const="errors",
                   help="list compiler errors and warnings shown on the screen")
    p.add_argument("--region", help="x,y,w,h in the screen's coordinate space (nanokvm status shows its size as Screen; "
                             "default: the whole screen)")
    p.add_argument("--minutes", type=int, default=5, help="length of the screen share (default 5)")
    p.add_argument("--check", metavar="FILE",
                   help="compare the code read with FILE, a dummy file that fits on the screen (for example "
                        "tools\\ab_screens\\live_check.cpp, found from any folder): prints which lines match, "
                        "as line numbers only")
    p.set_defaults(fn=cmd_read)
    p = sub.add_parser("ask", help="ask about the screen: read it locally, send only redacted text to Muse Spark")
    p.add_argument("question")
    p.add_argument("--region", help="x,y,w,h in the screen's coordinate space (nanokvm status shows its size as Screen; "
                             "default: the whole screen)")
    p.add_argument("--minutes", type=int, default=5, help="length of the screen share (default 5)")
    p.add_argument("--stand-in", action="store_true",
                   help="the screen shows only test or dummy content: answers in advance what the share prompt asks "
                            "while Meta billing is on free credits")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--approve", action="store_true", help="show the exact text and ask before sending")
    mode.add_argument("--local-only", action="store_true", help="answer with Glimmer on this laptop; send nothing")
    p.set_defaults(fn=cmd_ask)
    for name in ("edit", "fix"):
        if name == "edit":
            p = sub.add_parser("edit", help="ask Muse Spark (or with --local-only, Glimmer) for code edits and type "
                                            "each one you approve into VS Code; nothing is saved")
            p.add_argument("request", help='what to change, for example "fix the off-by-one in sum_first"')
        else:
            p = sub.add_parser("fix", help="as edit, with no request: Glimmer notes what is wrong on the screen, and "
                                           "Muse Spark (or with --local-only, Glimmer) finds the error and fixes it, "
                                           "then saves the file once the fix is checked on the screen and you allow it")
            p.set_defaults(request=None)
        p.add_argument("--region", help="x,y,w,h in the screen's coordinate space (nanokvm status shows its size as Screen; "
                                 "default: the whole screen)")
        p.add_argument("--minutes", type=int, default=10, help="length of the screen share (default 10)")
        p.add_argument("--stand-in", action="store_true",
                       help="the screen shows only test or dummy content: answers in advance what the share prompt asks "
                            "while Meta billing is on free credits")
        mode = p.add_mutually_exclusive_group()
        mode.add_argument("--approve", action="store_true", help="also show the exact text and ask before each send")
        mode.add_argument("--local-only", action="store_true",
                          help="Muse Glimmer on this laptop plans the edits instead of Spark; nothing is sent")
        p.add_argument("--auto", action="store_true",
                       help="type the edits without asking for each one: you allow it once, at the share prompt; "
                            "each edit is still shown and checked on the screen" + (
                                ", and the file is saved once the coder (Spark, or Glimmer with --local-only) has "
                                "checked the fix on a fresh reading of the screen" if name == "fix" else
                                ", and nothing is saved"))
        p.set_defaults(fn=cmd_edit)
    args = ap.parse_args(argv)
    cl = ServiceClient(Config.load())
    try:
        return int(args.fn(cl, args) or 0)
    except ServiceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
