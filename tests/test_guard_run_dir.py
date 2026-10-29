"""tools/guard_run_dir.py, the Claude Code hook that refuses shell commands reaching the .run folder. Run as Claude
Code runs it: a separate process, the hook's JSON on stdin, the decision on stdout."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tools" / "guard_run_dir.py"


def run_guard(payload, cmd=None, env=None) -> dict | None:
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    out = subprocess.run(cmd or [sys.executable, "-I", "-S", str(GUARD)], input=data, capture_output=True,
                         timeout=60, check=True, env=env).stdout
    return json.loads(out) if out.strip() else None


def decision(tool, command):
    out = run_guard({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command}})
    return out and out["hookSpecificOutput"]


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cat .run/token"),
    ("Bash", "cat ./.run/meta_api_key | head -c 4"),
    ("Bash", "python -c \"print(open('.run/llama_api_key').read())\""),
    ("Bash", "cd /e/meta_hackathon && ls -la .run"),
    ("Bash", "tail E:/meta_hackathon/.RUN/service.log"),
    ("Bash", "python - <<'EOF'\nfrom pathlib import Path\nprint(Path(\".run\").iterdir())\nEOF"),
    ("Bash", "echo $(ls .run)\r\n"),
    ("Bash", "grep -rn x --exclude-dir=.run . ; cat .run/token"),       # an exclusion doesn't hide a read
    ("Bash", "grep -rn x --exclude-dir=.run .run/"),
    ("PowerShell", r"Get-Content .run\token"),
    ("PowerShell", r"Get-Content 'E:\meta_hackathon\.run\outbound.log' -Tail 5"),
    ("PowerShell", "Get-ChildItem -Force .run"),
    ("PowerShell", r"Get-Content \\?\E:\meta_hackathon\.run\token"),
    ("PowerShell", r"Get-Content FileSystem::E:\meta_hackathon\.run\token"),
    ("PowerShell", "Get-Content \"$env:CLAUDE_PROJECT_DIR\\.run\\token\""),
    ("Monitor", "tail -f .run/outbound.log | grep --line-buffered ERROR"),
])
def test_commands_naming_the_run_folder_are_refused(tool, command):
    d = decision(tool, command)
    assert d["hookEventName"] == "PreToolUse" and d["permissionDecision"] == "deny"
    assert "names the project's .run folder" in d["permissionDecisionReason"]
    assert "git commit -F" in d["permissionDecisionReason"]          # the way through for text that names it


@pytest.mark.parametrize("command,what", [
    ("grep -rn LLM_ .", "a recursive grep"),
    ("grep -R --include=*.log error .", "a recursive grep"),
    ("cd src && egrep -r 'a|b' .", "a recursive grep"),
    ("grep --directories=recurse token .", "a recursive grep"),
    ("rg -uu TODO", "rg with --no-ignore or -u"),
    ("rg --no-ignore token", "rg with --no-ignore or -u"),
    ("find . -name '*.log' -exec tail -n 20 {} +", "find with -exec"),
    ("grep -rn --exclude=.run LLM_ .", "a recursive grep"),            # --exclude= skips files, not folders
    ("find /e/meta_hackathon -path ./.run -prune -o -name '*.log' -exec tail {} +", "find with -exec"),
])
def test_searches_that_reach_the_folder_without_naming_it_are_refused(command, what):
    d = decision("Bash", command)
    assert d["permissionDecision"] == "deny" and what in d["permissionDecisionReason"]
    assert "--exclude-dir=.run" in d["permissionDecisionReason"]


@pytest.mark.parametrize("tool,command", [
    ("Bash", "git status --short"),
    ("Bash", ".venv/Scripts/python.exe -m pytest -q"),
    ("Bash", "python -c \"import subprocess; subprocess.run(['git', 'log', '-1'])\""),
    ("Bash", "python -c \"import unittest; unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite())\""),
    ("Bash", "node -e \"require('./scripts/build').run()\" && echo x[0].run()"),
    ("Bash", "grep -n run_dir src/nanokvm_mcp/config.py"),
    ("Bash", "echo cfg.run_dir / 'token' && npm run build --dry-run"),
    ("Bash", "rg TODO src && git grep -n run_dir"),
    ("Bash", 'grep -rn "TODO" --exclude-dir=.venv --exclude-dir=.git --exclude-dir=.run .'),
    ("Bash", 'grep -rn TODO --exclude-dir=".run" . && grep -rn TODO --exclude-dir=./.run/ src'),
    ("Bash", "find /e/meta_hackathon -path '*/.run' -prune -o -name '*.log' -exec tail {} +"),
    ("Bash", "rg -u -g '!.run' TODO && rg -uu --glob '!.run/**' TODO"),
    ("Bash", 'find . -path ./.run -prune -o -name "*.log" -exec tail {} +'),
    ("Bash", "find . -name '*.py' -not -path './.run/*' -exec wc -l {} +"),
    ("Bash", "tar --exclude=.run -czf ../snapshot.tgz ."),
    ("PowerShell", "Get-ChildItem tools; (New-Object -ComObject WScript.Shell).Run('notepad.exe')"),
])
def test_other_commands_pass(tool, command):
    assert decision(tool, command) is None


def test_only_commands_are_checked_and_unreadable_input_is_refused():
    # the file tools are kept out of .run by the permission deny rules, not by this hook
    assert run_guard({"tool_name": "Read", "tool_input": {"file_path": "E:/meta_hackathon/.run/token"}}) is None
    assert run_guard({"tool_name": "Monitor", "tool_input": {"ws": "ws://127.0.0.1:1/x"}}) is None   # no shell
    nested = b"[" * 100000 + b"]" * 100000
    for bad in ({"tool_name": "Bash", "tool_input": "cat .run/token"}, {"tool_name": "PowerShell"}, {},
                {"tool_input": {"command": "cat .run/token"}}, b"not json", b"", b"   ", nested):
        assert run_guard(bad)["hookSpecificOutput"]["permissionDecision"] == "deny"


def _documented_settings() -> dict:
    """The settings block that docs/claude-code.md tells people to copy."""
    text = (ROOT / "docs" / "claude-code.md").read_text(encoding="utf-8")
    block = re.search(r"```json\n(\"permissions\".*?)\n```", text, re.S).group(1)
    return json.loads("{" + block + "}")


def test_the_documented_settings_block_works():
    s = _documented_settings()
    assert {"Read(/.run/**)", "Edit(/.run/**)"} <= set(s["permissions"]["deny"])
    entry = s["hooks"]["PreToolUse"][0]
    hook = entry["hooks"][0]
    assert entry["matcher"] == "Bash|PowerShell|Monitor" and hook["onFailure"] == "block" and hook["shell"] == "bash"
    bash = shutil.which("bash")
    if not bash or "git" not in bash.lower():                         # Git Bash, as Claude Code uses on Windows
        pytest.skip("needs Git Bash")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT))
    out = run_guard({"tool_name": "Bash", "tool_input": {"command": "cat .run/x"}},
                    cmd=[bash, "-c", hook["command"]], env=env)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
