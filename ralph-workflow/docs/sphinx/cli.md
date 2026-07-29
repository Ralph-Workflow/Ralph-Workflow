# CLI Reference

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) if you want the same flow with more context.

Ralph Workflow is invoked as `ralph` (or `python -m ralph`). Running `ralph` with no flags starts the normal workflow.

## Discovery and diagnostics

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--list-agents` | | `False` | List configured agents and their status (includes the 8 built-ins: `claude`, `claude-headless`, `codex`, `opencode`, `nanocoder`, `agy`, `pi`, `cursor`) |
| `--list-providers` | | `False` | List available AI providers (OpenCode API) |
| `--diagnose` | `-d` | `False` | Run pre-flight diagnostics and print a status table |
| `--check-config` | `-C` | `False` | Load and validate configuration, then exit |
| `--check-mcp` | | `False` | Validate custom MCP server definitions and AGY + Cursor transport compatibility, then exit. Set `RALPH_AGY_BINARY` to point at a non-PATH `agy` binary; set `RALPH_CURSOR_BINARY` to point at a non-PATH `agent` binary. |
| `--check-policy` | | `False` | Validate the active pipeline policy and print a summary |
| `--explain-policy` | | `False` | Print a human-readable explanation of the active policy and exit |
| `--inspect-checkpoint` | | `False` | Print the current checkpoint contents |

### `--check-policy` example

```bash
ralph --check-policy
```

This validates the active pipeline policy and prints a summary of the authored block model, compiled phases, drains, artifact contracts, and routing limits Ralph Workflow will use.

Use `--explain-policy-dir` to point at a custom policy directory:

```bash
ralph --check-policy --explain-policy-dir /path/to/policy/dir
```

See [Policy Explanation](configuration.md#inspecting-the-active-policy) for the deeper inspection view.

## Setup

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--init [label]` | | `None` | Create `PROMPT.md` and user-global configuration, detect agent CLIs already on `PATH`, and install bundled skills. Labels: `feature-spec`, `guardrail` (or `bug-fix`), `refactor`, `test-coverage`, `docs`. It creates `.gitignore` only in a git repo. Use `--init-local-config` only when this repo needs project-local overrides. |
| `--force-init-skills` | | `False` | Re-run baseline skill installation (user-global + project-scope) and exit. Pairs with `--init` for an explicit re-init; standalone forces the recheck path on a normal `ralph` run. |
| `--init-local-config` | | `False` | Create `.agent/` config files as explicit project-local copies of the main Ralph Workflow config set |
| `--regenerate-config` | | `False` | Rewrite config files from bundled defaults and keep backups as `<name>.bak` |

## What `--init` does on first run

1. **Scaffolds `PROMPT.md`** at the project root using the starter template (the `<!-- ralph:starter-prompt: ... -->` sentinel marks it for validation).
2. **Creates the user-global set** under `~/.config/` (`ralph-workflow.toml` and policy files). It does not create project-local `.agent/` configuration unless you later run `ralph --init-local-config`.
3. **Installs skills + symlinks siblings** by materializing the bundled skill bundle at `~/.claude/skills/` and symlinking it into the documented supported-agents sibling roots (Codex, OpenCode, AGY).
4. **Wires Pi as a transport without a skill-fan-out target** — pi.dev is wired as a transport (so the `pi` BuiltinAgentSpec, `pi/<model>` resolver, and `PiCommandBuilder` are all available end-to-end) but pi.dev has no documented skill-discovery system per <https://pi.dev/docs/latest/usage>, so no Pi user-global install target is created and no `.pi/skills/` directory is written. Skills loaded on the pi side use the per-invocation `--skill <path>` flag.
5. **Seeds `.gitignore` only in a git repo** with common local-artifact patterns; it never creates one in a non-git directory. Re-runs add any new patterns that have been added to the default set since the last init.

## Quick mode

Run one developer iteration with an inline prompt:

```bash
ralph -Q "do a quick change"
```

`-Q` / `--quick` forces `developer_iters=1` and lets you pass an inline prompt instead of using `PROMPT.md`. Ralph Workflow writes that inline prompt to `.agent/PRODUCT_CRITERIA.md` for the run.

```bash
ralph -Q "add a /healthz endpoint"
ralph -Q --prompt "add a /healthz endpoint"
ralph -Q -P "add a /healthz endpoint"
```

`--prompt` also accepts the short alias `-P`.

## Prompt helper

The `--prompt-helper` flag launches a dedicated interactive prompt-refinement flow. Unlike the normal pipeline, this mode is simpler and does not use multi-stage workflows, drain configuration, or fallback agents. Instead, it runs a single PM-style agent that asks you what you want to build and helps you refine the idea into a structured `PROMPT.md`.

```bash
ralph --prompt-helper
```

The helper asks follow-up questions about users, goals, constraints, success criteria, product behavior, and UX/UI expectations. It periodically shows you a polished draft and asks for review. When you approve, it writes a structured `PROMPT.md` to the workspace root.

This is a simpler alternative to writing `PROMPT.md` by hand, not the standard pipeline. The resulting `PROMPT.md` can be used directly with the next `ralph` run.

The `ralph-prompt` executable is an alternate entrypoint for the same experience. Both `ralph --prompt-helper` and `ralph-prompt` launch identical interactive sessions:

```bash
ralph-prompt
```

`ralph-prompt` ships with Ralph Workflow and is installed automatically by `pip install ralph-workflow`. No separate install is needed.

`ralph-mcp` is the standalone MCP server entrypoint (declared in `pyproject.toml` as `ralph.mcp.server.runtime:main`). It starts Ralph Workflow's local MCP server outside of a full pipeline run, which is useful for debugging tool calls or connecting an agent manually:

```bash
ralph-mcp --drain development --workspace .
```

See [MCP Architecture](mcp-architecture.md) for the server internals.

## Thorough mode

Use the long preset when the default two iterations are not enough:

```bash
ralph -L
```

`-L` / `--long` forces `developer_iters=5`.

Use the thorough preset when you want a longer unattended run budget:

```bash
ralph -T
```

`-T` / `--thorough` forces `developer_iters=10`.

The three depth presets are mutually exclusive — `-Q`, `-L`, and `-T`
cannot be combined with one another. Each one overrides an explicit `-D`.

## Pipeline tuning

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--counter NAME=VALUE` | | | Override a named budget or loop counter declared in `pipeline.toml` |
| `--developer-iters N` | `-D` | `2` | Maximum developer iterations per run |
| `--quick` | `-Q` | `False` | Quick mode: one developer iteration with optional inline prompt |
| `--long` | `-L` | `False` | Long mode: five developer iterations |
| `--thorough` | `-T` | `False` | Thorough mode: ten developer iterations |
| `--developer-agent <name>` | `-a` | (from config) | Override the developer agent by name |
| `--developer-model <flag>` | | (from config) | Forward a model flag to the developer agent binary |

## Execution control

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--quiet` | `-q` | `False` | Suppress all output except errors |
| `--debug` | | `False` | Enable trace-level debug output |
| `--verbosity <level>` | `-v` | `verbose` | Set output verbosity: `quiet`, `normal`, `verbose`, `full`, `debug` |
| `--resume` | `-r` | `False` | Resume from the saved checkpoint if one exists |
| `--no-resume` | | `False` | Ignore the checkpoint and restart from the beginning |
| `--dry-run` | | `False` | Run the pipeline structure without invoking agents |
| `--unsafe-mode` | | `False` | Merge Ralph Workflow MCP config into the agent's existing MCP config instead of overwriting it |

> **Note:** Verbosity defaults to `verbose` so the run looks visibly alive by default. Use `--quiet` in CI when you only want errors.

## Commit-message helpers

These flags support Ralph Workflow's commit-message generation flow and the `ralph --generate-commit` command that agents may be instructed to call.

Commits created through this generated-commit path keep the active git author identity unless you override it, and Ralph Workflow appends a `Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>` trailer so automated commits stay attributable.

| Flag | Default | Description |
|------|---------|-------------|
| `--generate-commit-msg` | `False` | Generate a commit message from the current repo changes |
| `--generate-commit` | `False` | Generate and apply the commit message in one step |
| `--show-commit-msg` | `False` | Print the most recently generated commit message |

## Git identity

| Flag | Default | Description |
|------|---------|-------------|
| `--git-user-name <name>` | (from git config) | Override git `user.name` for commits made by Ralph Workflow |
| `--git-user-email <email>` | (from git config) | Override git `user.email` for commits made by Ralph Workflow |

## Miscellaneous

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--config <path>` | `-c` | (auto-discovered) | Path to a specific config file |
| `--version` | `-V` | `False` | Print the Ralph Workflow version and exit |

## Sub-commands

### `ralph cleanup`

Remove per-worker namespaces under `.agent/workers/` in same-workspace parallel mode. Each parallel worker normally writes to `.agent/workers/<unit_id>/` and cleans up automatically; after a hard-kill (or any other interruption that skips the normal cleanup), the directories can be left behind. `cleanup` enumerates every subdirectory directly under `.agent/workers/` and (after confirmation) removes them. There is no separate liveness or staleness check — anything listed by `iterdir()` in that directory is treated as a candidate for removal, so use `--dry-run` first when you are not sure what is there.

`cleanup` only touches `.agent/workers/`. It does not remove `.agent/` config files, `PROMPT.md`, checkpoints, MCP sockets, or other temporary artifacts.

```bash
ralph cleanup
ralph cleanup --dry-run     # list namespaces without removing them
ralph cleanup --force       # remove without prompting for confirmation
```

### `ralph contribute`

Open the canonical Ralph Workflow repository in your default browser so you can review contribution options. GitHub is the primary repo (default); the Codeberg mirror remains available via `--source codeberg`. No git repository, configuration, or authentication is required. Pull requests on either forge must check the CLA box in the PR template.

```bash
ralph contribute                         # open GitHub (default)
ralph contribute --source codeberg       # open the Codeberg mirror
ralph contribute --source github         # explicit form of the default
```

### `ralph star`

Open the GitHub repository in your default browser so you can star Ralph Workflow. Use `--no-browser` to print the link without launching a browser.

```bash
ralph star                # open GitHub in your browser
ralph star --no-browser   # print the link instead
```

### `ralph smoke-interactive-claude`

Run the manual PTY/TUI smoke test for interactive Claude using `claude/haiku`. This is an ad-hoc manual verification command: it writes a smoke prompt under `tmp/interactive-claude-smoke/`, asks Claude to create a JavaScript todo list, and prints a parity report of session capture, tool activity, completion signal, and parser events. It consumes live agent tokens and is **not** part of `make verify`; keep it for diagnosing real interactive-Claude regressions only.

```bash
python -m ralph smoke-interactive-claude
```

All interactive smoke commands accept `--subagents` for diagnosing native
subagent behavior. The scenario requires exactly one observed subagent dispatch,
its correlated tool result, meaningful main-agent activity after that result, and the normal
artifact and completion signals. A missing signal makes the command exit
non-zero with a specific break in the parity report.

```bash
python -m ralph smoke-interactive-claude --subagents
python -m ralph smoke-interactive-cursor --subagents
```

Use `--subagent-prompt-file PATH` with `--subagents` to replace the default
read-only child task while retaining the harness-owned ordering, artifact, and
completion requirements. The file must be non-empty UTF-8 text inside the
current workspace. This is useful
for reproducing child silence, multi-tool work, delayed results, or other
parser, activity-router, and watchdog edge cases without editing Ralph Workflow.

```bash
python -m ralph smoke-interactive-claude \
  --subagents \
  --subagent-prompt-file tmp/subagent-edge-case.txt
```

These scenarios consume live agent tokens or quota and remain manual
diagnostics outside `make verify`. A transport without native subagent support
fails the subagent check; the harness does not silently downgrade to the basic
scenario.

### `ralph smoke-interactive-agy`

Run this manual, paid-usage diagnostic one at a time for Google Anti Gravity
(AGY). It is not part of `make verify`. The default
`agy/gemini-3.6-flash-low` is a conservative low-tier choice by name; AGY
price ordering is unverified. Each invocation is one `agy --print` run for one
smoke prompt, one at a time, with AGY's observed default 5-minute print timeout;
a billed amount requires the unrun pricing probe.

```bash
python -m ralph smoke-interactive-agy
```

On v1.1.8, manual probes accepted the default model ID, a no-model
`--effort high` invocation, and stream-json output. The CLI rejected every
tested combination of `--effort` with an explicit `--model`, so Ralph Workflow model
aliases use published IDs without an effort suffix. The latest live smoke
created its requested file and showed parser/tool activity without a permission
prompt. It wrote a valid fallback `smoke_test_result`; Ralph Workflow promoted
it, recorded the receipt, and wrote host-owned durable completion evidence after
AGY missed the MCP completion call. Continuation remains disabled: `--continue`
and `--conversation` accepted a prompt but did not expose proof that they
resumed the intended conversation. See `tmp/agy-source-of-truth.txt` for
verbatim observations before manually running a paid probe.

Set `RALPH_AGY_BINARY` to use a custom AGY executable or the deterministic
mock at `tests/_support/mock_agy.sh` for CI. The mock tests Ralph Workflow's harness;
it is not live-AGY evidence.

### `ralph smoke-interactive-nanocoder`

Run the manual PTY smoke test for Nanocoder. Use the same alias that the pipeline will use; testing bare `nanocoder` does not prove a configured `nanocoder/<provider>/<model>` chain is valid.

```bash
python -m ralph smoke-interactive-nanocoder
python -m ralph smoke-interactive-nanocoder --agent 'nanocoder/MiniMax Coding/MiniMax-M3'
```

Provider/model startup errors printed by Nanocoder are terminal invocation failures. The smoke report should show the exact provider/model error instead of waiting for an idle timeout.

The smoke also exercises Nanocoder's prompt-submission contract: Ralph Workflow
must keep Nanocoder on the PTY-backed Ink runtime, not Nanocoder's JSON/plain
automation path, because that path has a hidden long-run action limit. The
smoke must also catch the opposite failure mode: a run that only prints the
welcome banner and leaves the task as pasted input is a Ralph Workflow
integration failure, not successful agent startup.

### `ralph smoke-interactive-cursor`

Run the manual end-to-end smoke test for the Cursor Agent CLI. This drives
the live `agent` binary through the documented headless `--print
--output-format stream-json` contract, asks it to create
`tmp/interactive-cursor-smoke/todo-list.js`, and reports a parity table
with file creation, session capture, parser events, tool activity, and
artifact submission. The default alias is `cursor/auto`; override it
with `--agent cursor/<model>`.

```bash
python -m ralph smoke-interactive-cursor                                  # default alias
python -m ralph smoke-interactive-cursor --agent 'cursor/gpt-5.3-codex-high'   # explicit override
```

This command is **not** part of `make verify` (per the cursor non-goal of
no live-token-consuming smoke tests in verify). The harness only runs
when an operator explicitly invokes it.

Set `RALPH_CURSOR_BINARY` to use a custom `agent` executable (a real
wrapper, alternate live binary, or an operator-wired test stub). There
is no bundled mock for Cursor (unlike AGY); non-executable paths are
ignored with a WARNING.

## Related pages

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough
- [Configuration](configuration.md) — config files, flags, and precedence
- [Concepts](concepts.md) — the key workflow terms
- [Troubleshooting](troubleshooting.md) — common error messages and fixes
