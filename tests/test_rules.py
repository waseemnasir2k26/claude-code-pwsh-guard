"""Every rule must fire on its positives and stay silent on its negatives."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from engine import Env, apply_fixes, load_rules, rule_applies

ROOT = Path(__file__).resolve().parent.parent
RULES = load_rules()

# A worst-case Windows box: PowerShell 5.1, no Git Bash, no coreutils.
BARE_WINDOWS = Env(
    is_windows=True,
    pwsh_major=5,
    tools_on_path=frozenset({"powershell"}),
    shell_is_powershell=True,
)


def ids(rules):
    return [r.id for r in rules]


def test_rules_load():
    assert len(RULES) >= 15, "rule set shrank unexpectedly"
    assert len(set(ids(RULES))) == len(RULES)


@pytest.mark.parametrize("rule", RULES, ids=ids(RULES))
def test_rule_has_examples(rule):
    assert rule.examples.get("positive"), "%s has no positive examples" % rule.id
    assert rule.examples.get("negative"), "%s has no negative examples" % rule.id


@pytest.mark.parametrize(
    "rule,sample",
    [(r, s) for r in RULES for s in r.examples.get("positive", [])],
    ids=["%s::pos%d" % (r.id, i) for r in RULES for i, _ in enumerate(r.examples.get("positive", []))],
)
def test_positive_examples_match(rule, sample):
    assert rule.detect_re.search(sample), "%s did not match %r" % (rule.id, sample)


@pytest.mark.parametrize(
    "rule,sample",
    [(r, s) for r in RULES for s in r.examples.get("negative", [])],
    ids=["%s::neg%d" % (r.id, i) for r in RULES for i, _ in enumerate(r.examples.get("negative", []))],
)
def test_negative_examples_do_not_match(rule, sample):
    m = rule.detect_re.search(sample)
    assert not m, "%s false-positived on %r (matched %r)" % (rule.id, sample, m.group(0) if m else None)


@pytest.mark.parametrize("rule", [r for r in RULES if r.fix], ids=ids([r for r in RULES if r.fix]))
def test_fix_actually_changes_positive_examples(rule):
    """A declared fix must rewrite at least one positive example."""
    changed = [s for s in rule.examples["positive"] if rule.fix_re.sub(rule.fix.replacement, s) != s]
    assert changed, "%s declares a fix that rewrites nothing" % rule.id


@pytest.mark.parametrize(
    "rule", [r for r in RULES if r.fix and r.fix.safe], ids=ids([r for r in RULES if r.fix and r.fix.safe])
)
def test_safe_fix_output_is_clean(rule):
    """A safe fix must not leave the same rule still firing, and must not
    reintroduce the pattern it was meant to remove."""
    for sample in rule.examples["positive"]:
        fixed = rule.fix_re.sub(rule.fix.replacement, sample)
        assert fixed != sample
        assert not rule.detect_re.search(fixed), "%s: fix left %r still matching (%r)" % (
            rule.id,
            sample,
            fixed,
        )


@pytest.mark.parametrize("rule", RULES, ids=ids(RULES))
def test_regexes_compile(rule):
    re.compile(rule.detect)
    if rule.fix:
        re.compile(rule.fix.pattern)


def test_applies_if_gates():
    r = next(r for r in RULES if r.id == "bash-chain-and-or-ps5")
    assert rule_applies(r, BARE_WINDOWS), "&& rule should fire on PowerShell 5.1"
    ps7 = Env(is_windows=True, pwsh_major=7, tools_on_path=frozenset({"pwsh"}), shell_is_powershell=True)
    assert not rule_applies(r, ps7), "&& rule must not fire on PowerShell 7"


def test_requires_missing_tool_gate():
    r = next(r for r in RULES if r.id == "unix-text-tools-missing")
    assert rule_applies(r, BARE_WINDOWS)
    with_grep = Env(
        is_windows=True,
        pwsh_major=5,
        tools_on_path=frozenset({"grep", "sed", "awk"}),
        shell_is_powershell=True,
    )
    assert not rule_applies(r, with_grep), "grep rule must not fire when grep is on PATH"


def test_yaml_and_json_do_not_drift():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_rules_json.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_json_fallback_matches_yaml_rules():
    from engine import RULES_JSON, RULES_YAML

    yaml_rules = load_rules(RULES_YAML)
    json_rules = load_rules(RULES_JSON)
    assert [r.id for r in yaml_rules] == [r.id for r in json_rules]
    assert [r.detect for r in yaml_rules] == [r.detect for r in json_rules]


def test_generated_json_is_parseable_without_yaml():
    doc = json.loads((ROOT / "rules" / "pwsh-rules.json").read_text(encoding="utf-8"))
    assert doc["_generated"].startswith("GENERATED")
    assert len(doc["rules"]) == len(RULES)


def test_apply_fixes_is_ordered_and_idempotent():
    from engine import analyze

    cmd = "export NODE_ENV=production; echo $PATH > /dev/null"
    findings = analyze(cmd, BARE_WINDOWS, RULES)
    fixed, applied = apply_fixes(cmd, findings)
    assert "$env:NODE_ENV" in fixed
    assert "$env:PATH" in fixed
    assert "$null" in fixed
    assert "/dev/null" not in fixed
    # running the same fixes again changes nothing further
    again, _ = apply_fixes(fixed, analyze(fixed, BARE_WINDOWS, RULES))
    assert again == fixed
