"""Smoke test for Meta's Model API (Muse Spark) before the pipeline uses it. Sends only dummy text.

Reads the key from E:\\meta_hackathon\\.run\\meta_api_key (one line; never printed or logged) and checks:
  1. GET /v1/status (no key) and GET /v1/models: the key works and muse-spark-1.3 is enabled.
  2. A plain chat completion with store=false: status 200, the reply names exactly the Standard model we asked for
     (Meta's terms say requests "may" be routed to a Contributor model, whose content can be used for training).
  3. Structured output (strict json_schema) and streaming with usage, which the pipeline will use.
Any failed check stops the run with exit code 1. It prints token use and the cost at muse-spark-1.3 Standard
prices; a full run costs well under one cent.

Usage:  E:\\meta_hackathon\\.venv\\Scripts\\python.exe E:\\meta_hackathon\\tools\\spark_smoke.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://api.meta.ai/v1"
MODEL = "muse-spark-1.3"
# Standard (non-contributor) IDs; content sent to these is not used to train Meta's models (ToS 5.1).
STANDARD = {"muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.1", "muse-wonder-1.0"}
PRICE_PER_M = {"input": 1.25, "cached": 0.15, "output": 4.25}  # USD, muse-spark-1.3 Standard
KEY_RE = re.compile(r"LLM_\d+_[!-~]+")  # Meta's documented form LLM_<numeric id>_<secret>: printable ASCII


def load_key() -> str:
    path = ROOT / ".run" / "meta_api_key"
    if not path.exists():
        sys.exit(f"No key file. Create a key at dev.meta.ai, save it as the only line of {path} "
                 "(not in chat), then run this again.")
    raw = path.read_bytes()
    # Windows PowerShell 5.1 writes UTF-16 for '>' and Out-File, and a UTF-8 BOM for -Encoding utf8.
    text = raw.decode("utf-16", errors="replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else raw.decode("utf-8-sig", errors="replace")
    key = text.strip()
    if not key:
        sys.exit(f"{path} is empty.")
    # A second line (a note, or the key pasted twice) would otherwise end up in an exception message that
    # prints the whole Authorization header. Never echo the file's contents.
    if not KEY_RE.fullmatch(key):
        sys.exit(f"{path} must hold only the API key (LLM_<id>_<secret>) on one line, with no notes, spaces "
                 "or second copy. Its contents are not shown here.")
    return key


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib would follow a redirect to any host, even plain http, and send the Authorization header again.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


OPENER = urllib.request.build_opener(_NoRedirect)  # TLS certificate checks stay on (the default context)


def request(method: str, path: str, key: str | None, body: dict | None = None, timeout: float = 120.0,
            accept: str = "application/json"):
    req = urllib.request.Request(BASE_URL + path, method=method,
                                 headers={"Accept": accept, "User-Agent": "nanokvm-spark-smoke/1"})
    if key:
        req.add_unredirected_header("Authorization", f"Bearer {key}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    return OPENER.open(req, timeout=timeout)


def call_json(method: str, path: str, key: str | None, body: dict | None = None):
    """Returns (status, payload, headers); headers is the stdlib message object, so lookups ignore case."""
    try:
        with request(method, path, key, body) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw[:300].decode(errors="replace")}
        return e.code, payload, e.headers or {}


def explain(status: int, payload: dict) -> str:
    err = payload.get("error", payload)
    hint = {401: "the key was rejected", 402: "billing: add a payment method or credits",
            403: "not allowed for this account or region", 404: "unknown path or model",
            429: "rate limited", 504: "server time limit"}.get(status, "")
    return f"HTTP {status} {hint}: {json.dumps(err)[:400]}"


def check_model(reply: dict) -> str:
    got = reply.get("model")
    if got != MODEL:  # exact match only: an unknown, suffixed or Contributor ID stops the run
        raise SystemExit(f"STOP: asked for {MODEL} but the reply came from {got!r}. Do not send real text.")
    return got


def cost(usage: dict) -> float:
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    fresh = (usage.get("prompt_tokens", 0) or 0) - cached
    out = usage.get("completion_tokens", 0) or 0
    return (fresh * PRICE_PER_M["input"] + cached * PRICE_PER_M["cached"] + out * PRICE_PER_M["output"]) / 1e6


def chat_body(messages: list, **extra) -> dict:
    # store=false explicitly (the default on Chat Completions); temperature/top_p left at Meta's tuned defaults;
    # max_completion_tokens counts reasoning tokens too, so leave room beyond the visible answer.
    body = {"model": MODEL, "store": False, "reasoning_effort": "low", "max_completion_tokens": 2048,
            "messages": messages}
    body.update(extra)
    return body


def run_checks(key: str) -> int:
    total = 0.0

    status, payload, _ = call_json("GET", "/status", None)
    print(f"[status] HTTP {status} {json.dumps(payload)[:120]}")

    status, payload, headers = call_json("GET", "/models", key)
    if status != 200:
        print("[models]", explain(status, payload))
        return 1
    ids = [m.get("id", "") for m in payload.get("data", [])]
    print(f"[models] {len(ids)} enabled: {', '.join(ids)}")
    if MODEL not in ids:
        print(f"[models] {MODEL} is not enabled for this key")
        return 1

    messages = [{"role": "developer", "content": "You are a connectivity test. Answer in at most five words."},
                {"role": "user", "content": "Reply with exactly: SMOKE OK"}]
    t0 = time.monotonic()
    status, reply, headers = call_json("POST", "/chat/completions", key, chat_body(messages))
    if status != 200:
        print("[chat]", explain(status, reply))
        return 1
    got = check_model(reply)
    choice = reply["choices"][0]
    usage = reply.get("usage", {})
    total += cost(usage)
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    print(f"[chat] {time.monotonic() - t0:.1f} s, model {got}, finish {choice.get('finish_reason')}, "
          f"refusal {choice['message'].get('refusal')!r}, content {choice['message'].get('content')!r}")
    print(f"[chat] tokens: prompt {usage.get('prompt_tokens')}, completion {usage.get('completion_tokens')} "
          f"(reasoning {reasoning}); rate limit left: {headers.get('x-ratelimit-remaining-requests')} requests")

    schema = {"type": "object", "additionalProperties": False, "required": ["ok", "word"],
              "properties": {"ok": {"type": "boolean"}, "word": {"type": "string"}}}
    status, reply, _ = call_json("POST", "/chat/completions", key, chat_body(
        [{"role": "user", "content": "Return ok=true and word set to the word 'smoke'."}],
        response_format={"type": "json_schema", "json_schema": {"name": "smoke", "strict": True, "schema": schema}}))
    if status != 200:
        print("[json]", explain(status, reply))
        return 1
    check_model(reply)
    total += cost(reply.get("usage", {}))
    content = reply["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(content)
    except ValueError:
        print(f"[json] NOT valid JSON: {content[:200]!r}")
        return 1
    if not (isinstance(parsed, dict) and set(parsed) == {"ok", "word"}
            and isinstance(parsed["ok"], bool) and isinstance(parsed["word"], str)):
        print(f"[json] valid JSON but it does not match the schema: {parsed!r}")
        return 1
    print(f"[json] valid and matches the schema: {parsed}")

    body = chat_body([{"role": "user", "content": "Count from one to five in words."}],
                     stream=True, stream_options={"include_usage": True})
    parts, usage, model_seen, done = [], {}, set(), False
    try:
        with request("POST", "/chat/completions", key, body, accept="text/event-stream") as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                chunk = json.loads(data)
                if "error" in chunk or chunk.get("type") == "error":
                    print("[stream] error event:", json.dumps(chunk)[:300])
                    return 1
                if "model" in chunk:
                    model_seen.add(check_model(chunk))  # every chunk that names a model must name ours
                for ch in chunk.get("choices") or []:
                    parts.append((ch.get("delta") or {}).get("content") or "")
                usage = chunk.get("usage") or usage
    except urllib.error.HTTPError as e:
        print("[stream]", explain(e.code, {"raw": (e.read() if e.fp else b"")[:300].decode(errors="replace")}))
        return 1
    if not (done and usage and model_seen):
        print(f"[stream] FAILED: {'complete' if done else 'no [DONE] marker'}, "
              f"usage {'present' if usage else 'missing'}, models {sorted(model_seen)}")
        return 1
    total += cost(usage)
    print(f"[stream] complete, text {''.join(parts)!r}, usage present, models {sorted(model_seen)}")

    print(f"All checks passed. Approximate cost of this run: ${total:.5f}")
    return 0


def main() -> int:
    assert MODEL in STANDARD and "contributor" not in MODEL
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    key = load_key()
    try:
        return run_checks(key)
    except Exception as e:  # noqa: BLE001
        # Only the exception type: some exception messages repeat request headers, and so the key.
        print(f"[error] {type(e).__name__}; stopped")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
