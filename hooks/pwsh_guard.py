#!/usr/bin/env python3
"""PreToolUse hook: stop bash-only syntax reaching a Windows PowerShell session.

Contract (verified against https://code.claude.com/docs/en/hooks):
  stdin   JSON with session_id, cwd, hook_event_name, tool_name, tool_input, ...
  stdout  JSON object with hookSpecificOutput
  exit 0  always - the decision travels in the JSON, never in the exit code

Decisions emitted:
  allow + updatedInput   every problem had a safe deterministic rewrite
  deny  + reason         at least one problem has no safe rewrite
  (silence)              nothing to say; normal permission flow applies
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import (  # noqa: E402
    BYPASS_MARKER,
    analyze,
    apply_fixes,
    explain,
    load_rules,
    mode,
    probe_env,
    unfixed,
)

HOOK_EVENT = "PreToolUse"


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def passthrough() -> None:
    """Say nothing. Claude Code applies the normal permission flow."""
    sys.exit(0)


def decide(event: dict) -> dict | None:
    if event.get("tool_name") != "Bash":
        return None

    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    if BYPASS_MARKER in command:
        return None

    run_mode = mode()
    if run_mode == "off":
        return None

    env = probe_env()
    if not env.shell_is_powershell:
        return None

    findings = analyze(command, env, load_rules())
    if not findings:
        return None

    if run_mode == "warn":
        return {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "permissionDecision": "allow",
                "permissionDecisionReason": "pwsh-guard: %d issue(s), warn mode" % len(findings),
                "additionalContext": explain(findings),
            }
        }

    fixed_command, applied = (command, [])
    if run_mode == "auto":
        fixed_command, applied = apply_fixes(command, findings)

    remaining = unfixed(findings, applied)

    if not remaining and applied:
        new_input = dict(event.get("tool_input") or {})
        new_input["command"] = fixed_command
        return {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "permissionDecision": "allow",
                "permissionDecisionReason": "pwsh-guard rewrote bash syntax to PowerShell (%s)"
                % ", ".join(applied),
                "updatedInput": new_input,
                "additionalContext": (
                    "claude-code-pwsh-guard rewrote this command for PowerShell "
                    "(rules: %s). Use PowerShell syntax directly next time." % ", ".join(applied)
                ),
            }
        }

    reason = explain(remaining)
    if applied:
        reason += "\n\nThese parts were already rewritten for you: %s\nPartially corrected command:\n%s" % (
            ", ".join(applied),
            fixed_command,
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        # A guard that cannot read its input must never block work.
        passthrough()
        return

    try:
        result = decide(event)
    except Exception as exc:  # noqa: BLE001 - never break the user's session
        sys.stderr.write("pwsh-guard internal error: %r\n" % (exc,))
        passthrough()
        return

    if result is not None:
        emit(result)
    sys.exit(0)


if __name__ == "__main__":
    main()
