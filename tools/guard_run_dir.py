"""Claude Code PreToolUse hook: refuse Bash and PowerShell commands that name this project's .run folder.

.run\\ holds the Meta API key, the local model server's key, the service token, the audit log of what was
sent to Muse Spark and the redaction terms. The permission deny rules in .claude\\settings.local.json
(Read(...) and Edit(...)) keep Claude Code's file tools out of it, but while the Bash sandbox is off they
don't cover shell commands. This hook covers the obvious cases: a command that names .run as a path
(cat .run/token, Get-Content .run\\token, open(".run/..."), E:\\meta_hackathon\\.run\\...) is refused
before it runs, whatever the permission mode.

It guards against accidents; it isn't a security boundary. A command that builds the path some other way
isn't caught, and programs that read .run themselves (git's pre-commit hook, the nanokvm command, the
service) aren't affected: they need those files.

Reads the hook's JSON from stdin. Prints a deny decision on stdout when the command names .run, and
nothing otherwise. Standard library only (run with -I -S).
"""
from __future__ import annotations

import json
import re
import sys

# ".run" as a path segment: not the end of a longer name (cfg.run_dir, subprocess.run(, .runner), and
# followed by a path separator, quote, space, shell punctuation or the end of the command.
RUN_DIR = re.compile(r"(?<![\w.-])\.run(?=$|[\\/\s\"'`;|&<>(),*?\[\]{}=:])", re.IGNORECASE)
SHELL_TOOLS = {"Bash", "PowerShell"}
REASON = ("This command names the project's .run folder, which holds API keys, the service token and the "
          "audit log. Claude Code may not read or change anything in it (docs/claude-code.md, section 3). "
          "If something there is needed, ask the user to look at it themselves.")


def names_run_dir(text: str) -> bool:
    return bool(RUN_DIR.search(text or ""))


def decide(payload: dict) -> str | None:
    """Why this tool call is refused, or None to let it through."""
    if payload.get("tool_name") not in SHELL_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return "the hook couldn't find the command to check, so it refuses"
    return REASON if names_run_dir(command) else None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except ValueError:
        payload = None
    reason = decide(payload) if isinstance(payload, dict) else "the hook couldn't read its input, so it refuses"
    if reason:
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
