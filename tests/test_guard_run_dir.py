"""tools/guard_run_dir.py, the Claude Code hook that refuses shell commands naming the .run folder. Run as
Claude Code runs it: a separate process, the hook's JSON on stdin, the decision on stdout."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "tools" / "guard_run_dir.py"


def run_guard(payload) -> dict | None:
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    out = subprocess.run([sys.executable, "-I", "-S", str(GUARD)], input=data, capture_output=True,
                         timeout=60, check=True).stdout
    return json.loads(out) if out.strip() else None


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cat .run/token"),
    ("Bash", "cat ./.run/meta_api_key | head -c 4"),
    ("Bash", "python -c \"print(open('.run/llama_api_key').read())\""),
    ("Bash", "cd /e/meta_hackathon && ls -la .run"),
    ("Bash", "tail E:/meta_hackathon/.RUN/service.log"),
    ("Bash", "python - <<'EOF'\nfrom pathlib import Path\nprint(Path(\".run\").iterdir())\nEOF"),
    ("PowerShell", r"Get-Content .run\token"),
    ("PowerShell", r"Get-Content 'E:\meta_hackathon\.run\outbound.log' -Tail 5"),
    ("PowerShell", "Get-ChildItem -Force .run"),
])
def test_commands_naming_the_run_folder_are_refused(tool, command):
    out = run_guard({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command}})
    decision = out["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse" and decision["permissionDecision"] == "deny"
    assert ".run folder" in decision["permissionDecisionReason"]


@pytest.mark.parametrize("tool,command", [
    ("Bash", "git status --short"),
    ("Bash", ".venv/Scripts/python.exe -m pytest -q"),
    ("Bash", "python -c \"import subprocess; subprocess.run(['git', 'log', '-1'])\""),
    ("Bash", "grep -n run_dir src/nanokvm_mcp/config.py"),
    ("Bash", "echo cfg.run_dir / 'token' && npm run build --dry-run"),
    ("PowerShell", "Get-ChildItem tools"),
])
def test_other_commands_pass(tool, command):
    assert run_guard({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command}}) is None


def test_only_shell_tools_are_checked_and_unreadable_input_is_refused():
    # the file tools are kept out of .run by the permission deny rules, not by this hook
    assert run_guard({"tool_name": "Read", "tool_input": {"file_path": "E:/meta_hackathon/.run/token"}}) is None
    for bad in ({"tool_name": "Bash", "tool_input": "cat .run/token"}, {"tool_name": "PowerShell"}, b"not json"):
        assert run_guard(bad)["hookSpecificOutput"]["permissionDecision"] == "deny"
