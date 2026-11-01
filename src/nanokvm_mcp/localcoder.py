"""Muse Glimmer as the fix loop's coder, on this laptop: Fix it's changes to code when Glimmer plans them (the viewer's
default), and nanokvm edit/fix --local-only. It gets what Muse Spark would get (the same request, system message,
apply_edit tool and conversation), from the local llama-server instead of through the outbound gate, so nothing
leaves the laptop and nothing needs redacting. Screen text still can't open or close the message's own blocks
(outbound.defang), nor end the turn with Glimmer's chat control tokens (glimmer.quote_safe), and its edits are
checked, approved and typed exactly as Spark's are (editing.plan_edit, EditTyper, the approvals).
"""
from __future__ import annotations

import json

from .glimmer import GlimmerClient, quote_safe
from .outbound import Answer, Prepared, defang
from .spark import SparkReply


class LocalCoder:
    """The outbound gate's part in Pipeline.edit (prepare, screen_blocks, follow_up, ask), done here."""

    def __init__(self, glimmer: GlimmerClient) -> None:
        self.glimmer = glimmer

    def prepare(self, screen_text: str, question: str, frame_sha256: str | None = None, *,
                notes: str | None = None, trim: bool = True) -> Prepared:
        """The first message, as the gate builds it, but whole (never trimmed: nothing leaves) and not redacted."""
        return Prepared(f"{question}\n\n{self.screen_blocks(None, screen_text, notes)}", question,
                        frame_sha256=frame_sha256)

    def screen_blocks(self, _p: Prepared | None, screen_text: str, notes: str | None = None) -> str:
        block = f"<screen_notes>\n{defang(quote_safe(notes))}\n</screen_notes>\n\n" if notes else ""
        return f"{block}<screen_text>\n{defang(quote_safe(screen_text))}\n</screen_text>"

    def follow_up(self, p: Prepared, screen_text: str, notes: str | None, intro: str) -> str:
        return f"{intro}\n\n{self.screen_blocks(p, screen_text, notes)}"

    def ask(self, p: Prepared, system: str, *, tools: list[dict] | None = None,
            parallel_tool_calls: bool | None = None, history: list[dict] | None = None) -> Answer:
        """One request to Glimmer, answered as the gate answers for Spark: its tool calls parsed (and, with nothing
        redacted, nothing to restore). It costs nothing and is logged nowhere, since nothing was sent."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": p.text}, *(history or [])]
        reply = self.glimmer.code(messages, tools=tools, parallel_tool_calls=parallel_tool_calls)
        calls = []
        for c in reply.tool_calls:
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {"_unparsed": c["arguments"]}
            calls.append({"id": c["id"], "name": c["name"], "arguments": args})
        return Answer(p, SparkReply(reply.content, reply.finish_reason, "muse-glimmer", reply.tool_calls,
                                    usage=reply.usage), reply.content, calls)
