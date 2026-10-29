"""Claude Code PreToolUse hook: refuse shell commands that would read or change this project's .run folder.

.run\\ holds the Meta API key, the local model server's key, the service token, the audit log of what was
sent to Muse Spark, the redaction terms and saved screenshots. The permission deny rules in
.claude\\settings.local.json (Read(...) and Edit(...)) keep Claude Code's file tools out of it, but while the
Bash sandbox is off they don't cover shell commands. This hook covers the common cases for the Bash,
PowerShell and Monitor tools, whatever the permission mode:

  * a command that names .run as a path (cat .run/token, Get-Content .run\\token, open(".run/..."),
    E:\\meta_hackathon\\.run\\...), unless the name only appears in an exclusion such as --exclude-dir=.run;
  * a recursive grep, rg with --no-ignore or -u, or find with -exec that doesn't exclude .run: these reach
    the folder without naming it.

It guards against mistakes; it isn't a security boundary. It reads only the command line, so a script saved
to a file and then run, code that uses the project's own Config.run_dir, or a command that copies or walks
the whole project some other way (tar, cp -r, a PowerShell Get-ChildItem -Recurse pipeline) isn't caught.
Programs that read .run themselves (the nanokvm command, a local git pre-commit hook, the service) aren't
affected: they need those files.

Reads the hook's JSON from stdin. Prints a deny decision on stdout when the command would reach .run or the
input can't be checked, and nothing otherwise. Standard library only (run with -I -S).
"""
from __future__ import annotations

import json
import re
import sys

# ".run" as a path segment: not part of a longer name (cfg.run_dir, .runner) or a method call (subprocess.run(,
# ).run(, ].run(), and followed by a path separator, quote, space, shell punctuation or the end.
RUN_DIR = re.compile(r"(?<![\w.\-)\]])\.run(?=$|[\\/\s\"'`;|&<>),*?\[\]{}=:])", re.IGNORECASE)
# Exclusions that keep a search or copy out of the folder. They're removed before RUN_DIR is applied, and a
# recursive search that has one passes.
EXCLUDE = re.compile(
    r"""(?:--exclude(?:-dir)?[= ]|(?:-g|--glob|--iglob)[= ]?['"]?!)['"]?(?:\./)?\.run(?:/\*\*|/\*|/)?['"]?(?=\s|$)"""
    r"""|-path\s+['"]?(?:\./|\*/)?\.run['"]?\s+-prune"""
    r"""|(?:-not|!)\s+-path\s+['"]?(?:\./|\*/)?\.run(?:/\*)?['"]?(?=\s|$)""",
    re.IGNORECASE)
# Commands that reach .run without naming it, checked per simple command (between ; | & and newlines).
WALKS = [
    ("a recursive grep", re.compile(
        r"(?:^|[\s(`])[ef]?grep\b(?=.*\s(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive|--dereference-recursive|"
        r"-d\s*recurse|--directories=recurse)(?=\s|$))")),
    ("rg with --no-ignore or -u", re.compile(
        r"(?:^|[\s(`])rg\b(?=.*\s(?:-u+|--unrestricted|--no-ignore(?:-vcs|-dot|-global|-parent)?)(?=\s|$))")),
    ("find with -exec", re.compile(r"(?:^|[\s(`])find\b(?=.*\s-(?:exec|execdir|ok|okdir)(?=\s|$))")),
]
SIMPLE_COMMAND = re.compile(r"\|\||&&|[;|&\n]")
SHELL_TOOLS = {"Bash", "PowerShell"}

TEXT_ONLY = (" If the name is only in text (a commit message, PR body or search pattern), write the text to a file "
             "with the Write tool and pass the file (git commit -F, gh pr create --body-file), or search with the "
             "Grep tool, which skips git-ignored folders.")
NAMES_IT = ("This command names the project's .run folder, which holds API keys, the service token and the audit "
            "log. Claude Code may not read or change anything in it (docs/claude-code.md, section 3); if something "
            "there is needed, ask the user to look at it themselves." + TEXT_ONLY)
REACHES_IT = ("This command uses {what} without excluding the project's .run folder (API keys, the service token, "
              "the audit log), so it would reach that folder without naming it. Exclude it (grep "
              "--exclude-dir=.run, rg -g '!.run', find -path ./.run -prune), or search with the Grep tool, which "
              "skips git-ignored folders.")
CANT_CHECK = "the hook couldn't read the command to check, so it refuses it"


def check_command(command: str) -> str | None:
    """Why this command line is refused, or None."""
    if RUN_DIR.search(EXCLUDE.sub(" ", command)):
        return NAMES_IT
    for part in SIMPLE_COMMAND.split(command):
        if not EXCLUDE.search(part):
            for what, pattern in WALKS:
                if pattern.search(part):
                    return REACHES_IT.format(what=what)
    return None


def decide(payload) -> str | None:
    """Why this tool call is refused, or None to let it through."""
    if not isinstance(payload, dict):
        return CANT_CHECK
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, str):
        return check_command(command)
    if payload.get("tool_name") in SHELL_TOOLS or "tool_name" not in payload:
        return CANT_CHECK          # a shell call without a command to check
    return None                    # nothing runs in a shell (a Monitor watching a WebSocket)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
        reason = decide(json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None)
    except Exception:  # noqa: BLE001  (a command that can't be checked is refused, not let through)
        reason = CANT_CHECK
    if reason:
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
