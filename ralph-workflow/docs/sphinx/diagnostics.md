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

Unlike runtime execution—which safely falls back to built-in defaults with
deduplicated warnings for optional configuration files—`ralph --check-config`
strictly reports an aggregated summary of every broken, unreadable, or schema-invalid
configuration file across all scopes without falling back.

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

## Retrying retained integrations with `ralph --retry`

When auto-integration rebase or merge conflict resolution is interrupted, fails due to agent errors or configuration issues, or is retained for human intervention, Ralph Workflow persists an `IntegrationRecord` on disk with a continuation retry gate key.

If you re-run `ralph`, the durable continuation retry gate checks whether the conflict identity, eligible resolver agents, and configuration files are identical to the prior attempt. If nothing has changed, Ralph Workflow suppresses re-invoking the resolver and emits a quiet `WAITING` status line rather than spinning or repeating critical alarms.

To force immediate re-entry into the retained conflict resolution workflow, run:

```bash
ralph --retry
```

(The alias `ralph --retry-integration` performs the identical action.)

### Safe triggers and operation limits

- **Gate bypass**: `ralph --retry` executes exactly one forced resolution attempt (`force_retry=True`), bypassing the durable gate. If resolution cannot complete, the retry gate key is updated on disk so subsequent non-forced executions remain quietly gated.
- **Configuration repair**: If conflict resolution was blocked by a configuration issue or malformed TOML (`CONFIG_FAILURE`), repairing any loaded configuration file releases the gate. The gate tracks changes across all active configuration sources: the primary workspace configuration (`.agent/ralph-workflow.toml` or `ralph-workflow.toml`), optional configurations (`.agent/agents.toml`, `.agent/mcp.toml`, `.agent/policy.toml`, or root equivalents), and global user configurations (`~/.config/ralph-workflow.toml`, `~/.config/ralph-workflow-agents.toml`). Repairing an offending configuration file updates the configuration fingerprint, which releases the gate and permits exactly one fresh resolution attempt: the running loop clears its waiting state and continues with a single continuation retry in the ongoing run, and a later invocation is permitted one attempt as well. Neither requires `--retry`. Legacy integration records lacking a gate key continue to execute as ungated. Run `ralph --check-config` to verify configuration syntax after edits.
- **Spent-chain escalation**: When candidate agents are temporarily cooling down with a positive delay, the engine emits `CANDIDATE_UNAVAILABLE` and awaits cooldown expiration rather than escalating permanently. When all configured resolver agents are permanently spent (`CANDIDATE_EXHAUSTED`), or when the pipeline cannot complete a conflict itself, the engine escalates to an actionable operator hand-off message naming each spent candidate and the exact git command needed to resolve the conflicted paths manually:
  ```bash
  git add -- <conflicted-paths>
  ```
  After resolving and staging conflicts manually, run `ralph --retry` to resume the pipeline.
- **Gitlink conflicts**: A diverged submodule pointer is not an operator case. The configured gitlink policy (`conflict_resolution.gitlink_policy.pointer_preference`, default `vendor-immutable`) selects the pointer, records the base, ours and theirs SHAs with the alternatives considered, and stages the conflict in place without invoking a resolver agent.
- **Terminal ladder state**: When the cleanup ladder reaches its last rung and no re-observed blocking signature can be supplied, the ladder ends in a terminal, operator-actionable state instead of an autonomous escalation. The message names the observed `HEAD`, the expected commit identities (`integrated_feature_sha`, `pre_target_sha`, `pre_feature_sha`, plus the pending stop anchor and tree when a rebase continuation is recorded), the preserved copy, and the operator action — land the expected commit and run `ralph --resume`. A preserved copy (a backup ref, or a sidecar patch and archive under `.agent/preserved/`) is created and named before any reset-capable rung runs; when there is nothing to preserve, the message says so rather than claiming a copy that does not exist. Unchanged facts do not start another round, so the terminal state is reported once.

## Common failure modes and what they mean

| Symptom                                          | Likely cause                                  | Fix                                                                |
| ------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------ |
| "Agent `codex` not found on PATH"               | The agent CLI is not installed or not on PATH | Install the CLI; ensure `which codex` works in the same shell      |
| "MCP upstream `fetch` unreachable"              | Network/auth or wrong URL                     | Verify the URL, check `MCP_AUTH_TOKEN` if set, retry `ralph --check-mcp` |
| "Capability `web.search` degraded"              | Web search provider missing or out of quota   | Set a provider key in `ralph-workflow.toml` or accept the degraded state |
| "Pre-flight policy validation: chain `dev → review` unsatisfiable" | An agent name in the chain is unknown | Run `ralph --list-agents`, fix the chain or install the missing CLI |
| "PROMPT.md is the starter template"             | You haven't replaced the starter text         | Edit `PROMPT.md` and remove the `<!-- ralph:starter-prompt ... -->` marker |

For a child process that exits unsuccessfully, the warning or error line identifies it with `label=` and shows only its executable plus the number of withheld arguments in `command=`. A label whose non-zero exit is an answer rather than a failure — for example a detached-HEAD observation, an ancestry answer, or an absent `MERGE_HEAD` — is logged at DEBUG instead of WARNING. The set of such labels is declared once, in the process manager's exit-code table, and the audit `audit_integration_invariants` fails the build if a second module decides that severity.

Every integration terminal line, and the run header, carries the build that produced it as ` [build=<package version>+<commit SHA>]`. A log line without that suffix, or with a different build, was produced by a different installation.

## Related pages

- [Agent CLI lifecycle](agents.md) — selection, detection, and invocation of
  every supported agent
- [CLI reference](cli.md) — every CLI flag including the diagnostic flags
- [Troubleshooting](troubleshooting.md) — when a real run has already failed
- [Configuration](configuration.md) — the config files diagnostics validates

## Filesystem-health warnings

`ralph --diagnose` reports Spotlight and `.fseventsd` signals as likely
external filesystem activity on macOS. The warning names the affected volume
and confirms that Ralph Workflow continues with bounded workspace awareness; no
privileged host change is required for normal operation.

If the host pressure still needs investigation, inspect the reported volume
with the platform's normal monitoring tools. Do not disable indexing, backup,
or filesystem services merely to run Ralph. Ralph-managed workspace changes
remain filtered, coalesced, and bounded independently of those services.
