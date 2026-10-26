"""Score the running Glimmer server on real captured screens of DUMMY files (the A/B for picking the setup).

Each argument pairs a captured frame with the file shown in it:
  capture.png=tools\\ab_screens\\ring_buffer.cpp     code: transcribed with line numbers, compared line by line
  errors.png=tools\\ab_screens\\build_errors.txt     terminal: error and warning lines, compared as a set
Code is scored on the line numbers Glimmer reports (the part of the file visible on screen):
  exact     lines identical, indentation included
  text      lines equal once runs of whitespace are collapsed
  cer       character error rate over the visible range (edit distance / characters)
  missing   line numbers inside the visible range that were not reported
By default only metrics are printed; --show-diffs prints mismatched lines (dummy content only).

Usage (server started with tools\\run-glimmer.ps1):
  E:\\meta_hackathon\\.venv\\Scripts\\python.exe E:\\meta_hackathon\\tools\\glimmer_ab.py --label unsloth-dflash ^
      --runs 2 --out results.jsonl light.png=tools\\ab_screens\\ring_buffer.cpp errors.png=tools\\ab_screens\\build_errors.txt
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import time
from pathlib import Path

import cv2

from glimmer_smoke import NO_THINKING, ROOT, call

CODE_PROMPT = ("Transcribe the code in this screenshot exactly. Output one line per code line, each starting with "
               "its line number as shown in the editor and a space, preserving indentation and every character. "
               "Output only the transcription.")
ERRORS_PROMPT = ("List every compiler error and warning line in this screenshot exactly as shown, one per line, "
                 "with nothing else.")
NUMBERED = re.compile(r"^\s*(\d+)(?: (.*))?$")


def norm(s: str) -> str:
    return " ".join(s.split())


def edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def parse_numbered(text: str) -> dict[int, str]:
    """Line number -> text. An unnumbered line continues the previous one (a wrapped line on screen)."""
    out: dict[int, str] = {}
    last = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("```"):
            continue
        m = NUMBERED.match(line)
        if m and (last is None or int(m.group(1)) > last):
            last = int(m.group(1))
            out[last] = m.group(2) or ""
        elif last is not None:
            out[last] += " " + line.strip()
    return out


def score_code(truth: list[str], got: dict[int, str]) -> dict:
    nums = sorted(n for n in got if 1 <= n <= len(truth))
    if not nums:
        return {"reported": 0, "exact": 0, "text": 0, "cer": 1.0, "missing": None, "beyond_file": len(got)}
    lo, hi = nums[0], nums[-1]
    visible = range(lo, hi + 1)
    exact = sum(1 for n in nums if got[n].rstrip() == truth[n - 1].rstrip())
    text = sum(1 for n in nums if norm(got[n]) == norm(truth[n - 1]))
    want = "\n".join(norm(truth[n - 1]) for n in visible)
    have = "\n".join(norm(got.get(n, "")) for n in visible)
    return {"reported": len(nums), "range": [lo, hi], "exact": exact, "text": text,
            "cer": round(edit_distance(want, have) / max(1, len(want)), 4),
            "missing": [n for n in visible if n not in got], "beyond_file": len(got) - len(nums)}


def score_errors(truth: list[str], content: str) -> dict:
    want = {norm(t) for t in truth if t.strip()}
    have = {norm(l.strip("`").strip()) for l in content.splitlines() if l.strip() and not l.strip().startswith("```")}
    return {"expected": len(want), "found": len(want & have), "extra": len(have - want)}


def image_url(path: Path, fmt: str) -> str:
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"cannot read {path}")
    ok, buf = cv2.imencode(".png" if fmt == "png" else ".jpg", img,
                           [] if fmt == "png" else [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return f"data:image/{'png' if fmt == 'png' else 'jpeg'};base64," + base64.b64encode(buf.tobytes()).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="+", help="FRAME.png=TRUTH_FILE")
    ap.add_argument("--label", required=True)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--format", choices=["png", "jpeg"], default="png", help="how the frame is sent (jpeg = q95)")
    ap.add_argument("--out", help="append one JSON line per read")
    ap.add_argument("--show-diffs", action="store_true", help="print mismatched lines (dummy content only)")
    a = ap.parse_args()
    key = (ROOT / ".run" / "llama_api_key").read_text(encoding="ascii").strip()
    for pair in a.pairs:
        frame_s, _, truth_s = pair.partition("=")
        frame, truth_path = Path(frame_s), Path(truth_s)
        truth = truth_path.read_text(encoding="utf-8").splitlines()
        is_code = truth_path.suffix != ".txt"
        url = image_url(frame, a.format)
        for run in range(1, a.runs + 1):
            body = {"model": "muse-glimmer", "max_tokens": 4096, "cache_prompt": False,
                    "chat_template_kwargs": {"reasoning_strength": "low"}, **NO_THINKING,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": CODE_PROMPT if is_code else ERRORS_PROMPT},
                        {"type": "image_url", "image_url": {"url": url}}]}]}
            t0 = time.monotonic()
            r = call(a.port, key, "/v1/chat/completions", body)
            wall = time.monotonic() - t0
            msg, tm = r["choices"][0]["message"], r.get("timings", {})
            content = msg.get("content") or ""
            got = parse_numbered(content) if is_code else None
            score = score_code(truth, got) if is_code else score_errors(truth, content)
            rec = {"label": a.label, "frame": frame.name, "truth": truth_path.name, "format": a.format, "run": run,
                   "wall_s": round(wall, 1), "prompt_n": tm.get("prompt_n"),
                   "prompt_s": round(tm.get("prompt_ms", 0) / 1000, 1), "gen_n": tm.get("predicted_n"),
                   "gen_tps": round(tm.get("predicted_per_second", 0), 1),
                   "draft_acceptance": round(tm["draft_n_accepted"] / tm["draft_n"], 2) if tm.get("draft_n") else None,
                   "reasoning_chars": len(msg.get("reasoning_content") or ""),
                   "finish": r["choices"][0].get("finish_reason"), **score}
            shown = {k: v for k, v in rec.items() if k != "missing"}
            if is_code:
                shown["missing"] = len(rec["missing"] or [])
            print(json.dumps(shown), flush=True)
            if a.out:
                with open(a.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            if a.show_diffs and is_code:
                for n in sorted(got):
                    if 1 <= n <= len(truth) and got[n].rstrip() != truth[n - 1].rstrip():
                        print(f"   {n:>3} want: {truth[n - 1]!r}\n       got:  {got[n]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
