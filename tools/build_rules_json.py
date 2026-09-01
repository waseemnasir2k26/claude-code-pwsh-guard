#!/usr/bin/env python3
"""Generate rules/pwsh-rules.json from rules/pwsh-rules.yaml.

The JSON copy is what runs on machines without PyYAML. Run this after every
edit to the YAML; tests/test_rules.py fails the build if they drift.

    python tools/build_rules_json.py
    python tools/build_rules_json.py --check   # exit 1 if regeneration needed
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rules" / "pwsh-rules.yaml"
DST = ROOT / "rules" / "pwsh-rules.json"

HEADER_NOTE = "GENERATED FROM rules/pwsh-rules.yaml BY tools/build_rules_json.py - DO NOT EDIT"


def render() -> str:
    doc = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    doc = {"_generated": HEADER_NOTE, **doc}
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = DST.read_text(encoding="utf-8") if DST.exists() else ""
        if current != text:
            print("rules/pwsh-rules.json is stale - run python tools/build_rules_json.py")
            return 1
        print("rules/pwsh-rules.json is up to date")
        return 0
    DST.write_text(text, encoding="utf-8")
    print("wrote %s" % DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
