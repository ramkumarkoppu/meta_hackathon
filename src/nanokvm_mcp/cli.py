"""``nanokvm`` command line: diagnostics, service control, live viewer, screenshots, change watching.

    nanokvm diag            check the NanoKVM devices and take a test screenshot
    nanokvm start | stop | status
    nanokvm viewer          watch the screen live in a window (--browser: in the browser; --no-open: URL)
    nanokvm screenshot [FILE]
    nanokvm watch           print one line per screen change (for the user; not gated by screen sharing)
    nanokvm serve           run the service in the foreground (debugging)
    nanokvm read            read the screen with Muse Glimmer on this laptop (nothing leaves)
    nanokvm ask "QUESTION"  read locally, send only redacted text to Muse Spark, print its answer
    nanokvm edit "REQUEST"  as ask, then type each edit Spark proposes into VS Code once you approve it
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import webbrowser
from pathlib import Path

from . import saved_images
from .client import ServiceClient, ServiceError
from .config import Config


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _save_jpeg(shot: dict, path: Path, cfg: Config) -> Path:
    """Write a locked-computer screenshot; it is deleted automatically after cfg.saved_image_minutes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(shot["jpeg_b64"]))
    saved_images.remember(cfg.run_dir, path, cfg.saved_image_minutes)
    return path


def _kept_for(cfg: Config) -> str:
    return f"deleted automatically after {max(1.0, cfg.saved_image_minutes):g} min"


def cmd_status(cl: ServiceClient, args) -> int:
    if not cl.is_running():
        _out("NanoKVM service: not running (it starts automatically when Claude uses a tool, or run 'nanokvm start').")
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
    _out(f"Live view: {cl.viewer_url()}")
    return 0


def cmd_start(cl: ServiceClient, args) -> int:
    cl.ensure_running()
    return cmd_status(cl, args)


def cmd_stop(cl: ServiceClient, args) -> int:
    _out("NanoKVM service stopped." if cl.stop() else "NanoKVM service was not running.")
    return 0


def cmd_viewer(cl: ServiceClient, args) -> int:
    cl.ensure_running()
    if args.browser or args.no_open:
        url = cl.viewer_url()
        _out(url)
        _out("In VS Code: Ctrl+Shift+P > 'Simple Browser: Show' and paste the URL above.")
        if args.browser:
            webbrowser.open(url)
        return 0
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


def cmd_screenshot(cl: ServiceClient, args) -> int:
    shot = cl.screenshot()
    path = _save_jpeg(shot, Path(args.file) if args.file else cl.cfg.run_dir / "screenshot.jpg", cl.cfg)
    _out(f"saved {path} ({shot['width']}x{shot['height']}, captured {shot['captured_at']}; {_kept_for(cl.cfg)})")
    for w in shot.get("warnings") or []:
        _out("WARNING: " + w)
    return 0


def cmd_watch(cl: ServiceClient, args) -> int:
    """One line per settled screen change; the latest frame is saved to .run/watch/latest.jpg."""
    out_dir = cl.cfg.run_dir / "watch"
    _out(f"watching the locked computer screen (min change {args.min_change}%); frames -> {out_dir} "
         f"({_kept_for(cl.cfg)})")
    n = 0
    while True:
        try:
            r = cl.wait_change(60.0, args.min_change, args.settle)
        except ServiceError as e:
            _out(f"{time.strftime('%H:%M:%S')} error: {e}")
            time.sleep(5)
            continue
        if not r["changed"]:
            continue
        n += 1
        shot = r["shot"]
        latest = _save_jpeg(shot, out_dir / "latest.jpg", cl.cfg)
        if args.keep:
            _save_jpeg(shot, out_dir / f"change-{time.strftime('%Y%m%d-%H%M%S')}-{n:04d}.jpg", cl.cfg)
        _out(f"{shot['captured_at']} screen changed {r['change_percent']:.1f}% -> {latest}")
        if args.count and n >= args.count:
            return 0


def cmd_diag(cl: ServiceClient, args) -> int:
    from .capture import list_video_devices
    from .hid import list_serial_ports
    cfg = cl.cfg
    ok = True
    _out("== Locked computer ==")
    try:
        _out(f"  locked_os = {cfg.locked_os_name}, keyboard_layout = {cfg.keyboard_layout}")
    except ValueError as e:     # the MCP server still starts, but tells Claude the OS is unknown
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
        if st["capture"]["state"] == "streaming":
            shot = cl.screenshot()
            path = _save_jpeg(shot, cfg.run_dir / "diag.jpg", cfg)
            _out(f"\nTest screenshot saved: {path} ({_kept_for(cfg)})")
            for w in shot.get("warnings") or []:
                ok = False
                _out("WARNING: " + w)
        else:
            ok = False
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


def _console_yes(prompt: str) -> bool:
    """Approval from the person at the keyboard. There is deliberately no --yes: a script, or an AI
    running commands without a terminal, can't approve."""
    if not sys.stdin.isatty():
        _out("This needs your approval at the keyboard: run it in a terminal.")
        return False
    try:
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _region(text: str | None) -> tuple[float, float, float, float] | None:
    if not text:
        return None
    parts = [float(p) for p in text.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise SystemExit("--region must be x,y,w,h (in the pixels of a nanokvm screenshot)")
    return parts[0], parts[1], parts[2], parts[3]


def _share_prompt(minutes: int) -> bool:
    return _console_yes(f"Share the locked computer's screen with Muse Glimmer on this laptop for {minutes} min?")


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
    """Compare a code reading with the file shown on the screen (a dummy file), line by line."""
    from .editing import numbered_lines
    truth = path.read_text(encoding="utf-8").splitlines()
    got = numbered_lines(text)
    nums = [n for n in sorted(got) if 1 <= n <= len(truth)]
    if not nums:
        return f"check against {path.name}: none of its line numbers were read"
    exact = sum(1 for n in nums if got[n].rstrip() == truth[n - 1].rstrip())
    loose = [n for n in nums if " ".join(got[n].split()) == " ".join(truth[n - 1].split())]
    out = [f"check against {path.name} (lines {nums[0]}-{nums[-1]} of {len(truth)} on the screen): "
           f"{exact}/{len(nums)} exact, {len(loose)}/{len(nums)} equal apart from spacing"]
    out += [f"  line {n}: read {got[n]!r}, file has {truth[n - 1]!r}" for n in nums if n not in loose]
    return "\n".join(out)


def cmd_read(cl: ServiceClient, args) -> int:
    from .glimmer import GlimmerError
    from .pipeline import Pipeline
    try:
        with Pipeline(cl.cfg, approve_share=_share_prompt) as pipe:
            pipe.open(args.minutes)
            r = pipe.read(args.kind, _region(args.region))
    except PermissionError:
        _out("Not shared: nothing was read.")
        return 1
    except GlimmerError as e:
        _out(f"error: {e}")
        return 2
    _out(r.text)
    f = r.frame
    _out(f"\n(read by Muse Glimmer on this laptop in {r.seconds:.1f} s from a {f['width']}x{f['height']} frame, "
         f"{f['visual_tokens']} image tokens; nothing left this laptop and the frame wasn't saved)")
    if args.kind == "code":
        _out(_line_summary(r.text))
        if args.check:
            _out(_check_against(r.text, Path(args.check)))
    return 0


def cmd_ask(cl: ServiceClient, args) -> int:
    from .glimmer import GlimmerError
    from .outbound import OutboundBlocked
    from .pipeline import Pipeline
    from .spark import SparkError
    cfg = cl.cfg
    if args.local_only:
        cfg.outbound = "local_only"
    elif args.approve:
        cfg.outbound = "approve"

    def approve_send(text: str) -> bool:
        _out("\n--- exactly this text would go to Muse Spark (Meta) ---\n" + text + "\n--- end ---")
        return _console_yes("Send it?")

    try:
        with Pipeline(cfg, approve_share=_share_prompt, approve_send=approve_send, stand_in=args.stand_in) as pipe:
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
    _out(res.answer)
    if res.local_only:
        _out("\n(answered by Muse Glimmer on this laptop; nothing was sent)")
    else:
        p, sent = res.prepared, res.sent
        found = ", ".join(f"{k} x{v}" for k, v in sorted(p.findings.items())) or "nothing found"
        _out(f"\n(sent {len(p.text)} characters to {sent.reply.model}, store=false; redacted: {found}; "
             f"{'trimmed by Glimmer to the lines needed' if p.trimmed else 'not trimmed'}; "
             f"cost ${sent.reply.cost_usd:.4f}; logged in {cfg.run_dir / 'outbound.log'})")
    return 0


def _approve_edit(plan) -> bool:
    _out(f"\n--- Muse Spark's edit to line(s) {plan.span()}: {plan.note or '(no note)'} ---")
    _out(plan.diff() or "(no visible change)")
    _out("--- keys sent to the locked computer ---")
    for i, step in enumerate(plan.steps(), 1):
        _out(f"  {i}. {step}")
    _out("VS Code on the locked computer must have the file focused, with its status bar showing.")
    return _console_yes("Type this edit?")


def cmd_edit(cl: ServiceClient, args) -> int:
    from .glimmer import GlimmerError
    from .outbound import OutboundBlocked
    from .pipeline import EditError, Pipeline
    from .spark import SparkError
    cfg = cl.cfg
    if args.approve:
        cfg.outbound = "approve"

    def approve_send(text: str) -> bool:
        _out("\n--- exactly this text would go to Muse Spark (Meta) ---\n" + text + "\n--- end ---")
        return _console_yes("Send it?")

    try:
        with Pipeline(cfg, approve_share=_share_prompt, approve_send=approve_send, stand_in=args.stand_in) as pipe:
            pipe.open(args.minutes)
            res = pipe.edit(args.request, _region(args.region), approve_edit=_approve_edit,
                            on_event=lambda m: _out(f"... {m}"))
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
    for rec in res.edits:
        if rec.plan is None:
            _out(f"- not shown: {rec.told_spark}")
        elif not rec.approved:
            _out(f"- line(s) {rec.plan.span()}: declined")
        else:
            _out(f"- line(s) {rec.plan.span()}: {rec.outcome.status} ({rec.outcome.detail})")
    if res.summary:
        _out("\n" + res.summary)
    if res.stopped:
        _out(f"\nStopped early: {res.stopped}")
    typed = sum(1 for r in res.edits if r.outcome is not None)
    _out(f"\n({typed} edit(s) typed, {sum(1 for r in res.edits if r.outcome and r.outcome.status == 'verified')} "
         f"checked on the screen; {res.turns} request(s) to {cfg.spark_model}, store=false, cost "
         f"${res.cost_usd:.4f}; logged in {cfg.run_dir / 'outbound.log'})")
    return 0 if not res.stopped else 4


def cmd_serve(cl: ServiceClient, args) -> int:
    from .service import serve
    return serve(cl.cfg, foreground=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nanokvm", description="NanoKVM-USB bridge for the locked computer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="show service, video and keyboard/mouse status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("start", help="start the background service")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_start)
    sub.add_parser("stop", help="stop the background service").set_defaults(fn=cmd_stop)
    p = sub.add_parser("viewer", help="watch the locked computer screen live in a window (read-only)")
    p.add_argument("--fps", type=_fps, default=30, help="frames per second in the window, 1-60 (default 30)")
    p.add_argument("--close-after", type=_seconds, metavar="SECONDS", help="close the window after this long")
    where = p.add_mutually_exclusive_group()
    where.add_argument("--browser", action="store_true", help="open the live view in your browser instead")
    where.add_argument("--no-open", action="store_true", help="only print the browser live view's URL")
    p.set_defaults(fn=cmd_viewer)
    p = sub.add_parser("screenshot", help="save a screenshot of the locked computer screen")
    p.add_argument("file", nargs="?")
    p.set_defaults(fn=cmd_screenshot)
    p = sub.add_parser("watch", help="print a line per screen change; saves the latest frame in .run/watch")
    p.add_argument("--min-change", type=float, default=0.5, help="percent of the screen (default 0.5)")
    p.add_argument("--settle", type=int, default=700, help="ms the screen must be still (default 700)")
    p.add_argument("--keep", action="store_true",
                   help="also save every changed frame (each is still deleted after saved_image_minutes)")
    p.add_argument("--count", type=int, default=0, help="exit after N changes (0 = run forever)")
    p.set_defaults(fn=cmd_watch)
    p = sub.add_parser("diag", help="check the NanoKVM hardware and take a test screenshot")
    p.add_argument("--wait", type=float, default=10.0, help="seconds to wait for video (default 10)")
    p.add_argument("--mouse-test", action="store_true", help="also move the locked computer pointer in a small square")
    p.set_defaults(fn=cmd_diag)
    sub.add_parser("serve", help="run the service in the foreground").set_defaults(fn=cmd_serve)
    p = sub.add_parser("read", help="read the screen with Muse Glimmer on this laptop (nothing leaves the laptop)")
    p.add_argument("--describe", dest="kind", action="store_const", const="describe", default="code",
                   help="describe the screen instead of transcribing code")
    p.add_argument("--errors", dest="kind", action="store_const", const="errors",
                   help="list compiler errors and warnings shown on the screen")
    p.add_argument("--region", help="x,y,w,h in the pixels of a nanokvm screenshot (default: the whole screen)")
    p.add_argument("--minutes", type=int, default=5, help="length of the screen share (default 5)")
    p.add_argument("--check", metavar="FILE",
                   help="compare the code read with FILE, a dummy file shown on the screen (for example "
                        "tools\\ab_screens\\live_check.cpp): prints how many lines match")
    p.set_defaults(fn=cmd_read)
    p = sub.add_parser("ask", help="ask about the screen: read it locally, send only redacted text to Muse Spark")
    p.add_argument("question")
    p.add_argument("--region", help="x,y,w,h in the pixels of a nanokvm screenshot (default: the whole screen)")
    p.add_argument("--minutes", type=int, default=5, help="length of the screen share (default 5)")
    p.add_argument("--stand-in", action="store_true",
                   help="the screen shows stand-in or dummy content (required while Meta billing is on free credits)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--approve", action="store_true", help="show the exact text and ask before sending")
    mode.add_argument("--local-only", action="store_true", help="answer with Glimmer on this laptop; send nothing")
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("edit", help="ask Muse Spark for code edits and type each one you approve into VS Code")
    p.add_argument("request", help='what to change, for example "fix the off-by-one in sum_first"')
    p.add_argument("--region", help="x,y,w,h in the pixels of a nanokvm screenshot (default: the whole screen)")
    p.add_argument("--minutes", type=int, default=10, help="length of the screen share (default 10)")
    p.add_argument("--stand-in", action="store_true",
                   help="the screen shows stand-in or dummy content (required while Meta billing is on free credits)")
    p.add_argument("--approve", action="store_true", help="also show the exact text and ask before each send")
    p.set_defaults(fn=cmd_edit)
    args = ap.parse_args(argv)
    cl = ServiceClient(Config.load())
    try:   # delete expired screenshots even when the service (which normally does it) isn't running
        saved_images.sweep(cl.cfg.run_dir, cl.cfg.saved_image_minutes)
    except OSError as e:
        print(f"warning: could not clean up saved screenshots: {e}", file=sys.stderr)
    try:
        return int(args.fn(cl, args) or 0)
    except ServiceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
