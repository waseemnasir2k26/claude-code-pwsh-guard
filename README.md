# claude-code-pwsh-guard

**A Claude Code hook pack that stops the agent firing bash-only syntax at a Windows PowerShell session: it rewrites the safe cases and blocks the rest with the right PowerShell command.**

![hooks](https://img.shields.io/badge/hooks-PreToolUse%20%2B%20SessionStart-blue) ![rules](https://img.shields.io/badge/rules-19-blue) ![license](https://img.shields.io/badge/license-MIT-blue)

On Windows, Claude keeps emitting `shopt -s extglob`, `$PATH`, `/dev/null`, heredocs and `&&` chains. Each fails with a message that does not name the PowerShell equivalent, so the next try is another bash variant and the loop repeats ([anthropics/claude-code#28670](https://github.com/anthropics/claude-code/issues/28670), closed as not planned). Hooks can fix this from outside the product.

## What it does

```text
Bash("export NODE_ENV=production; echo $PATH > /dev/null")
  -> allowed, rewritten to: $env:NODE_ENV = "production"; echo $env:PATH > $null

Bash("shopt -s extglob; rm -f !(keep.txt)")
  -> denied, with the PowerShell equivalent (Get-ChildItem -Exclude ... | Remove-Item)
```

- 19 rules defined as data in `rules/pwsh-rules.yaml` (JSON copy in `rules/pwsh-rules.json`), each with positive and negative examples checked by the test suite.
- Rewrites only substitutions marked safe (`/dev/null`, `export`, `$PATH`, `which`, line continuation). Blocks the rest with an explanation.
- A SessionStart note tells the model which shell it is talking to.
- Bypass one command with `# pwsh-guard: ignore`, or set `CCPG_MODE=off` or `CCPG_MODE=warn`.

## Quickstart

```powershell
git clone https://github.com/waseemnasir2k26/claude-code-pwsh-guard
cd claude-code-pwsh-guard
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Restart Claude Code. The installer backs up and merges two hook entries into `%USERPROFILE%\.claude\settings.json`. For one project only: `.\install.ps1 -SettingsPath .\.claude\settings.json`. Remove with `.\uninstall.ps1`. Needs Python 3.9+ on PATH; PyYAML is optional.

## For you if

You run Claude Code on Windows, your Bash tool lands in PowerShell, and you are tired of watching it retry the same bash command.

## Limits

- Pattern matching with regexes, not a shell parser. A bash-looking string inside a quoted argument can trigger a rule, so false positives are possible.
- Shell detection is inferred (Windows and no `bash` on PATH). If that is wrong for you, set `CCPG_SHELL`; pin the PowerShell major version with `CCPG_PWSH_MAJOR`.
- A rewrite is a guess about intent, which is why only safe ones are applied.
- Not affiliated with or endorsed by Anthropic.
- Last reviewed October 2026.

## Built by SkynetLabs

Built by [SkynetLabs](https://skynetjoe.com). Questions: info@skynetjoe.com

Need this wired into your business? SkynetLabs builds voice agents, n8n back offices and sites that convert → https://skynetjoe.com
