"""settings.json merge safety, exercised against the real install.ps1.

These tests run the actual PowerShell installer against throwaway settings
files. They are the reason this repo can claim the installer will not eat
somebody's configuration.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.ps1"
UNINSTALL = ROOT / "uninstall.ps1"

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell on PATH")


def run_ps(script: Path, settings: Path, extra=()):
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-SettingsPath",
            str(settings),
            "-Quiet",
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def install(settings: Path):
    return run_ps(INSTALL, settings)


def uninstall(settings: Path):
    return run_ps(UNINSTALL, settings)


def read(settings: Path) -> dict:
    return json.loads(settings.read_text(encoding="utf-8"))


def backups(settings: Path):
    return sorted(settings.parent.glob(settings.name + ".pwsh-guard-backup-*"))


def guard_groups(doc: dict, event: str):
    return [
        g
        for g in doc.get("hooks", {}).get(event, [])
        if any(h.get("pwshGuardMarker") == "claude-code-pwsh-guard" for h in g.get("hooks", []))
    ]


# ------------------------------------------------------------- fresh install


def test_install_into_missing_file(tmp_path):
    s = tmp_path / "settings.json"
    r = install(s)
    assert r.returncode == 0, r.stderr
    doc = read(s)
    assert len(guard_groups(doc, "PreToolUse")) == 1
    assert len(guard_groups(doc, "SessionStart")) == 1
    assert doc["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert doc["hooks"]["SessionStart"][0]["matcher"] == "startup"
    handler = doc["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["type"] == "command"
    assert handler["args"][-1].endswith("pwsh_guard.py")


def test_install_into_empty_file(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("", encoding="utf-8")
    r = install(s)
    assert r.returncode == 0, r.stderr
    assert guard_groups(read(s), "PreToolUse")


def test_install_writes_utf8_without_bom(tmp_path):
    s = tmp_path / "settings.json"
    install(s)
    assert not s.read_bytes().startswith(b"\xef\xbb\xbf")


# --------------------------------------------------- preserve existing config


def test_unrelated_settings_survive(tmp_path):
    s = tmp_path / "settings.json"
    original = {
        "model": "opus",
        "env": {"FOO": "bar", "NESTED": {"deep": {"deeper": [1, 2, 3]}}},
        "permissions": {"allow": ["Bash(git status)"], "deny": []},
        "statusLine": {"type": "command", "command": "my-statusline"},
    }
    s.write_text(json.dumps(original, indent=2), encoding="utf-8")
    r = install(s)
    assert r.returncode == 0, r.stderr
    doc = read(s)
    for key, value in original.items():
        assert doc[key] == value, "installer altered %r" % key


def test_existing_unrelated_hooks_survive(tmp_path):
    s = tmp_path / "settings.json"
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter.sh"}]},
                {"matcher": "Write", "hooks": [{"type": "command", "command": "fmt.sh"}]},
            ],
            "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "prettier"}]}],
            "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "brain.js"}]}],
        }
    }
    s.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    assert install(s).returncode == 0
    doc = read(s)

    pre = doc["hooks"]["PreToolUse"]
    assert len(pre) == 3, "existing PreToolUse hooks must be kept and the guard appended"
    assert pre[0]["hooks"][0]["command"] == "my-linter.sh"
    assert pre[1]["hooks"][0]["command"] == "fmt.sh"
    assert guard_groups(doc, "PreToolUse")

    assert doc["hooks"]["PostToolUse"] == existing["hooks"]["PostToolUse"], "untouched event changed"

    start = doc["hooks"]["SessionStart"]
    assert len(start) == 2
    assert start[0]["hooks"][0]["command"] == "brain.js"


def test_guard_is_appended_last_not_prepended(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(
        json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "a"}]}]}}
        ),
        encoding="utf-8",
    )
    install(s)
    pre = read(s)["hooks"]["PreToolUse"]
    assert pre[0]["hooks"][0]["command"] == "a"
    assert pre[-1]["hooks"][0]["pwshGuardMarker"] == "claude-code-pwsh-guard"


# ---------------------------------------------------------------- idempotence


def test_reinstall_does_not_duplicate(tmp_path):
    s = tmp_path / "settings.json"
    install(s)
    install(s)
    install(s)
    doc = read(s)
    assert len(guard_groups(doc, "PreToolUse")) == 1
    assert len(guard_groups(doc, "SessionStart")) == 1
    assert len(doc["hooks"]["PreToolUse"]) == 1


def test_reinstall_preserves_neighbours(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(
        json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "a"}]}]}}
        ),
        encoding="utf-8",
    )
    install(s)
    install(s)
    pre = read(s)["hooks"]["PreToolUse"]
    assert len(pre) == 2
    assert pre[0]["hooks"][0]["command"] == "a"


# -------------------------------------------------------------------- backups


def test_backup_is_written_before_change(tmp_path):
    s = tmp_path / "settings.json"
    original = {"model": "opus"}
    s.write_text(json.dumps(original), encoding="utf-8")
    install(s)
    b = backups(s)
    assert len(b) == 1
    assert json.loads(b[0].read_text(encoding="utf-8")) == original


def test_no_backup_when_there_was_no_file(tmp_path):
    s = tmp_path / "settings.json"
    install(s)
    assert backups(s) == []


# ------------------------------------------------------------------ malformed


def test_malformed_json_is_refused_and_backed_up(tmp_path):
    s = tmp_path / "settings.json"
    broken = '{"model": "opus", "hooks": {  <<< this is not json'
    s.write_text(broken, encoding="utf-8")

    r = install(s)
    assert r.returncode != 0, "installer must fail loudly on malformed JSON"
    assert "REFUSING TO WRITE" in (r.stderr + r.stdout)
    assert s.read_text(encoding="utf-8") == broken, "the broken file must be left untouched"

    b = backups(s)
    assert len(b) == 1
    assert b[0].read_text(encoding="utf-8") == broken


def test_json_array_is_refused(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("[1, 2, 3]", encoding="utf-8")
    r = install(s)
    assert r.returncode != 0
    assert "REFUSING TO WRITE" in (r.stderr + r.stdout)
    assert s.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_result_is_always_valid_json(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {"PreToolUse": []}, "env": {"A": "1"}}), encoding="utf-8")
    install(s)
    read(s)  # raises if invalid


# ------------------------------------------------------------------ uninstall


def test_uninstall_removes_only_the_guard(tmp_path):
    s = tmp_path / "settings.json"
    existing = {
        "model": "opus",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter.sh"}]}],
            "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "prettier"}]}],
        },
    }
    s.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    install(s)
    r = uninstall(s)
    assert r.returncode == 0, r.stderr
    doc = read(s)
    assert doc["model"] == "opus"
    assert doc["hooks"]["PreToolUse"] == existing["hooks"]["PreToolUse"]
    assert doc["hooks"]["PostToolUse"] == existing["hooks"]["PostToolUse"]
    assert "SessionStart" not in doc["hooks"], "empty event key should be cleaned up"


def test_uninstall_round_trip_restores_original(tmp_path):
    s = tmp_path / "settings.json"
    original = {"model": "opus", "env": {"A": "1"}}
    s.write_text(json.dumps(original), encoding="utf-8")
    install(s)
    r = uninstall(s)
    assert r.returncode == 0, r.stderr
    doc = read(s)
    assert doc == original, "install -> uninstall must be a no-op on the config"
    assert "hooks" not in doc, "an emptied hooks object should be removed entirely"


def test_uninstall_on_clean_file_is_a_noop(tmp_path):
    s = tmp_path / "settings.json"
    original = {"model": "opus"}
    s.write_text(json.dumps(original), encoding="utf-8")
    r = uninstall(s)
    assert r.returncode == 0
    assert read(s) == original


def test_uninstall_refuses_malformed_json(tmp_path):
    s = tmp_path / "settings.json"
    broken = "{oops"
    s.write_text(broken, encoding="utf-8")
    r = uninstall(s)
    assert r.returncode != 0
    assert s.read_text(encoding="utf-8") == broken
