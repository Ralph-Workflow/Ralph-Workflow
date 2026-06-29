# Run diagnostics before a workflow

This page is the **how-to guide** for the pre-flight diagnostic workflow.
It covers every command you can run *before* a real `ralph` run to verify
your environment is ready, and what each one proves about your setup.

## Goal

A failed run that you could have prevented by running one command is a wasted
run. Diagnostics exist to catch the preventable failures — missing agents,
broken MCP transport, misloaded policy, an unrecoverable capability bundle —
*before* you invest time in a real workflow.

## Prerequisites

- Ralph Workflow installed (`pipx install ralph-workflow`)
- A project directory with `PROMPT.md` written or the starter template in
  place
- At least one supported agent CLI installed on `PATH` (see
  [Agent CLI lifecycle](agents.md))

## When to run this

- **Before your first run** on a new machine
- **After changing** `pipeline.toml`, `artifacts.toml`, `mcp.toml`, or
  `ralph-workflow.toml`
- **After upgrading** Ralph Workflow or any agent CLI
- **When debugging** a run that failed earlier than expected
- **After pulling** changes that touched policy or agent configuration

## The full pre-flight: `ralph --diagnose`

The single command that runs every check is:

```bash
ralph --diagnose
```

This runs seven checks in order. Each check writes a status panel to the
terminal and updates the overall verdict.

| # | Check                              | What it proves                                                                                  |
| - | ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| 1 | Git repository                     | You're in a git repo and the working tree state is sane for a workflow                          |
| 2 | Configuration                      | `ralph-workflow.toml`, `pipeline.toml`, `artifacts.toml`, `mcp.toml` load and validate          |
| 3 | Agent availability                 | Each configured agent CLI is on `PATH`, version-detected, and executable                        |
| 4 | MCP servers                        | Configured MCP upstream servers are reachable and the chosen transport is compatible            |
| 5 | Workspace files                    | `.agent/` support files exist and `PROMPT.md` is present (or the starter sentinel is detected) |
| 6 | Capability state                   | The shipped baseline capability bundle is loaded; missing / degraded capabilities are surfaced  |
| 7 | Pre-flight policy validation       | The full policy bundle passes validation: agent chains, recovery, scope, artifact contracts     |

**Expected output (success):** a green "All checks passed" panel and exit
code `0`. **Failure mode:** a red panel per failing check and a non-zero exit
code.

## Targeted pre-flight flags

For narrower checks, use one of these instead of `--diagnose`. They exit
faster and produce a smaller, focused report.

### `ralph --check-config`

Loads and validates `ralph-workflow.toml`, `pipeline.toml`, `artifacts.toml`,
and `mcp.toml`. Exits non-zero if any file fails to parse, fails the loader
precedence rules, or fails schema validation.

Use this when:

- You just edited a config file and want to know it parses
- You're upgrading and want to check for deprecated keys
- A run failed early with a config-shaped error

### `ralph --check-mcp`

Validates only the MCP server configuration: probes each upstream's
transport, checks reachability, and confirms the chosen transport (stdio /
HTTP / SSE) is compatible with the configured agent. Useful when you've
changed `mcp.toml` and want to isolate MCP failures from agent failures.

### `ralph --check-policy`

Loads the bundled policy bundle (or your override) and validates it:

- Phase routing and drain graph
- Agent chain satisfiability
- Recovery policy structure
- Artifact requirements contract

Use this when:

- You've overridden `pipeline.toml` or `recovery.toml`
- A run failed in a way that looks policy-shaped

### `ralph --dry-run`

Runs the **pipeline skeleton** without invoking any agent CLI: phase routing
is exercised, capability probes happen, and a synthetic workflow report is
emitted, but no agent commands are spawned. The fastest way to verify
routing, prompts, and recovery decisions before paying for a real run.

Use this when:

- You want to verify phase routing without spending agent credits
- You're debugging "the wrong agent ran the wrong phase"
- You want to see what the run *would* do

### `ralph --list-agents`

Lists every configured agent (built-in + project-local + user-global) and
each one's availability status: on `PATH`, version detected, headless mode
supported. Useful when configuring a new agent or confirming a `PATH` fix.

### `ralph --list-providers`

Lists the OpenCode provider configurations visible to the run. Useful when
chaining providers or debugging OpenCode-specific transport issues.

### `ralph --inspect-checkpoint`

Prints the most recent checkpoint JSON: phase, drain, artifact path, agent
name, model, prompts, and verdict. Use this after a failed run to understand
what state was reached without re-running the workflow.

## Verification signal

After running `ralph --diagnose`, you should see:

```text
Ralph Workflow Diagnostics
─────────────────────────
✓ Git repository
✓ Configuration
✓ Agents (claude, opencode)
✓ MCP servers (3 upstreams reachable)
✓ Workspace files
✓ Capability state (12/12 healthy)
✓ Pre-flight policy validation

All checks passed. Ready for `ralph`.
```

If any check is red, fix it before running a real workflow. The diagnostic
report tells you exactly which check failed and what it expected.

## Common failure modes and what they mean

| Symptom                                          | Likely cause                                  | Fix                                                                |
| ------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------ |
| "Agent `codex` not found on PATH"               | The agent CLI is not installed or not on PATH | Install the CLI; ensure `which codex` works in the same shell      |
| "MCP upstream `fetch` unreachable"              | Network/auth or wrong URL                     | Verify the URL, check `MCP_AUTH_TOKEN` if set, retry `ralph --check-mcp` |
| "Capability `web.search` degraded"              | Web search provider missing or out of quota   | Set a provider key in `ralph-workflow.toml` or accept the degraded state |
| "Pre-flight policy validation: chain `dev → review` unsatisfiable" | An agent name in the chain is unknown | Run `ralph --list-agents`, fix the chain or install the missing CLI |
| "PROMPT.md is the starter template"             | You haven't replaced the starter text         | Edit `PROMPT.md` and remove the `<!-- ralph:starter-prompt ... -->` marker |

## Related pages

- [Agent CLI lifecycle](agents.md) — selection, detection, and invocation of
  every supported agent
- [CLI reference](cli.md) — every CLI flag including the diagnostic flags
- [Troubleshooting](troubleshooting.md) — when a real run has already failed
- [Configuration](configuration.md) — the config files diagnostics validates