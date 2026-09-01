"""Rule engine for claude-code-pwsh-guard.

Pure logic, no I/O with Claude Code. The hook entry points import from here so
the rules can be unit-tested without simulating a hook invocation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_YAML = REPO_ROOT / "rules" / "pwsh-rules.yaml"
RULES_JSON = REPO_ROOT / "rules" / "pwsh-rules.json"

SEVERITY_ORDER = {"info": 0, "warn": 1, "error": 2}

# Inline escape hatch: any command containing this marker is passed straight
# through. Documented in the README under Limitations.
BYPASS_MARKER = "pwsh-guard: ignore"


# --------------------------------------------------------------------------
# Rule loading
# --------------------------------------------------------------------------


class RuleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fix:
    pattern: str
    replacement: str
    safe: bool = False


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    title: str
    detect: str
    why: str
    pwsh: str
    applies_if: dict = field(default_factory=dict)
    fix: Fix | None = None
    examples: dict = field(default_factory=dict)

    @property
    def detect_re(self) -> re.Pattern:
        return _compile(self.detect)

    @property
    def fix_re(self) -> re.Pattern | None:
        return _compile(self.fix.pattern) if self.fix else None


_COMPILE_CACHE: dict[str, re.Pattern] = {}


def _compile(pattern: str) -> re.Pattern:
    hit = _COMPILE_CACHE.get(pattern)
    if hit is None:
        hit = re.compile(pattern, re.MULTILINE)
        _COMPILE_CACHE[pattern] = hit
    return hit


def _rule_from_dict(raw: dict) -> Rule:
    missing = [k for k in ("id", "severity", "title", "detect", "why", "pwsh") if k not in raw]
    if missing:
        raise RuleError("rule %r missing keys: %s" % (raw.get("id", "<no id>"), missing))
    if raw["severity"] not in SEVERITY_ORDER:
        raise RuleError("rule %r has unknown severity %r" % (raw["id"], raw["severity"]))
    fix = None
    if raw.get("fix"):
        f = raw["fix"]
        fix = Fix(pattern=f["pattern"], replacement=f["replacement"], safe=bool(f.get("safe", False)))
    return Rule(
        id=raw["id"],
        severity=raw["severity"],
        title=raw["title"],
        detect=raw["detect"],
        why=" ".join(raw["why"].split()),
        pwsh=" ".join(raw["pwsh"].split()),
        applies_if=raw.get("applies_if") or {},
        fix=fix,
        examples=raw.get("examples") or {},
    )


def load_raw_rules(path: Path | None = None) -> dict:
    """Load the rule document.

    Prefers the YAML source when PyYAML is importable so that editing the YAML
    is enough during development. Falls back to the generated JSON, which is
    what ships for users who do not have PyYAML installed.
    """
    if path is not None:
        return _load_any(path)
    try:
        import yaml  # noqa: F401
    except ImportError:
        if not RULES_JSON.exists():
            raise RuleError(
                "PyYAML is not installed and %s is missing. Run tools/build_rules_json.py." % RULES_JSON
            )
        return _load_any(RULES_JSON)
    if RULES_YAML.exists():
        return _load_any(RULES_YAML)
    return _load_any(RULES_JSON)


def _load_any(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml

        return yaml.safe_load(text)
    return json.loads(text)


def load_rules(path: Path | None = None) -> list[Rule]:
    doc = load_raw_rules(path)
    rules = [_rule_from_dict(r) for r in doc.get("rules", [])]
    ids = [r.id for r in rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise RuleError("duplicate rule ids: %s" % sorted(dupes))
    return rules


# --------------------------------------------------------------------------
# Environment probe
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Env:
    """What the guard believes about the machine it is running on."""

    is_windows: bool
    pwsh_major: int          # 7 when PowerShell Core is on PATH, else 5
    tools_on_path: frozenset[str]
    shell_is_powershell: bool

    def has_tool(self, name: str) -> bool:
        return name in self.tools_on_path


PROBE_TOOLS = ("bash", "grep", "sed", "awk", "which", "xargs", "jq", "pwsh", "powershell")


def probe_env(overrides: dict[str, Any] | None = None) -> Env:
    """Detect the local shell environment.

    Environment variable overrides (all optional):
      CCPG_SHELL        pwsh | bash | auto   (default auto)
      CCPG_PWSH_MAJOR   integer, forces the assumed PowerShell major version
    """
    overrides = overrides or {}
    env = os.environ

    is_windows = bool(overrides.get("is_windows", os.name == "nt"))

    tools = overrides.get("tools_on_path")
    if tools is None:
        tools = frozenset(t for t in PROBE_TOOLS if shutil.which(t))
    else:
        tools = frozenset(tools)

    forced_major = overrides.get("pwsh_major", env.get("CCPG_PWSH_MAJOR"))
    if forced_major:
        pwsh_major = int(forced_major)
    else:
        # pwsh.exe on PATH means PowerShell 7+; otherwise the shell is the
        # Windows-bundled powershell.exe, which is 5.1.
        pwsh_major = 7 if "pwsh" in tools else 5

    shell_pref = str(overrides.get("shell", env.get("CCPG_SHELL", "auto"))).lower()
    if shell_pref == "pwsh":
        shell_is_powershell = True
    elif shell_pref == "bash":
        shell_is_powershell = False
    else:
        # Auto: the guard only engages on Windows, and only when a POSIX shell
        # is not available to run the command instead. If bash.exe is on PATH,
        # Claude Code's Bash tool very likely routes through it and bash syntax
        # is correct as written.
        shell_is_powershell = is_windows and "bash" not in tools

    return Env(
        is_windows=is_windows,
        pwsh_major=pwsh_major,
        tools_on_path=tools,
        shell_is_powershell=shell_is_powershell,
    )


def rule_applies(rule: Rule, env: Env) -> bool:
    cond = rule.applies_if or {}
    if "max_pwsh_major" in cond and env.pwsh_major > int(cond["max_pwsh_major"]):
        return False
    if "min_pwsh_major" in cond and env.pwsh_major < int(cond["min_pwsh_major"]):
        return False
    tool = cond.get("requires_missing_tool")
    if tool and env.has_tool(tool):
        return False
    return True


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    rule: Rule
    match: str
    start: int

    @property
    def id(self) -> str:
        return self.rule.id


_COMMENT_RE = re.compile(r"(?m)(?<!\S)#[^\n]*$")


def strip_comments(command: str) -> str:
    """Blank out shell comments so rules do not fire on prose.

    Replaces comment text with spaces rather than deleting it, so every
    character offset in the returned string still lines up with the original.
    """
    def blank(m: re.Match) -> str:
        return " " * len(m.group(0))

    return _COMMENT_RE.sub(blank, command)


def analyze(command: str, env: Env, rules: Iterable[Rule] | None = None) -> list[Finding]:
    if rules is None:
        rules = load_rules()
    haystack = strip_comments(command)
    findings: list[Finding] = []
    for rule in rules:
        if not rule_applies(rule, env):
            continue
        m = rule.detect_re.search(haystack)
        if m:
            findings.append(Finding(rule=rule, match=m.group(0), start=m.start()))
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f.rule.severity], f.start))
    return findings


def apply_fixes(command: str, findings: Iterable[Finding]) -> tuple[str, list[str]]:
    """Apply every safe fix. Returns (new_command, applied_rule_ids)."""
    out = command
    applied: list[str] = []
    for f in findings:
        fix = f.rule.fix
        if not fix or not fix.safe:
            continue
        new = _compile(fix.pattern).sub(fix.replacement, out)
        if new != out:
            out = new
            applied.append(f.rule.id)
    return out, applied


def unfixed(findings: Iterable[Finding], applied: Iterable[str]) -> list[Finding]:
    done = set(applied)
    return [f for f in findings if f.rule.id not in done]


def explain(findings: Iterable[Finding]) -> str:
    """Render findings as the guidance text Claude sees on a deny."""
    lines = ["This command uses bash syntax that will not run under PowerShell."]
    for f in findings:
        lines.append("")
        lines.append("[%s] %s (%s)" % (f.rule.severity.upper(), f.rule.title, f.rule.id))
        lines.append("  matched: %s" % f.match.replace("\n", "\\n").strip())
        lines.append("  why: %s" % f.rule.why)
        lines.append("  PowerShell: %s" % f.rule.pwsh)
    lines.append("")
    lines.append(
        "Rewrite the command in PowerShell syntax and try again. "
        "To run it as-is anyway, append the comment '# %s'." % BYPASS_MARKER
    )
    return "\n".join(lines)


def mode() -> str:
    """auto | block | warn | off  (default auto)."""
    m = os.environ.get("CCPG_MODE", "auto").strip().lower()
    return m if m in ("auto", "block", "warn", "off") else "auto"


def env_note(env: Env) -> str:
    """Factual one-paragraph environment note for the SessionStart hook."""
    shell = "PowerShell" if env.shell_is_powershell else "a POSIX shell (bash found on PATH)"
    ps_name = "PowerShell 7+ (pwsh)" if env.pwsh_major >= 7 else "Windows PowerShell 5.1 (powershell.exe)"
    posix_tools = sorted(t for t in ("grep", "sed", "awk", "which", "xargs", "jq") if t in env.tools_on_path)
    lines = [
        "Shell environment (detected by claude-code-pwsh-guard):",
        "- OS: %s" % ("Windows" if env.is_windows else sys.platform),
        "- Bash tool commands are expected to run under: %s" % shell,
        "- PowerShell available: %s" % ps_name,
        "- POSIX tools on PATH: %s" % (", ".join(posix_tools) if posix_tools else "none"),
    ]
    if env.shell_is_powershell:
        lines += [
            "",
            "Write shell commands in PowerShell syntax: $env:VAR not $VAR, $null not /dev/null,"
            " here-strings (@'...'@) not heredocs, backtick not backslash for line continuation.",
        ]
        if env.pwsh_major < 7:
            lines.append("This PowerShell does not support && or || chaining. Use ';' or an explicit"
                         " $LASTEXITCODE check.")
    return "\n".join(lines)
