#!/usr/bin/env python3
"""SessionStart hook: tell the agent what shell it is actually talking to.

Contract (verified against https://code.claude.com/docs/en/hooks):
  stdin   JSON with session_id, cwd, hook_event_name, source (startup | resume |
          clear | compact | fork) and optionally model
  stdout  {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                  "additionalContext": "..."}}
  SessionStart has no decision control - context only, no blocking.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import env_note, mode, probe_env  # noqa: E402

HOOK_EVENT = "SessionStart"


def build(event: dict) -> dict | None:
    if mode() == "off":
        return None
    env = probe_env()
    note = env_note(env)
    source = event.get("source")
    if source in ("resume", "fork"):
        note = note + "\n(refreshed on session %s)" % source
    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT,
            "additionalContext": note,
        }
    }


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        event = {}

    try:
        result = build(event)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("pwsh-guard session note error: %r\n" % (exc,))
        sys.exit(0)

    if result is not None:
        sys.stdout.write(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
