# claude-code-pwsh-guard

A Claude Code hook pack that catches bash-only shell syntax before it reaches a Windows PowerShell session, and either rewrites it to the PowerShell equivalent or blocks it with the correct command in the error message.

## Status

Last reviewed: September 2026 · release v2026.09


```text
Claude                                                    pwsh-guard                          Result
──────────────────────────────────────────────────────────────────────────────────────────────────
Bash("shopt -s extglob;                       ─▶  PreToolUse: deny                   ✗ never runs
      rm -f !(keep.txt|keep2.txt)")               [ERROR] shopt / extglob is a bash builtin
                                                   PowerShell: Get-ChildItem -Exclude ... |
                                                               Remove-Item
                                                  [ERROR] extglob pattern !(...)

Bash("Get-ChildItem -Exclude                  ─▶  (silent)                            ✓ runs
      keep.txt,keep2.txt | Remove-Item")

Bash("export NODE_ENV=production;             ─▶  PreToolUse: allow + updatedInput    ✓ runs, fixed
      echo $PATH > /dev/null")                    $env:NODE_ENV = "production";
                                                  echo $env:PATH > $null
```

Without the guard, that first command produces `The term 'shopt' is not recognized`, Claude tries a
variation of the same bash syntax, and the loop repeats. That loop is
[issue #28670](https://github.com/anthropics/claude-code/issues/28670).

![hook events](https://img.shields.io/badge/hooks-PreToolUse%20%2B%20SessionStart-blue)
![rules](https://img.shields.io/badge/rules-19-blue)
![tests](https://img.shields.io/badge/tests-204%20passing-brightgreen)
![license](https://img.shields.io/badge/license-MIT-blue)

## Quick start

1. Clone this repo anywhere: `git clone https://github.com/<you>/claude-code-pwsh-guard`
2. `cd claude-code-pwsh-guard`
3. `powershell -ExecutionPolicy Bypass -File .\install.ps1`
4. Restart Claude Code.

That is it. The installer merges two hook entries into `%USERPROFILE%\.claude\settings.json`,
backing it up first. Requires Python 3.9+ on PATH (PyYAML is optional — a pre-generated JSON copy of
the rules ships with the repo). Remove it with `.\uninstall.ps1`.

To install into a single project instead of your user settings:

```powershell
.\install.ps1 -SettingsPath .\.claude\settings.json
```

## The problem

Claude Code on Windows can run its Bash tool against PowerShell. Claude, trained overwhelmingly on
POSIX shell, keeps emitting bash syntax at it: `shopt -s extglob`, `$PATH`, `/dev/null`, heredocs,
`&&` chains. Each one fails, and the failure message (`The term 'shopt' is not recognized`) does not
tell the model what the PowerShell equivalent is, so the next attempt is usually another bash
variant.

Two issues track this in `anthropics/claude-code`:

- [#28670](https://github.com/anthropics/claude-code/issues/28670) — "[BUG] Claude Code repeatedly
  uses Bash syntax (extglob) on Windows PowerShell and fails to adapt". **Closed as not planned.**
- [#35760](https://github.com/anthropics/claude-code/issues/35760) — "[FEATURE] Plugin to use
  PowerShell Preview (pwsh) as default shell on all platforms", a request to fix the same
  bash-versus-PowerShell mismatch at the shell level. **Closed as not planned.**

(Titles and states above were read from the GitHub API on 2026-09-01.)

Claude Code ships a hook API that is fully capable of solving it. This repo is that solution, built
outside the product. It is not affiliated with or endorsed by Anthropic.

## What it catches

19 rules, all defined as data in [`rules/pwsh-rules.yaml`](rules/pwsh-rules.yaml). Every rule carries
its own positive and negative examples, and the test suite asserts each one fires on its positives
and stays silent on its negatives.

| Rule                          | Severity | Example input                      | Action                                                     |
| ----------------------------- | -------- | ---------------------------------- | ---------------------------------------------------------- |
| `bash-shopt-extglob`          | error    | `shopt -s extglob`                 | block                                                      |
| `bash-extglob-pattern`        | error    | `rm -f !(keep.txt\|also-keep.txt)` | block                                                      |
| `bash-brace-expansion`        | error    | `mkdir -p src/{a,b,c}`             | block                                                      |
| `posix-dev-null`              | error    | `git status > /dev/null`           | **rewrite** → `> $null`                                    |
| `posix-redirect-both-streams` | error    | `npm run build > /dev/null 2>&1`   | block → `*> $null`                                         |
| `bash-export`                 | error    | `export NODE_ENV=production`       | **rewrite** → `$env:NODE_ENV = "production"`               |
| `bash-source`                 | error    | `source ./venv/bin/activate.sh`    | block → `. .\script.ps1`                                   |
| `bash-which`                  | warn     | `which node`                       | **rewrite** → `Get-Command node`                           |
| `bash-command-v`              | warn     | `command -v docker`                | block → `Get-Command docker -ErrorAction SilentlyContinue` |
| `posix-env-var-ref`           | error    | `echo $PATH`                       | **rewrite** → `echo $env:PATH`                             |
| `bash-line-continuation`      | error    | trailing `\` before a newline      | **rewrite** → backtick                                     |
| `bash-heredoc`                | error    | `cat <<'EOF'`                      | block → here-string `@'…'@`                                |
| `bash-ansi-c-quoting`         | error    | `printf $'a\nb'`                   | block: use a double-quoted string with backtick escapes |
| `bash-test-brackets`          | error    | `if [ -f package.json ]; then …`   | block → `if (Test-Path …) { … }`                           |
| `bash-chain-and-or-ps5`       | error    | `npm ci && npm test`               | block → `;` or `$LASTEXITCODE` check                       |
| `bash-xargs`                  | warn     | `… \| xargs rm`                    | block → `ForEach-Object`                                   |
| `unix-text-tools-missing`     | warn     | `… \| grep error`                  | block → `Select-String`                                    |
| `posix-absolute-path`         | warn     | `cp build.zip /tmp/build.zip`      | block → `$env:TEMP`                                        |
| `bash-single-quote-literal`   | info     | `echo 'path is $PATH'`             | note only                                                  |

Three rules are context-gated rather than always-on:

- `bash-chain-and-or-ps5` only fires when the detected PowerShell is older than 7, since `&&` and
  `||` are valid in PowerShell 7+.
- `bash-which`, `bash-xargs` and `unix-text-tools-missing` only fire when the tool is genuinely
  absent from `PATH`. A machine with Git Bash installed never sees them.
- `posix-env-var-ref` deliberately excludes `$HOME`, `$PWD` and `$PSHOME`, which really are
  PowerShell automatic variables.

## How it works

Two hook events, both verified against the current
[hooks reference](https://code.claude.com/docs/en/hooks):

**`PreToolUse`** (matcher `Bash`) — `hooks/pwsh_guard.py` reads the tool call from stdin, runs the
rule set against `tool_input.command`, and writes one JSON object to stdout:

- **It rewrites.** When every problem found has a deterministic, safe substitution, the hook returns
  `permissionDecision: "allow"` with `updatedInput` carrying the corrected command. `updatedInput`
  replaces the entire input object, so the hook copies the original `description`, `timeout` and
  `run_in_background` through unchanged. The rewrite is real: the corrected command is what actually
  executes.
- **It blocks.** When any problem has no safe rewrite — `shopt`, heredocs, `[ … ]` tests — the hook
  returns `permissionDecision: "deny"` and puts the explanation and the PowerShell equivalent in
  `permissionDecisionReason`, which is the field Claude sees on a denial. Claude reads the correct
  command and self-corrects on the next turn. If some parts were fixable, the partially corrected
  command is included in the same message.
- **It stays quiet** on anything clean, so the normal permission flow is untouched.

The hook always exits 0; the decision travels in the JSON, never in the exit code. Any internal
error is swallowed and the call is passed through — a guard that crashes must not stop your work.

**`SessionStart`** (matcher `startup`) — `hooks/session_env_note.py` returns
`hookSpecificOutput.additionalContext` with a short factual note: the OS, which shell Bash tool
calls will actually hit, the PowerShell version, and which POSIX tools are on `PATH`. SessionStart
has no decision control in the API; this is context only.

### Modes

Set `CCPG_MODE` in your environment:

| Value            | Behaviour                                                |
| ---------------- | -------------------------------------------------------- |
| `auto` (default) | Rewrite what is safely rewritable, block the rest        |
| `block`          | Never rewrite; always deny with guidance                 |
| `warn`           | Never block; allow and attach the explanation as context |
| `off`            | Disable both hooks entirely                              |

`CCPG_SHELL=pwsh\|bash\|auto` forces or disables shell detection, and `CCPG_PWSH_MAJOR=5\|7` pins the
assumed PowerShell version.

## Safe install

A hook installer that corrupts `settings.json` breaks Claude Code for that user, so `install.ps1`
holds to a strict contract, and [`tests/test_install_merge.py`](tests/test_install_merge.py)
exercises the real script against throwaway files for every case below:

1. **Parse before touching.** If the existing file is not valid JSON — or is a JSON array rather
   than an object — the installer copies it to a timestamped backup, prints `REFUSING TO WRITE`,
   exits non-zero, and leaves the original byte-for-byte unchanged.
2. **Back up first.** `settings.json.pwsh-guard-backup-<yyyyMMdd-HHmmss>` is written before any
   modification, with a numeric suffix if that name is taken.
3. **Merge, never replace.** Existing hooks on `PreToolUse` and `SessionStart` are preserved and the
   guard is appended after them. Every other key — `model`, `env`, `permissions`, `statusLine`,
   nested objects, other hook events — is passed through untouched.
4. **Idempotent.** Each guard handler carries a `pwshGuardMarker` field. Re-running replaces the
   previous guard entry in place instead of stacking duplicates.
5. **Atomic write.** The new content goes to a temp file and is then moved over the target, so an
   interrupted run cannot leave a half-written settings file. Output is UTF-8 without a BOM.
6. **Verify after.** The installer re-parses what it just wrote. If that fails it restores the
   backup and reports a bug.

`uninstall.ps1` reverses it under the same contract, removing only groups carrying the marker,
cleaning up event keys and the `hooks` object if they end up empty.

### Manual install

If you would rather not run the script, add this to `~/.claude/settings.json`, replacing
`C:\path\to\repo` with wherever you cloned it:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["-X", "utf8", "C:\\path\\to\\repo\\hooks\\pwsh_guard.py"],
            "timeout": 15
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": [
              "-X",
              "utf8",
              "C:\\path\\to\\repo\\hooks\\session_env_note.py"
            ],
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

If `hooks.PreToolUse` already exists, append the new group to the array rather than replacing it.

## Limitations

- **It is heuristic pattern matching, not a shell parser.** The rules run regexes over the command
  text with comments blanked out. They do not understand quoting or nesting, so a bash-looking
  string inside a quoted argument can trip a rule. The negative examples in the rule file exist to
  keep this in check, but false positives are possible and expected.
- **Bypass, per command:** append `# pwsh-guard: ignore` to any command and the guard passes it
  straight through. **Bypass, globally:** `CCPG_MODE=off`, or `CCPG_MODE=warn` to keep the
  explanations without the blocking.
- **Shell detection is inferred, not reported.** Claude Code does not tell a hook which shell will
  run the command. In `auto` mode the guard engages only on Windows and only when `bash` is not on
  `PATH`, on the reasoning that a box with Git Bash installed is probably routing Bash tool calls
  through it. If that is wrong for your setup, set `CCPG_SHELL` explicitly.
- **PowerShell version is inferred** from whether `pwsh` is on `PATH` (7+) or not (5.1). Pin it with
  `CCPG_PWSH_MAJOR` if you run something unusual.
- **A rewrite is a guess about intent.** Only substitutions marked `safe: true` in the rule file are
  ever applied automatically, and each is a mechanical one-to-one translation. `&&` has a fix
  recorded but is marked unsafe precisely because `;` does not preserve its failure semantics — that
  one is always blocked, never silently changed.
- **Not affiliated with or endorsed by Anthropic.**

## Contributing

Rules are data. To add one, append a block to `rules/pwsh-rules.yaml` with an `id`, `severity`,
`detect` regex, `why`, `pwsh` equivalent, and at least one positive and one negative example — then:

```bash
python tools/build_rules_json.py     # regenerate the no-PyYAML fallback
python -m pytest tests -q            # every example is asserted
```

The suite will fail if a positive example does not match, a negative example does match, a declared
fix rewrites nothing, a safe fix leaves its own rule still firing, or the YAML and JSON drift apart.
Pull requests that add a rule without examples will not pass.

Bug reports are most useful with the exact command, the rule id, and what PowerShell actually did.

---

Built by SkynetLabs (Waseem Nasir) — https://skynetjoe.com

## License

MIT. See [LICENSE](LICENSE).
