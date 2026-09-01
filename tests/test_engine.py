"""Engine behaviour: environment probing, comment stripping, explanation text."""

import pytest

from engine import (
    Env,
    analyze,
    apply_fixes,
    env_note,
    explain,
    load_rules,
    mode,
    probe_env,
    strip_comments,
    unfixed,
)

RULES = load_rules()

BARE_WINDOWS = Env(
    is_windows=True,
    pwsh_major=5,
    tools_on_path=frozenset({"powershell"}),
    shell_is_powershell=True,
)


def fired(command, env=BARE_WINDOWS):
    return {f.rule.id for f in analyze(command, env, RULES)}


# --------------------------------------------------------------------- probe


def test_probe_auto_engages_on_windows_without_bash():
    env = probe_env({"is_windows": True, "tools_on_path": ["powershell"]})
    assert env.shell_is_powershell is True
    assert env.pwsh_major == 5


def test_probe_auto_stands_down_when_bash_is_on_path():
    env = probe_env({"is_windows": True, "tools_on_path": ["bash", "grep"]})
    assert env.shell_is_powershell is False


def test_probe_auto_stands_down_off_windows():
    env = probe_env({"is_windows": False, "tools_on_path": []})
    assert env.shell_is_powershell is False


def test_probe_shell_override_forces_engagement():
    env = probe_env({"is_windows": False, "tools_on_path": ["bash"], "shell": "pwsh"})
    assert env.shell_is_powershell is True


def test_probe_detects_pwsh7():
    env = probe_env({"is_windows": True, "tools_on_path": ["pwsh"]})
    assert env.pwsh_major == 7


def test_mode_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("CCPG_MODE", raising=False)
    assert mode() == "auto"


@pytest.mark.parametrize("value,expected", [("off", "off"), ("BLOCK", "block"), ("nonsense", "auto")])
def test_mode_env(monkeypatch, value, expected):
    monkeypatch.setenv("CCPG_MODE", value)
    assert mode() == expected


# ------------------------------------------------------------------ comments


def test_strip_comments_preserves_offsets():
    src = "echo hi # export FOO=bar\necho bye"
    out = strip_comments(src)
    assert len(out) == len(src)
    assert out.startswith("echo hi ")
    assert "export" not in out


def test_rule_does_not_fire_inside_a_comment():
    assert "bash-export" not in fired("Get-ChildItem   # export FOO=bar")


def test_hash_inside_a_word_is_not_a_comment():
    # "#" must be preceded by whitespace or start-of-line to open a comment
    assert "posix-dev-null" in fired("git log --format=%h#/dev/null")


# ------------------------------------------------------------------- analyze


def test_the_issue_28670_case():
    assert "bash-shopt-extglob" in fired("shopt -s extglob")


def test_multiple_rules_on_one_command():
    hits = fired("export FOO=1 && echo $PATH > /dev/null")
    assert {"bash-export", "bash-chain-and-or-ps5", "posix-env-var-ref", "posix-dev-null"} <= hits


def test_clean_powershell_command_is_silent():
    assert fired('Get-ChildItem -Recurse -Filter *.py | Select-String -Pattern "TODO"') == set()


def test_ordinary_git_command_is_silent():
    assert fired("git commit -m 'wip'") == set()


def test_findings_sorted_error_first():
    findings = analyze("which node; export A=1", BARE_WINDOWS, RULES)
    severities = [f.rule.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warn": 1, "info": 2}[s])


# ---------------------------------------------------------------------- fixes


def test_full_autofix_path():
    cmd = "export NODE_ENV=production; which node > /dev/null"
    findings = analyze(cmd, BARE_WINDOWS, RULES)
    fixed, applied = apply_fixes(cmd, findings)
    assert unfixed(findings, applied) == []
    assert fixed == '$env:NODE_ENV = "production"; Get-Command node > $null'


def test_partial_fix_leaves_remainder():
    cmd = "shopt -s extglob; echo $PATH"
    findings = analyze(cmd, BARE_WINDOWS, RULES)
    fixed, applied = apply_fixes(cmd, findings)
    assert applied == ["posix-env-var-ref"]
    assert "$env:PATH" in fixed
    assert [f.rule.id for f in unfixed(findings, applied)] == ["bash-shopt-extglob"]


def test_unsafe_fix_is_never_auto_applied():
    cmd = "npm ci && npm test"
    findings = analyze(cmd, BARE_WINDOWS, RULES)
    fixed, applied = apply_fixes(cmd, findings)
    assert applied == [], "&& has an unsafe fix and must not be auto-applied"
    assert fixed == cmd


def test_home_is_not_flagged_because_powershell_has_it():
    assert "posix-env-var-ref" not in fired("Get-ChildItem $HOME")
    assert "posix-env-var-ref" in fired("Get-ChildItem $USERPROFILE")


def test_line_continuation_fix():
    cmd = "docker run \\\n  --rm alpine"
    findings = analyze(cmd, BARE_WINDOWS, RULES)
    fixed, applied = apply_fixes(cmd, findings)
    assert "bash-line-continuation" in applied
    assert "`\n" in fixed and "\\\n" not in fixed


# -------------------------------------------------------------------- explain


def test_explain_names_the_rule_and_the_fix():
    findings = analyze("cat <<'EOF'\nhi\nEOF", BARE_WINDOWS, RULES)
    text = explain(findings)
    assert "bash-heredoc" in text
    assert "here-string" in text
    assert "pwsh-guard: ignore" in text


# ------------------------------------------------------------------ env note


def test_env_note_is_factual_for_ps5():
    note = env_note(BARE_WINDOWS)
    assert "Windows PowerShell 5.1" in note
    assert "does not support && or ||" in note
    assert "POSIX tools on PATH: none" in note


def test_env_note_for_bash_box_does_not_lecture():
    env = Env(
        is_windows=True,
        pwsh_major=7,
        tools_on_path=frozenset({"bash", "grep", "sed", "pwsh"}),
        shell_is_powershell=False,
    )
    note = env_note(env)
    assert "POSIX shell" in note
    assert "Write shell commands in PowerShell syntax" not in note
    assert "grep, sed" in note
