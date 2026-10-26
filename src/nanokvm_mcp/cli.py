"""``nanokvm`` command line: diagnostics, service control, live viewer, screenshots, change watching.

    nanokvm diag            check the NanoKVM devices and take a test screenshot
    nanokvm start | stop | status
    nanokvm viewer          open the live view in the browser (or print its URL)
    nanokvm screenshot [FILE]
    nanokvm watch           print one line per screen change (for the user; not gated by screen sharing)
    nanokvm serve           run the service in the foreground (debugging)
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
    """Write a Work-laptop screenshot; it is deleted automatically after cfg.saved_image_minutes."""
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
    url = cl.viewer_url()
    _out(url)
    _out("In VS Code: Ctrl+Shift+P > 'Simple Browser: Show' and paste the URL above.")
    if not args.no_open:
        webbrowser.open(url)
    return 0


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
    _out(f"watching the Work laptop screen (min change {args.min_change}%); frames -> {out_dir} "
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
    _out("== Video capture devices (DirectShow; enumeration only) ==")
    vids = cfg.video_id_list
    found_video = False
    for d in list_video_devices():
        tag = ""
        if (d["vid"], d["pid"]) in vids:
            tag, found_video = "  <-- NanoKVM-USB", True
        vp = f"{d['vid']:04X}:{d['pid']:04X}" if d["vid"] is not None else "?"
        _out(f"  [{d['index']}] {d['name']}  ({vp}){tag}")
    if not found_video:
        ok = False
        _out("  !! NanoKVM capture device not found: plug the NanoKVM's HOST-end USB-C into this laptop.")
    _out("\n== Serial ports ==")
    found_serial = False
    for p in list_serial_ports():
        tag = ""
        if (p["vid"], p["pid"]) in cfg.serial_id_list:
            tag, found_serial = "  <-- NanoKVM keyboard/mouse (CH340)", True
        _out(f"  {p['device']}: {p['description']}{tag}")
    if cfg.control and not found_serial and not cfg.serial_port:
        ok = False
        _out("  !! CH340 COM port not found. If video works, Windows still needs the WCH CH340 driver:\n"
             "     Device Manager > the unknown 'USB Serial' device > Update driver > Search automatically.")
    _out("\n== Service ==")
    try:
        cl.ensure_running()
        deadline = time.monotonic() + args.wait
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
            _out("\nMoving the Work laptop's pointer in a small square around the centre...")
            sw, sh = st["screen"]["width"], st["screen"]["height"]
            for dx, dy in ((0, 0), (60, 0), (60, 60), (0, 60), (0, 0)):
                cl.act({"action": "move", "x": sw / 2 + dx, "y": sh / 2 + dy})
                time.sleep(0.3)
            _out("Done - did the pointer move on the Work laptop?")
    except ServiceError as e:
        ok = False
        _out(f"!! {e}")
    _out("\nRESULT: " + ("OK" if ok else "problems found (see above)"))
    return 0 if ok else 1


def cmd_serve(cl: ServiceClient, args) -> int:
    from .service import serve
    return serve(cl.cfg, foreground=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nanokvm", description="NanoKVM-USB bridge for the Work laptop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="show service, video and keyboard/mouse status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("start", help="start the background service")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_start)
    sub.add_parser("stop", help="stop the background service").set_defaults(fn=cmd_stop)
    p = sub.add_parser("viewer", help="open the live view of the Work laptop screen")
    p.add_argument("--no-open", action="store_true", help="only print the URL")
    p.set_defaults(fn=cmd_viewer)
    p = sub.add_parser("screenshot", help="save a screenshot of the Work laptop screen")
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
    p.add_argument("--mouse-test", action="store_true", help="also move the Work laptop pointer in a small square")
    p.set_defaults(fn=cmd_diag)
    sub.add_parser("serve", help="run the service in the foreground").set_defaults(fn=cmd_serve)
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
