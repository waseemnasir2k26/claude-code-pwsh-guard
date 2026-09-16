# Changelog

All notable changes to this project are documented in this file.

## [2026.09] - 2026-09-16

- Maintenance review of `claude-code-pwsh-guard` — a Claude Code hook pack that catches bash-only shell syntax before it reaches a Windows PowerShell session, rewriting it where the substitution is safe and blocking it with the PowerShell equivalent where it is not.
- Status: Python hooks (`hooks/pwsh_guard.py` on `PreToolUse`, `hooks/session_env_note.py` on `SessionStart`) driven by 19 data-defined rules in `rules/pwsh-rules.yaml` (with a pre-built `pwsh-rules.json` fallback for machines without PyYAML), plus `install.ps1`/`uninstall.ps1` that merge into `~/.claude/settings.json` under a backup-verify-atomic-write contract, a pytest suite under `tests/`, and `tools/build_rules_json.py`. Modes are controlled by `CCPG_MODE` (auto/block/warn/off), with `CCPG_SHELL` and `CCPG_PWSH_MAJOR` overrides. Requires Python 3.9+ on PATH. MIT licensed; not affiliated with Anthropic.
- Reviewed September 2026: documentation refreshed, CHANGELOG added and the project versioned as v2026.09.
- Known gaps, as the README's own Limitations section states: the rules are heuristic regex matching rather than shell parsing, so false positives are possible; shell detection and PowerShell version are inferred, not reported by Claude Code; and `&&` chains are always blocked rather than rewritten because `;` does not preserve failure semantics. There is also no CI workflow in the repo, so the test suite runs only when invoked manually, and no version field exists in any manifest (no package.json or VERSION file) to bump.
- No rules, hook code or installer logic were changed in this release — documentation only.
