"""The stdin/stdout contract, exercised by actually running the hook scripts.

Payload shapes follow https://code.claude.com/docs/en/hooks (PreToolUse and
SessionStart input examples).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "hooks" / "pwsh_guard.py"
SESSION = ROOT / "hooks" / "session_env_note.py"

# Force the guard to believe it is talking to Windows PowerShell 5.1 regardless
# of the machine the test suite runs on.
PWSH_ENV = {"CCPG_SHELL": "pwsh", "CCPG_PWSH_MAJOR": "5"}


def run_hook(script: Path, payload: dict, env_extra: dict | None = None):
    env = dict(os.environ)
    env.pop("CCPG_MODE", None)
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc


def pretooluse(command: str, tool_name: str = "Bash") -> dict:
    return {
        "session_id": "abc123",
        "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
        "transcript_path": "/home/user/.claude/projects/x/transcript.jsonl",
        "cwd": "C:\\Users\\dev\\project",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {
            "command": command,
            "description": "run something",
            "timeout": 120000,
            "run_in_background": False,
        },
        "tool_use_id": "toolu_01ABC123",
    }


def out_json(proc):
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "expected JSON on stdout, got nothing"
    return json.loads(proc.stdout)


# ------------------------------------------------------------ silence path


def test_clean_command_produces_no_output():
    proc = run_hook(GUARD, pretooluse("Get-ChildItem -Recurse"), PWSH_ENV)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_non_bash_tool_is_ignored():
    payload = pretooluse("shopt -s extglob", tool_name="Read")
    proc = run_hook(GUARD, payload, PWSH_ENV)
    assert proc.stdout.strip() == ""


def test_bypass_marker_passes_through():
    proc = run_hook(GUARD, pretooluse("shopt -s extglob  # pwsh-guard: ignore"), PWSH_ENV)
    assert proc.stdout.strip() == ""


def test_mode_off_disables_everything():
    env = dict(PWSH_ENV, CCPG_MODE="off")
    proc = run_hook(GUARD, pretooluse("shopt -s extglob"), env)
    assert proc.stdout.strip() == ""


def test_guard_stands_down_when_shell_is_bash():
    env = {"CCPG_SHELL": "bash"}
    proc = run_hook(GUARD, pretooluse("shopt -s extglob"), env)
    assert proc.stdout.strip() == ""


def test_empty_stdin_does_not_crash():
    proc = subprocess.run([sys.executable, str(GUARD)], input="", capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_malformed_stdin_does_not_block():
    proc = subprocess.run(
        [sys.executable, str(GUARD)], input="{not json", capture_output=True, text=True
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_missing_tool_input_does_not_crash():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    proc = run_hook(GUARD, payload, PWSH_ENV)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ---------------------------------------------------------------- deny path


def test_extglob_is_denied_with_guidance():
    proc = run_hook(GUARD, pretooluse("shopt -s extglob; rm !(keep.txt|keep2.txt)"), PWSH_ENV)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["hookEventName"] == "PreToolUse"
    assert body["permissionDecision"] == "deny"
    reason = body["permissionDecisionReason"]
    assert "shopt" in reason
    assert "Get-ChildItem" in reason
    assert "pwsh-guard: ignore" in reason
    assert "updatedInput" not in body


def test_deny_reports_partial_rewrite():
    proc = run_hook(GUARD, pretooluse("shopt -s extglob; echo $PATH"), PWSH_ENV)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
    assert "$env:PATH" in body["permissionDecisionReason"]


def test_ps7_does_not_deny_chain_operators():
    env = {"CCPG_SHELL": "pwsh", "CCPG_PWSH_MAJOR": "7"}
    proc = run_hook(GUARD, pretooluse("npm ci && npm test"), env)
    assert proc.stdout.strip() == "", "&& is valid on PowerShell 7 and must not be blocked"


def test_ps5_denies_chain_operators():
    proc = run_hook(GUARD, pretooluse("npm ci && npm test"), PWSH_ENV)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
    assert "$LASTEXITCODE" in body["permissionDecisionReason"]


# -------------------------------------------------------------- rewrite path


def test_full_rewrite_returns_updated_input():
    proc = run_hook(GUARD, pretooluse("export NODE_ENV=production; echo $PATH > /dev/null"), PWSH_ENV)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["permissionDecision"] == "allow"
    updated = body["updatedInput"]
    assert updated["command"] == '$env:NODE_ENV = "production"; echo $env:PATH > $null'
    # updatedInput REPLACES the whole input object, so unchanged fields must survive
    assert updated["description"] == "run something"
    assert updated["timeout"] == 120000
    assert updated["run_in_background"] is False


def test_block_mode_never_rewrites():
    env = dict(PWSH_ENV, CCPG_MODE="block")
    proc = run_hook(GUARD, pretooluse("echo $PATH"), env)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
    assert "updatedInput" not in body


def test_warn_mode_allows_but_explains():
    env = dict(PWSH_ENV, CCPG_MODE="warn")
    proc = run_hook(GUARD, pretooluse("shopt -s extglob"), env)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["permissionDecision"] == "allow"
    assert "shopt" in body["additionalContext"]
    assert "updatedInput" not in body


def test_output_is_a_single_json_object_on_one_line():
    """Claude Code parses stdout as JSON only when it starts with { and ends
    with }. Multi-line output that each parse as JSON is treated as plain text."""
    proc = run_hook(GUARD, pretooluse("echo $PATH"), PWSH_ENV)
    text = proc.stdout
    assert text.startswith("{") and text.rstrip().endswith("}")
    assert "\n" not in text.strip()


# -------------------------------------------------------------- SessionStart


def sessionstart(source: str = "startup") -> dict:
    return {
        "session_id": "abc123",
        "transcript_path": "/home/user/.claude/projects/x/transcript.jsonl",
        "cwd": "C:\\Users\\dev\\project",
        "hook_event_name": "SessionStart",
        "source": source,
        "model": "claude-opus-5",
    }


def test_session_note_shape():
    proc = run_hook(SESSION, sessionstart(), PWSH_ENV)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["hookEventName"] == "SessionStart"
    assert "permissionDecision" not in body, "SessionStart has no decision control"
    ctx = body["additionalContext"]
    assert "Shell environment" in ctx
    assert "PowerShell" in ctx


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_note_handles_every_source(source):
    proc = run_hook(SESSION, sessionstart(source), PWSH_ENV)
    body = out_json(proc)["hookSpecificOutput"]
    assert body["additionalContext"]
    if source in ("resume", "fork"):
        assert "refreshed on session %s" % source in body["additionalContext"]


def test_session_note_off_mode_is_silent():
    env = dict(PWSH_ENV, CCPG_MODE="off")
    proc = run_hook(SESSION, sessionstart(), env)
    assert proc.stdout.strip() == ""


def test_session_note_survives_empty_stdin():
    proc = subprocess.run([sys.executable, str(SESSION)], input="", capture_output=True, text=True)
    assert proc.returncode == 0
    json.loads(proc.stdout)
