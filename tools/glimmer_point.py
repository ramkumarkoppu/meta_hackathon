"""Can Muse Glimmer point at things on a screen? A synthetic 1920x1080 window of a made-up requirements tool (dummy
names only, no real screen), with the true box of everything on it, and Glimmer asked for each target with Do it's own
prompt (acting.GROUND_PROMPT): a hit is a box whose middle, where Do it would click, falls inside the true box.

    .venv\\Scripts\\python tools\\glimmer_point.py            (Glimmer running: tools\\run-glimmer.ps1)

On 30 Oct this found 8 of 8 targets, and NONE for a button that isn't on the screen.
"""
from __future__ import annotations

import base64
import sys
import time

import cv2
import numpy as np

from nanokvm_mcp import acting as A
from nanokvm_mcp.config import Config
from nanokvm_mcp.glimmer import GlimmerClient

W, H = 1920, 1080
FONT = cv2.FONT_HERSHEY_SIMPLEX
MENUS = ["File", "Edit", "View", "Tools", "Change Management", "Publish", "Help"]
TREE = [(0, "Demo Database"), (1, "Core Things"), (1, "Project Alpha"), (2, "Customer Info"), (2, "Glossary"),
        (2, "Requirements"), (3, "Design Notes"), (3, "FMEA"), (3, "Systems Requirements"), (2, "Standards"),
        (1, "Project Beta"), (2, "Requirements"), (1, "Archive"), (1, "Training")]
TARGETS = [("the 'Design Notes' item in the tree", "tree:Design Notes"),
           ("the 'Requirements' item under 'Project Beta' in the tree", "tree:Requirements under Project Beta"),
           ("the Tools menu", "menu:Tools"), ("the OK button", "button:OK"),
           ("the 'Description' column header", "col:Description"), ("the Cancel button", "button:Cancel"),
           ("the 'Glossary' item in the tree", "tree:Glossary"), ("the Help menu", "menu:Help")]
ABSENT = "the 'Save As' button"


def screen() -> tuple[np.ndarray, dict[str, tuple[int, int, int, int]]]:
    """The synthetic window, and the true box (in pixels) of each thing on it."""
    img = np.full((H, W, 3), 240, np.uint8)
    boxes: dict[str, tuple[int, int, int, int]] = {}

    def text(s: str, x: int, y: int, key: str | None = None) -> None:
        (tw, th), base = cv2.getTextSize(s, FONT, 0.55, 1)
        cv2.putText(img, s, (x, y), FONT, 0.55, (20, 20, 20), 1, cv2.LINE_AA)
        if key:
            boxes[key] = (x - 4, y - th - 4, x + tw + 4, y + base + 4)
    cv2.rectangle(img, (0, 0), (W, 34), (255, 255, 255), -1)
    text("Demo Requirements Database: /Project-Beta - ReqTool", 12, 23)
    for i, m in enumerate(MENUS):
        text(m, 12 + sum(len(n) * 11 + 26 for n in MENUS[:i]), 58, key=f"menu:{m}")
    cv2.line(img, (0, 68), (W, 68), (200, 200, 200), 1)
    cv2.rectangle(img, (0, 120), (700, 1000), (255, 255, 255), -1)
    y, seen = 150, set()
    for depth, name in TREE:
        x = 20 + depth * 28
        cv2.rectangle(img, (x, y - 13), (x + 16, y + 1), (60, 170, 230), -1)       # a folder icon
        text(name, x + 24, y, key="tree:" + (name if name not in seen else f"{name} under Project Beta"))
        seen.add(name)
        y += 26
    cv2.rectangle(img, (720, 120), (1900, 1000), (255, 255, 255), -1)
    for i, col in enumerate(["Name", "Type", "Description", "Deleted?"]):
        text(col, 740 + i * 220, 145, key=f"col:{col}")
    cv2.rectangle(img, (1300, 700), (1700, 860), (225, 225, 225), -1)                 # a small dialog
    text("Copy 'Systems Requirements' here?", 1320, 740)
    cv2.rectangle(img, (1430, 800), (1520, 836), (250, 250, 250), -1)
    text("OK", 1462, 824, key="button:OK")
    cv2.rectangle(img, (1560, 800), (1670, 836), (250, 250, 250), -1)
    text("Cancel", 1580, 824, key="button:Cancel")
    return img, boxes


def main() -> int:
    img, boxes = screen()
    b64 = base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode("ascii")
    glimmer = GlimmerClient(Config.load())
    if not glimmer.health():
        print("Muse Glimmer isn't answering: start tools\\run-glimmer.ps1 first.")
        return 2
    hits = 0
    for want, key in TARGETS:
        t0 = time.monotonic()
        reply = glimmer.locate(b64, "png", A.GROUND_PROMPT.format(target=want)).content
        box, why = A.parse_box(reply)
        x1, y1, x2, y2 = boxes[key]
        hit = box is not None and x1 <= (box[0] + box[2]) / 2000 * W <= x2 and y1 <= (box[1] + box[3]) / 2000 * H <= y2
        hits += hit
        print(f"{'HIT ' if hit else 'MISS'} {want}: {reply.strip()!r} {why} ({time.monotonic() - t0:.1f} s)")
    absent = A.parse_box(glimmer.locate(b64, "png", A.GROUND_PROMPT.format(target=ABSENT)).content)[0]
    print(f"absent target: {'NONE (right)' if absent is None else f'a box {absent} (wrong)'}")
    print(f"{hits} of {len(TARGETS)} targets found")
    return 0 if hits == len(TARGETS) and absent is None else 1


if __name__ == "__main__":
    sys.exit(main())
