"""Save one full-resolution frame from the NanoKVM's HDMI capture as a lossless PNG, for the Glimmer A/B.

Use DUMMY content only: loop the AI computer's HDMI into the NanoKVM TARGET end and open
tools\\ab_screens\\*.cpp in VS Code on that display. The frame stays on this laptop; this script prints
only metadata. The background service holds the capture device, so stop it first (nanokvm stop).

Usage:  E:\\meta_hackathon\\.venv\\Scripts\\python.exe E:\\meta_hackathon\\tools\\capture_frame.py OUT.png [--settle 2]
        E:\\meta_hackathon\\.venv\\Scripts\\python.exe E:\\meta_hackathon\\tools\\capture_frame.py --interactive OUT_DIR
        (asks for each A/B screen in turn and saves it when you press Enter)
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from nanokvm_mcp.capture import HdmiCapture
from nanokvm_mcp.config import Config

AB = Path(__file__).resolve().parent / "ab_screens"
# (file name, what to show) for --interactive; glimmer_ab.py pairs NAME.png with the file.
SCREENS = [
    ("ring_buffer.png", f"{AB / 'ring_buffer.cpp'} in VS Code, light theme, font size 16, from line 1"),
    ("config_parser.png", f"{AB / 'config_parser.cpp'} in VS Code, dark theme, font size 14, from line 1"),
    ("long_lines.png", f"{AB / 'long_lines.cpp'} in VS Code with word wrap on (Alt+Z), from line 1"),
    ("build_errors.png", f"a terminal showing: type {AB / 'build_errors.txt'}"),
]


def grab(cap: HdmiCapture, out: Path, settle: float) -> bool:
    time.sleep(settle)  # let the picture and the MJPEG encoder settle
    last = cap.latest()
    frame = cap.wait_frame(last.seq if last else 0, timeout=3.0) or last
    if frame is None:
        print("no frame received")
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out), frame.image):
        print(f"could not write {out}")
        return False
    st = cap.status()
    print(f"saved {out} ({frame.image.shape[1]}x{frame.image.shape[0]}, mode {st['mode']}, {st['fps']} fps)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", help="PNG path to write (a folder with --interactive)")
    ap.add_argument("--interactive", action="store_true", help="capture each A/B screen in turn, on Enter")
    ap.add_argument("--settle", type=float, default=2.0, help="seconds of streaming before the frame is taken")
    ap.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for the device to stream")
    a = ap.parse_args()
    cfg = Config.load()
    cap = HdmiCapture(cfg.video_id_list, cfg.video_index, cfg.capture_width, cfg.capture_height,
                      cfg.capture_fps, cfg.capture_fourcc)
    try:
        deadline = time.monotonic() + a.timeout
        while cap.status()["state"] != "streaming":
            if time.monotonic() > deadline:
                st = cap.status()
                print(f"capture not streaming ({st['state']}): {st['error']}")
                return 1
            time.sleep(0.2)
        if not a.interactive:
            return 0 if grab(cap, Path(a.out), a.settle) else 1
        for name, what in SCREENS:
            input(f"\nShow {what}\nthen press Enter here (Ctrl+C to stop)... ")
            if not grab(cap, Path(a.out) / name, a.settle):
                return 1
        print("\nAll screens captured.")
        return 0
    finally:
        cap.close()


if __name__ == "__main__":
    raise SystemExit(main())
