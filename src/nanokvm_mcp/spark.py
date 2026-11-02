"""Client for Muse Spark through Meta's Model API: text only, Standard models only, store=false.

Safeguards, from Meta's documentation (checked 24 Oct 2026):
- Standard model IDs only. Content sent to them isn't used to train Meta's models (Terms of Service
  5.1); -contributor IDs are refused. Meta's terms say a request "may" be routed to a Contributor
  model, so every streamed chunk must name exactly the model we asked for, or the reply is discarded.
- Chat Completions with store=false (the Responses API stores by default).
- Text only: a message part that isn't text, or any "data:image" string, is refused before sending.
- Streaming, because long non-streamed requests end in 504 gateway_timeout.
- No temperature or top_p (Spark is tuned for its defaults).
- The key is read from .run/meta_api_key, is never printed or logged, and isn't sent on redirects
  (redirects are refused).
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import Config

BASE_URL = "https://api.meta.ai/v1"
STANDARD = {"muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.1", "muse-wonder-1.0"}
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")      # "none" returns 400 for Spark
# USD per million tokens (input, cached input, output); unknown models are costed at Muse Wonder's prices
PRICES = {"muse-spark-1.3": (1.25, 0.15, 4.25), "muse-wonder-1.0": (4.0, 4.0, 20.0)}
KEY_RE = re.compile(r"LLM_\d+_[!-~]+")


class SparkError(RuntimeError):
    pass


class SparkModelMismatch(SparkError):
    """A reply came from a model other than the Standard one requested; its content is discarded."""


@dataclass
class SparkReply:
    content: str
    finish_reason: str
    model: str
    tool_calls: list[dict] = field(default_factory=list)   # [{"id", "name", "arguments" (JSON text)}]
    refusal: str | None = None
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0


def cost_usd(model: str, usage: dict) -> float:
    p_in, p_cached, p_out = PRICES.get(model, PRICES["muse-wonder-1.0"])
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    fresh = (usage.get("prompt_tokens", 0) or 0) - cached
    return (fresh * p_in + cached * p_cached + (usage.get("completion_tokens", 0) or 0) * p_out) / 1e6


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


class SparkClient:
    def __init__(self, cfg: Config, base_url: str | None = None) -> None:
        if base_url is not None and not cfg.simulate:
            raise ValueError("the Meta API address can only be changed in simulate mode (tests)")
        if cfg.spark_model not in STANDARD or "contributor" in cfg.spark_model:
            raise ValueError(f"spark_model must be a Standard model ID ({', '.join(sorted(STANDARD))}), "
                             f"not {cfg.spark_model!r}")
        self.cfg = cfg
        self.base_url = base_url or BASE_URL
        self.model = cfg.spark_model
        self._opener = urllib.request.build_opener(_NoRedirect)   # TLS certificate checks stay on

    def _key(self) -> str:
        path = self.cfg.run_dir / "meta_api_key"
        try:
            raw = path.read_bytes()
        except OSError:
            raise SparkError(f"no Meta API key: save it as the only line of {path}") from None
        text = raw.decode("utf-16", errors="replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
            else raw.decode("utf-8-sig", errors="replace")
        key = text.strip()
        if not KEY_RE.fullmatch(key):     # never echo the file: a second line would end up in a header error
            raise SparkError(f"{path} must hold only the API key (LLM_<id>_<secret>) on one line")
        return key

    @staticmethod
    def _text_only(messages: list[dict]) -> None:
        for m in messages:
            content = m.get("content")
            parts = content if isinstance(content, list) else [content]
            for part in parts:
                if isinstance(part, dict):
                    if part.get("type") != "text":
                        raise ValueError("only text may be sent to Muse Spark")
                    part = part.get("text")
                if part is not None and not isinstance(part, str):
                    raise ValueError("only text may be sent to Muse Spark")
                if part and "data:image" in part:
                    raise ValueError("refusing to send an inline image (data:image) to Muse Spark")

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None, reasoning_effort: str | None = None,
             max_completion_tokens: int | None = None, parallel_tool_calls: bool | None = None,
             timeout: float = 600.0) -> SparkReply:
        self._text_only(messages)
        effort = reasoning_effort or self.cfg.spark_reasoning
        if effort not in EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {EFFORTS}")
        body: dict = {"model": self.model, "store": False, "messages": messages, "stream": True,
                      "stream_options": {"include_usage": True}, "reasoning_effort": effort,
                      "max_completion_tokens": max_completion_tokens or self.cfg.spark_max_tokens}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
            if parallel_tool_calls is not None:
                body["parallel_tool_calls"] = parallel_tool_calls
        data = json.dumps(body).encode("utf-8")
        for attempt in range(3):
            req = urllib.request.Request(self.base_url + "/chat/completions", data=data, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Accept": "text/event-stream",
                                                  "User-Agent": "nanokvm-pipeline/1"})
            req.add_unredirected_header("Authorization", f"Bearer {self._key()}")
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    return self._read_stream(resp)
            except urllib.error.HTTPError as e:
                payload = (e.read() if e.fp else b"")[:500].decode(errors="replace")
                if e.code in (429, 503) and attempt < 2:
                    time.sleep(min(60.0, float(e.headers.get("Retry-After") or 5)))
                    continue
                raise SparkError(f"Meta API error {e.code}: {payload}") from None
            except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError) as e:
                raise SparkError(f"cannot reach Meta's API ({type(e).__name__})") from None
        raise SparkError("Meta API kept answering 429/503")

    def _check_model(self, got) -> None:
        if got != self.model:
            raise SparkModelMismatch(f"asked for {self.model} but the reply came from {got!r}; reply discarded")

    def _read_stream(self, resp) -> SparkReply:
        content: list[str] = []
        refusal: list[str] = []
        calls: dict[int, dict] = {}
        finish, usage, models, done = "", {}, set(), False
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                done = True
                break
            chunk = json.loads(payload)
            if "error" in chunk or chunk.get("type") == "error":
                raise SparkError(f"Meta API error in the stream: {json.dumps(chunk)[:300]}")
            if "model" in chunk:
                self._check_model(chunk["model"])
                models.add(chunk["model"])
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                content.append(delta.get("content") or "")
                refusal.append(delta.get("refusal") or "")
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] = fn.get("name") or slot["name"]
                    slot["arguments"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
            usage = chunk.get("usage") or usage
        if not done or not models:
            raise SparkError("the reply stream ended early (no [DONE] or no model named)")
        return SparkReply(content="".join(content), finish_reason=finish, model=self.model,
                          tool_calls=[calls[i] for i in sorted(calls)], refusal="".join(refusal) or None,
                          usage=usage, cost_usd=cost_usd(self.model, usage))
