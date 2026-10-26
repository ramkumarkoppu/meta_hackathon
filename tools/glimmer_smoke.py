"""Smoke test for the local Muse Glimmer server (llama-server started by tools\\run-glimmer.ps1).

Renders a DUMMY C++ file as a 1920x1080 image (nothing from either laptop's screen), asks Glimmer to
transcribe it, and reports accuracy against the source, speed and GPU memory.

Usage:  E:\\meta_hackathon\\.venv\\Scripts\\python.exe E:\\meta_hackathon\\tools\\glimmer_smoke.py [--port 8001] [--label unsloth]
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Thinking budget 0 for reading tasks. On llama.cpp b10976 the documented thinking_budget_tokens and
# --reasoning-budget are ignored for Glimmer (its chat handler sets no thinking tags, PR #27475), so the
# budget fields go in each request with Glimmer's own thinking tags. Measured 24 Oct on the dummy code:
# 223 output tokens on every run and 27/27 lines, against 279-610 tokens without it. Re-test after upgrading.
NO_THINKING = {"reasoning_budget_tokens": 0, "reasoning_budget_start_tag": " to=self<|message|>",
               "reasoning_budget_end_tags": ["<|eom|>"], "reasoning_budget_message": ""}

DUMMY_CPP = """#include <string>
#include <vector>

// Parse comma-separated integers from one line of text.
std::vector<int> parse_line(const std::string& line) {
    std::vector<int> out;
    std::string cur;
    for (char c : line) {
        if (c == ',') {
            if (!cur.empty()) out.push_back(std::stoi(cur));
            cur.clear();
        } else if (c != ' ') {
            cur += c;
        }
    }
    if (!cur.empty()) out.push_back(std::stoi(cur));
    return out;
}

// Sum the first n values.
int sum_first(const std::vector<int>& v, int n) {
    int total = 0;
    for (int i = 0; i <= n; ++i) {
        total += v[i];
    }
    return total;
}"""


def render(code: str) -> bytes:
    """Light editor-like rendering: grey line numbers, dark code text, 1920x1080, JPEG quality 95."""
    img = np.full((1080, 1920, 3), 255, np.uint8)
    for i, line in enumerate(code.splitlines(), start=1):
        y = 40 + i * 34
        cv2.putText(img, f"{i:>3}", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 1, cv2.LINE_AA)
        cv2.putText(img, line, (100, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (30, 30, 30), 1, cv2.LINE_AA)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return jpg.tobytes()


def call(port: int, key: str, path: str, body: dict | None = None, timeout: float = 600.0) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 method="GET" if body is None else "POST")
    with NO_PROXY.open(req, timeout=timeout) as r:
        return json.loads(r.read())


def gpu_mem() -> str:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out
    except Exception as e:  # noqa: BLE001
        return f"unavailable ({e})"


def chat(port: int, key: str, reasoning: str, content, max_tokens: int, extra: dict | None = None) -> tuple[dict, float]:
    # Set the reasoning level through the template variable, never as "Reasoning strength: ..." in a system
    # prompt: Unsloth's GGUF has Meta's old template, which then also appends "Reasoning strength: high.".
    t0 = time.monotonic()
    body = {"model": "muse-glimmer", "max_tokens": max_tokens,
            "chat_template_kwargs": {"reasoning_strength": reasoning},
            "messages": [{"role": "user", "content": content}]}
    body.update(extra or {})
    r = call(port, key, "/v1/chat/completions", body)
    return r, time.monotonic() - t0


def normalise(text: str) -> list[str]:
    lines = []
    for line in text.strip().splitlines():
        line = line.strip().strip("`")
        if line and not line.startswith("```"):
            lines.append(" ".join(line.split()))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--label", default="glimmer")
    a = ap.parse_args()
    key = (ROOT / ".run" / "llama_api_key").read_text(encoding="ascii").strip()

    t0 = time.monotonic()
    while True:
        try:
            if call(a.port, key, "/health", timeout=5).get("status") == "ok":
                break
        except Exception:  # noqa: BLE001
            pass
        if time.monotonic() - t0 > 600:
            print("server not ready after 10 minutes")
            return 1
        time.sleep(2)
    print(f"[{a.label}] server ready after {time.monotonic() - t0:.0f} s; GPU memory: {gpu_mem()}")

    r, dt = chat(a.port, key, "low", "Reply with exactly one word: READY", 256)
    print(f"[{a.label}] text test: {r['choices'][0]['message'].get('content', '').strip()!r} in {dt:.1f} s, "
          f"{r.get('timings', {}).get('predicted_per_second', 0):.1f} tok/s")

    jpeg = render(DUMMY_CPP)
    prompt = ("Transcribe the code in this screenshot exactly. Output one line per code line, each starting with "
              "its line number and a space, preserving indentation and every character. Output only the transcription.")
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]
    r, dt = chat(a.port, key, "low", content, 4096, NO_THINKING)
    text = r["choices"][0]["message"].get("content", "")
    if r["choices"][0]["message"].get("reasoning_content"):
        print(f"[{a.label}] WARNING: the thinking cap did not hold (reasoning returned); check the llama.cpp build")
    usage, timings = r.get("usage", {}), r.get("timings", {})
    # Blank source lines count too: the model correctly outputs just their line number.
    expected = [f"{i} {' '.join(l.split())}".strip() for i, l in enumerate(DUMMY_CPP.splitlines(), 1)]
    got = normalise(text)
    ratio = difflib.SequenceMatcher(None, "\n".join(expected), "\n".join(got)).ratio()
    exact = sum(1 for e, g in zip(expected, got) if e == g)
    print(f"[{a.label}] image test: {dt:.1f} s, prompt tokens {usage.get('prompt_tokens')}, "
          f"output tokens {usage.get('completion_tokens')}, {timings.get('predicted_per_second', 0):.1f} tok/s")
    print(f"[{a.label}] transcription: similarity {ratio:.3f}, exact lines {exact}/{len(expected)}")
    print(f"[{a.label}] GPU memory now: {gpu_mem()}")
    for e, g in zip(expected, got):
        if e != g:
            print(f"   expected: {e}\n   got:      {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
