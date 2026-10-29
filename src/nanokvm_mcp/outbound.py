"""The outbound gate: the only path from screen text to Muse Spark.

1. Redact the screen text and the question (built-in patterns plus .run/redact_terms.txt).
2. Glimmer, on this laptop, trims the redacted text to the lines the question needs. The excerpt is
   rebuilt from the redacted lines themselves, so it can only contain text that was on the screen.
3. Redact the outgoing text again with the same mapping.
4. Checks; if any fails, nothing is sent:
   - outbound = "local_only";
   - free credits: while meta_billing = "free_credits", only content marked as stand-in may go (Meta's
     Billing page: "While using free credits your content may be used for product improvement");
   - an original redacted value is still present;
   - an inline image (data:image);
   - more than outbound_max_chars;
   - the spend recorded in the audit log has reached meta_budget_usd;
   - .run/outbound.halt exists (written when a reply came from an unexpected model);
   - outbound = "approve" and the user declines after seeing the exact text.
5. The exact text is written to .run/outbound.log before sending; the reply is logged after.
6. Spark's answer has its placeholders restored here, never before sending.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from . import redact as R
from .config import Config
from .glimmer import GlimmerClient, GlimmerError
from .spark import SparkClient, SparkModelMismatch, SparkReply

OUTBOUND_MODES = ("auto", "approve", "local_only")
BILLING_MODES = ("free_credits", "paid")


class OutboundBlocked(RuntimeError):
    """Nothing was sent; the message says why."""


@dataclass
class Prepared:
    text: str                                   # exactly what would go to Meta (redacted, trimmed)
    question: str                               # the redacted question, included in text
    mapping: dict[str, str] = field(default_factory=dict)
    findings: dict[str, int] = field(default_factory=dict)
    raw_sha256: str = ""
    frame_sha256: str | None = None
    trimmed: bool = False                       # Glimmer's excerpt was used (not the whole screen text)


@dataclass
class Answer:
    prepared: Prepared
    reply: SparkReply
    content: str                                # Spark's answer, placeholders restored (local only)
    tool_calls: list[dict] = field(default_factory=list)   # arguments parsed and restored


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _restore_json(value, mapping: dict[str, str]):
    if isinstance(value, str):
        return R.restore(value, mapping)
    if isinstance(value, list):
        return [_restore_json(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _restore_json(v, mapping) for k, v in value.items()}
    return value


def history_text(history: list[dict] | None) -> str:
    """The text of the messages after the first user message (Spark's own replayed tool calls and the results
    sent back), so the gate's checks cover them too."""
    out = []
    for m in history or []:
        if isinstance(m.get("content"), str):
            out.append(m["content"])
        for c in m.get("tool_calls") or []:
            out.append((c.get("function") or {}).get("arguments") or "")
    return "\n".join(out)


def excerpt(source: str, picked: str) -> str:
    """The lines of ``source`` that also appear in ``picked`` (Glimmer's selection), in source order.
    Anything Glimmer wrote that wasn't on the screen is dropped."""
    wanted = {" ".join(ln.split()) for ln in picked.splitlines() if ln.strip()}
    return "\n".join(ln for ln in source.splitlines() if ln.strip() and " ".join(ln.split()) in wanted)


class OutboundGate:
    def __init__(self, cfg: Config, glimmer: GlimmerClient, spark: SparkClient,
                 approve: Callable[[str], bool] | None = None, stand_in: bool = False) -> None:
        if cfg.outbound not in OUTBOUND_MODES:
            raise ValueError(f"outbound must be one of {OUTBOUND_MODES}")
        if cfg.meta_billing not in BILLING_MODES:
            raise ValueError(f"meta_billing must be one of {BILLING_MODES}")
        self.cfg, self.glimmer, self.spark = cfg, glimmer, spark
        self.approve = approve
        self.stand_in = stand_in
        self.log_path = cfg.run_dir / "outbound.log"
        self.halt_path = cfg.run_dir / "outbound.halt"

    # ---- audit log (local, never sent anywhere)
    def _audit(self, record: dict) -> None:
        self.cfg.run_dir.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def spent_usd(self) -> float:
        total = 0.0
        try:
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                try:
                    total += float(json.loads(line).get("cost_usd") or 0)
                except (ValueError, AttributeError):
                    continue
        except OSError:
            pass
        return total

    # ---- preparing the text
    def prepare(self, screen_text: str, question: str, frame_sha256: str | None = None, *,
                notes: str | None = None, trim: bool = True) -> Prepared:
        """``notes``: Glimmer's own account of the screen (nanokvm fix), sent as a <screen_notes> block, redacted
        but not trimmed. ``trim=False`` sends every redacted screen line: with no question, Glimmer can't know
        which lines matter, and trimming could drop the one with the error."""
        terms = R.load_terms(self.cfg.run_dir / "redact_terms.txt")
        r1 = R.redact(screen_text, terms)
        q = R.redact(question, terms, mapping=r1.mapping)
        n = R.redact(notes, terms, mapping=q.mapping) if notes else None
        findings = dict(r1.findings)
        for part in (q, n):
            for k, v in (part.findings if part else {}).items():
                findings[k] = findings.get(k, 0) + v
        body, trimmed = r1.text, False
        if trim:
            try:
                picked = self.glimmer.minimise(r1.text, q.text).content
                cut = excerpt(r1.text, picked)
                if cut:
                    body, trimmed = cut, True
            except GlimmerError:
                pass                              # sends the whole redacted text, which is still redacted
        block = f"<screen_notes>\n{n.text}\n</screen_notes>\n\n" if n else ""
        text = f"{q.text}\n\n{block}<screen_text>\n{body}\n</screen_text>"
        r2 = R.redact(text, terms, mapping=(n or q).mapping)    # second pass: anything Glimmer let back through
        for k, v in r2.findings.items():
            findings[k] = findings.get(k, 0) + v
        return Prepared(r2.text, q.text, r2.mapping, findings, _sha(screen_text), frame_sha256, trimmed)

    def screen_blocks(self, p: Prepared, screen_text: str, notes: str | None = None) -> str:
        """A fresh reading of the screen for a later message (nanokvm fix), as <screen_notes> and <screen_text>
        blocks redacted with the same placeholders. New values join ``p.mapping``, so the checks before every
        request and restoring Spark's replies cover them too. Not trimmed, like fix's first message."""
        terms = R.load_terms(self.cfg.run_dir / "redact_terms.txt")
        s = R.redact(screen_text, terms, mapping=p.mapping)
        n = R.redact(notes, terms, mapping=s.mapping) if notes else None
        block = f"<screen_notes>\n{n.text}\n</screen_notes>\n\n" if n else ""
        r = R.redact(f"{block}<screen_text>\n{s.text}\n</screen_text>", terms, mapping=(n or s).mapping)
        p.mapping.update(r.mapping)
        for part in (s, n, r):
            for k, v in (part.findings if part else {}).items():
                p.findings[k] = p.findings.get(k, 0) + v
        return r.text

    def follow_up(self, p: Prepared, screen_text: str, notes: str | None, intro: str) -> str:
        """A later user message: ``intro``, then the screen read afresh (see screen_blocks)."""
        return f"{intro}\n\n{self.screen_blocks(p, screen_text, notes)}"

    # ---- checks and sending
    def check(self, p: Prepared, history: list[dict] | None = None) -> None:
        text = p.text + ("\n" + history_text(history) if history else "")
        if self.cfg.outbound == "local_only":
            raise OutboundBlocked("outbound = local_only: nothing is sent to Meta")
        if self.cfg.meta_billing == "free_credits" and not self.stand_in:
            raise OutboundBlocked("the Meta account is on free credits, and Meta says free-credit content may be "
                                  "used for product improvement: send only stand-in content (--stand-in), or "
                                  "switch Billing to pay as you go and set meta_billing = \"paid\"")
        if self.halt_path.exists():
            raise OutboundBlocked(f"{self.halt_path} exists (a reply came from an unexpected model); "
                                  "delete it after checking .run/outbound.log")
        if R.leaked_values(text, p.mapping):
            raise OutboundBlocked("a redacted value is still in the outgoing text")
        if "data:image" in text:
            raise OutboundBlocked("the outgoing text contains an inline image")
        if len(text) > self.cfg.outbound_max_chars:
            raise OutboundBlocked(f"the outgoing text is {len(text)} characters, over outbound_max_chars "
                                  f"({self.cfg.outbound_max_chars})")
        if self.spent_usd() >= self.cfg.meta_budget_usd:
            raise OutboundBlocked(f"the audit log shows ${self.spent_usd():.2f} spent, at meta_budget_usd")

    def ask(self, p: Prepared, system: str, *, tools: list[dict] | None = None,
            parallel_tool_calls: bool | None = None, history: list[dict] | None = None) -> Answer:
        """One request to Spark. ``history`` continues a conversation after the user message: Spark's earlier
        tool calls replayed as it sent them (redacted), and the results sent back; it is checked and logged too."""
        rid = uuid.uuid4().hex[:12]
        base = {"id": rid, "outbound": self.cfg.outbound, "billing": self.cfg.meta_billing,
                "stand_in": self.stand_in, "findings": p.findings, "raw_sha256": p.raw_sha256,
                "frame_sha256": p.frame_sha256, "trimmed": p.trimmed, "model": self.spark.model,
                "turn": 1 + sum(1 for m in history or [] if m.get("role") == "assistant")}
        extra = history_text(history)
        try:
            self.check(p, history)
            if self.cfg.outbound == "approve" and not (
                    self.approve and self.approve(p.text + (f"\n\n--- then ---\n{extra}" if extra else ""))):
                raise OutboundBlocked("not approved: nothing was sent")
        except OutboundBlocked as e:
            self._audit({**base, "event": "blocked", "reason": str(e), "sent_sha256": _sha(p.text)})
            raise
        self._audit({**base, "event": "request", "sent_sha256": _sha(p.text), "sent_text": p.text,
                     "history": history or [], "system": system, "tools": [t["function"]["name"] for t in tools or []]})
        messages = [{"role": "developer", "content": system}, {"role": "user", "content": p.text}, *(history or [])]
        try:
            reply = self.spark.chat(messages, tools=tools, parallel_tool_calls=parallel_tool_calls)
        except SparkModelMismatch as e:
            self.halt_path.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} request {rid}: {e}\n", encoding="utf-8")
            self._audit({"id": rid, "event": "halt", "error": str(e)})
            raise
        except Exception as e:
            self._audit({"id": rid, "event": "error", "error": f"{type(e).__name__}: {e}"})
            raise
        self._audit({"id": rid, "event": "response", "response_model": reply.model, "finish": reply.finish_reason,
                     "usage": reply.usage, "cost_usd": round(reply.cost_usd, 6), "content": reply.content,
                     "tool_calls": reply.tool_calls, "refusal": reply.refusal})
        calls = []
        for c in reply.tool_calls:
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {"_unparsed": c["arguments"]}
            calls.append({"id": c["id"], "name": c["name"], "arguments": _restore_json(args, p.mapping)})
        return Answer(p, reply, R.restore(reply.content, p.mapping), calls)
