# Contributing to Ralph Workflow (Python)

This directory contains the maintained Python package.

## Development setup

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
make dev
```

### Dev build vs stable build

You can keep a live **dev build** (your working tree) and a pinned **stable build**
side by side. They never collide because the dev build registers no global command.

| | Command to run it | How to install / refresh | Tracks |
|---|---|---|---|
| **Dev build** | `rdev …` (anywhere) or `uv run ralph …` (from the repo) | `make install` | your working tree, live — no reinstall after edits |
| **Stable build** | `ralph …` (anywhere) | `make stable` | a pinned release, isolated via `uv tool` |

- **Dev build** — `make install` syncs the project's uv environment (editable
  project + dev extras) and writes an `rdev` launcher to `~/.local/bin/rdev`.
  `rdev` runs the working tree from anywhere (it's `uv run ralph` against this
  checkout) and picks up edits with no reinstall. From inside the repo you can
  also just use `uv run ralph`. There is deliberately **no global `ralph`** for
  the dev build — the distinct `rdev` name is what keeps it from shadowing the
  stable one. (`make dev` does only the env sync, without the launcher.)
- **Stable build** — `make stable` runs `uv tool install --force --upgrade
  ralph-workflow`, putting an isolated `ralph` on your `PATH`
  (`~/.local/bin/ralph`), independent of the working tree. Re-running `make
  stable` **upgrades to the latest published release** if you are behind
  (`--upgrade` implies `--refresh`, so it re-checks PyPI; `--force` is what lets
  it reinstall over an existing install instead of no-op'ing). To pin a specific
  version instead of the latest release:

  ```bash
  python -m ralph.install --version 0.8.14   # or: uv tool install ralph-workflow==0.8.14
  ```

- **Switching** — type `rdev` for the dev build and `ralph` for the stable
  build; nothing to toggle. Verify which is which with:

  ```bash
  rdev --version   # -> working-tree version  (~/.local/bin/rdev)
  ralph --version  # -> stable release version (~/.local/bin/ralph)
  ```

  Bump the stable pin later with `uv tool upgrade ralph-workflow`, or remove it
  entirely with `uv tool uninstall ralph-workflow`. Remove the dev launcher with
  `rm ~/.local/bin/rdev`.

> `uv` is required for the dev and stable builds, and `~/.local/bin` must be on
> your `PATH`. Do not install the dev build as a global `ralph` (via pipx or
> `uv tool`) — it would shadow the stable one, which is exactly the collision the
> separate `rdev` name avoids.

When adding or renaming fields on `UnifiedConfig` / `GeneralConfig` in `ralph/config/models.py`, also update the bundled user-global template at `ralph/policy/defaults/ralph-workflow.toml` so new users see the documented default.

## Policy-driven pipeline

Ralph Workflow's pipeline behavior is declared in TOML policy files, not in runtime code.
The runtime is a generic policy interpreter that validates and enforces these declarations.

**Adding a new phase:**

1. Add the phase to `ralph/policy/defaults/pipeline.toml` with the correct `role` field.
2. Set all fields required for that role (`loop_policy` for `analysis`, `commit_policy` for `commit`, etc.).
3. Add a matching drain binding in `ralph/policy/defaults/agents.toml` and `ralph/policy/defaults/ralph-workflow.toml`.
4. Add an artifact contract in `ralph/policy/defaults/artifacts.toml` if the phase emits artifacts.
5. Run `ralph/policy/validation.py:validate_policy_completeness` logic is exercised by `make verify` — fix any validation errors before merging.

**Adding a new phase role:**

1. Add the role literal to `PhaseRole` in `ralph/policy/models.py`.
2. Add role-specific required-field validation to `validate_policy_completeness` in `ralph/policy/validation.py`.
3. Update the `POLICY COMPLETENESS` comment in `ralph/policy/defaults/pipeline.toml`.
4. Add tests in `tests/test_policy_validation.py` covering the new validation rule.

**Adding a new budget or loop counter:**

Adding a new budget counter or loop iteration counter is a `pipeline.toml`-only change. No runtime code changes are required.

- For a budget counter (e.g., iteration caps): add a `[budget_counters.<name>]` entry with `default_max` and `description`.
- For a loop counter (e.g., per-analysis iteration bounds): add a `[loop_counters.<name>]` entry with `default_max` and `description`, then reference the counter name in the phase's `loop_policy.iteration_state_field` field.
- Override counter caps at run time with `--counter NAME=VALUE`; the counter name must be declared in `pipeline.toml` or Ralph Workflow will reject the run with a validation error.
- The runtime automatically tracks and enforces every counter declared in policy.

**Changing workflow behavior** (routing, retries, analysis bounds, commit semantics):
Update the relevant `pipeline.toml` fields instead of adding code branches. If behavior is not expressible as policy, first extend the policy schema — do not add hardcoded phase-name logic to the reducer.

## Required verification

Run this before opening or updating a PR:

```bash
make verify
```

`make verify` now emits a high-visibility failure banner that cites `AGENTS.md` and `CLAUDE.md` so AI agents are explicitly told to stop and fix the failing check immediately.

### Test budget policy — 60 seconds, combined total

`make verify` enforces an **immutable 60-second combined test budget** across all test suites:

| Scope | Limit | Enforcer |
|-------|-------|----------|
| Per individual test | 1 s | `conftest.py` SIGALRM watchdog |
| Per suite invocation | 60 s (SECONDARY cap) | `python -m ralph.verify_timeout --suite-timeout 60` |
| All test suites combined (`make test`) | 60 s (AUTHORITATIVE cap) | `ralph.verify._TOTAL_TEST_BUDGET_SECONDS = 60.0` (enforced via cumulative `time.monotonic()` tracking) |

This 60-second combined budget is **absolute** and cannot be circumvented by:
- Splitting tests into more suites or shards (cumulative tracker sums ALL tracked steps)
- Moving slow tests to a different target
- Raising `DEFAULT_SUITE_TIMEOUT_SECONDS` or `PYTEST_SUITE_TIMEOUT_SECONDS`
- Modifying `_TOTAL_TEST_BUDGET_SECONDS` or `_BUDGET_TRACKED_STEPS` (blocked by import-time `if`/`raise RuntimeError` checks — immune to `python -O`)

The combined budget is enforced at the verify runner level (`ralph/verify.py`). Per-suite timeouts are secondary caps only. The total elapsed time of every test suite running sequentially under `make verify` must not exceed 60 s. Splitting tests across N suites does NOT give N × 60 s.

A slow test is a design defect. Fix the production coupling (extract I/O behind `MemoryWorkspace`, use `FakeAgentExecutor`). See `docs/agents/testing-guide.md` for the full no-I/O test policy.

For cumulative volume bottlenecks (many fast tests with per-item overhead dominating), valid fix strategies include consolidating parameterized tests with overlapping coverage, optimizing shared fixtures, or reducing redundant test coverage. Do NOT disable, skip, or quarantine tests to work around the budget.

The dead-code audit is available separately while the existing dead-code backlog is still being cleaned up:

```bash
make dead-code
```

`make dead-code` uses Vulture and is expected to fail until the repo is fully cleaned. Keep it separate from `make verify` for now so the tooling can be validated without blocking unrelated work.

You can narrow failures with:

```bash
ruff check ralph/ tests/
ruff format --check ralph/ tests/
uv run python -m mypy ralph/
make test-cov
make test
make test-unit
make test-integration
```

## Documentation expectations

- Update user-facing Markdown when workflows or commands change.
- Update public module/package docstrings when APIs change.
- Keep exported package docstrings self-sufficient enough for `pydoc` users.
- New public subpackages added under `ralph/` must have an `.. automodule::` entry in `docs/sphinx/modules.rst`. The test `tests/test_sphinx_modules_coverage.py` enforces this — update `_EXCLUDED` in that test if the subpackage is intentionally private.
- When changing pipeline hardening around artifacts or agent success criteria, document both the behavior change and the failure mode it prevents. Future contributors need to understand why the stricter contract exists.

## Typing suppression policy

- Blanket `# type: ignore` comments are forbidden.
- The full policy lives at `docs/agents/type-ignore-policy.md`.
- Any unavoidable suppression must include the narrowest mypy error code and end with exactly one policy reason:
  - `# reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library`
  - `# reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code`
- No other reason text is allowed in `# type: ignore[...]` comments.
- `external library has no type support` is allowed only when the suppression exactly matches the policy case in `docs/agents/type-ignore-policy.md#external-library`. If the real blocker is first-party Ralph Workflow code, the suppression must be removed.
- Prefer typed adapters, local stubs, or precise `cast(...)` calls over `# type: ignore` whenever possible.

## Agent hardening contract

When working on `ralph/pipeline/runner.py`, `ralph/phases/`, or Claude/CCS agent invocation, preserve these invariants unless you are deliberately replacing them with something stronger:

1. A clean subprocess exit is not enough evidence of useful work for `review`; `development` and `fix` must still produce real workspace side effects, not empty no-op runs.
2. `review` depends on a fresh per-phase artifact created during the current invocation; `development` must submit `development_result` with proof entries covering every plan step (and every prior how_to_fix item when development_analysis feedback exists). Proof policy is enforced by `[phases.development.artifact_proof_policy]` in `pipeline.toml` and can be disabled with `require_plan_proof = false` and `require_analysis_proof = false` in a project-local `.agent/pipeline.toml`.
3. The runner clears stale per-phase artifacts before invoking the agent so interrupted runs cannot satisfy later checks accidentally or leak old summaries into later phases.
4. Claude/CCS MCP invocations must avoid half-configured tool restriction flags. If the live MCP allowlist cannot be discovered, prefer the safer strict-MCP path over emitting brittle `--tools ""` combinations.

## Agent timeout contract

All wall-clock timeout decisions in the agent invocation system are consolidated behind two
watchdog controllers, both using Clock-injected time for deterministic FakeClock-driven testing:

- **IdleWatchdog** (`ralph/agents/idle_watchdog/idle_watchdog.py`) — owns in-stream timeouts:
  - `SESSION_CEILING_EXCEEDED` — absolute wall-clock ceiling; activity cannot reset it.
  - `NO_OUTPUT_DEADLINE` — idle deadline since last output (+ drain window).
  - `CHILDREN_PERSIST_TOO_LONG` — cumulative WAITING_ON_CHILD ceiling.
- **PostExitWatchdog** (`ralph/agents/idle_watchdog/_post_exit_watchdog.py`) — owns post-exit timeouts:
  - `PROCESS_EXIT_HANG` — subprocess closed stdout but did not exit within budget.
  - `DESCENDANT_HANG` — descendant_wait deadline elapsed with WAITING_ON_CHILD persistent (post-exit only).
  - Parent-exit grace window (`wait_parent_exit_grace`).
  - Descendant-quiesce window (`wait_descendant_quiesce`).

**Forbidden:** No ad-hoc `clock.monotonic()` / `clock.sleep()` loops are allowed in
`invoke.py`. Every wall-clock decision must route through one of the two watchdogs to
remain testable. The `FakeClock` / `Clock` seam is the only mechanism for deterministic
timeout testing; real `time.sleep()` in production tests is a test-smell that indicates
a missing watchdog seam.
- The read loop must call `watchdog.evaluate()` on every iteration, including the
  post-yield path, so `SESSION_CEILING_EXCEEDED` cannot be defeated by continuous output.
- The read loop must defensively wrap `classify_quiet` so a transient liveness probe
  exception cannot silence the watchdog (default to `WAITING_ON_CHILD` on exception so
  the cumulative child-wait ceiling remains in force).

**Canonical child-evidence model:** All stale-vs-fresh child-liveness decisions flow
through one function: `classify_child_snapshot()` in `ralph/process/child_liveness.py`.
Both the in-stream idle-timeout path (`classify_quiet` in `ralph/agents/execution_state/opencode_execution_strategy.py`) and the
post-exit path (`classify_exit` / `_evidence_precedence`) must call this function rather
than reimplementing the precedence rules independently.

**Per-channel activity evidence model (idle watchdog):** The idle watchdog treats
real work happening on any of four channels as evidence the session is NOT idle:

| Channel | Source | Recorder |
|---|---|---|
| `stdout` | The agent's stdout output (the baseline) | `record_activity()` / `record_lifecycle_activity()` |
| `mcp_tool` | An MCP `tools/call` invocation/completion (Ralph Workflow MCP server) | `record_mcp_tool_call()` via `ralph/mcp/server/_activity_sink.py` |
| `subagent` | A subagent progress / heartbeat / tool_call signal routed through `OpenCodeExecutionStrategy.observe_line` | `record_subagent_work()` via the parallel subagent contextvar |
| `workspace` | A workspace file change captured by `WorkspaceMonitor.record_event` | `record_workspace_event()` |

While ANY non-stdout channel age is below
`agent_idle_activity_evidence_ttl_seconds` (default 30.0s under `[general]` in
`ralph-workflow.toml`; set to `0.0` to disable), the watchdog defers
a `NO_OUTPUT_DEADLINE` fire and returns `WatchdogVerdict.CONTINUE` with a debug
log. The absolute `SESSION_CEILING_EXCEEDED` and `CHILDREN_PERSIST_TOO_LONG`
ceilings are checked BEFORE the deferral, so they remain absolute (activity
cannot reset either ceiling). The diagnostic embedded in every watchdog fire
carries the per-channel `evidence_summary` (channel name, last_at, age_seconds,
counter) so an on-call operator or post-mortem can see exactly which channels
were fresh and which were stale at the moment the watchdog fired.

Workspace evidence collection runs whenever a run has a `workspace_path`,
regardless of whether the progress UI (`show_progress`) is enabled, so quiet
unattended runs that do real file work are not falsely killed. Activity is
**demonstrated work**, not mere existence: an OpenCode subagent process that
is alive but has produced no output, no tool calls, and no file changes for
the configured idle window is **not** evidence of progress. Once scoped Ralph Workflow
child evidence goes stale, the run falls back to the normal idle timeout
instead of lingering under the larger cumulative waiting-on-child ceiling.
Raw OS descendants alone defer the verdict only when Ralph Workflow never had scoped
visibility into the child in the first place.

The three recorders are additive on top of `record_activity()`: they update
per-channel `_last_at` timestamps and counters WITHOUT touching `_last_activity`
(the stdout baseline). The existing 'stdout only resets idle baseline'
invariant is preserved; the activity-aware verdict is layered on top without
perturbing the existing semantics. See `tests/agents/test_idle_watchdog_3.py`,
`tests/mcp/test_mcp_activity_sink.py`, and
`tests/agents/test_subagent_activity_wiring.py` for the black-box regression
suite covering this contract.

**Upstream MCP coverage.** The `mcp_tool` channel covers both in-process
Ralph Workflow tool calls and upstream (third-party) MCP tool calls proxied through
`UpstreamProxyHandler` (`ralph/mcp/tools/bridge/_upstream_proxy_handler.py`).
The single emission point for any `tools/call` is `McpServer._handle_tools_call`,
which records the call on the `mcp_tool` channel before dispatch; the proxy is a
pure pass-through and does not emit again, so a delegated upstream tool call
refreshes the `mcp_tool` channel exactly once, just like a native Ralph Workflow
tool call. Set `agent_idle_activity_evidence_ttl_seconds = 0.0` only when you
want to opt out of the activity-aware verdict entirely and restore the legacy
stdout-only behavior.

See `ralph/agents/idle_watchdog/_post_exit_watchdog.py` for the full post-exit transition matrix and
verdict semantics.

### Watchdog two-state invariant

The recovery controller has exactly two recovery paths. There is no third
state; a future PR that needs to introduce a new recovery state MUST update
the import-time invariants below in the same commit. The two MAIN RULES
(quoted verbatim from the user's prompt) are:

> There are two types of retries: exponential backoff to the next agent, OR
> retry with the same agent. There is never a state where we skip an agent
> permanently. All agents are recoverable. We never exit the pipeline because
> of agent unavailability.

The two MAIN RULES are wired as:

- **Exponential backoff to the next agent** -- driven by
  `RecoveryController._mark_agent_unavailable` in
  `ralph/recovery/controller.py`, which calls
  `AgentUnavailabilityTracker.mark_unavailable` (per-reason
  `ReasonBackoffPolicy` exponential backoff capped at `max_backoff_ms`).
  The chain advances to the next available agent; `wrap=True` re-arming
  in `_next_available_agent_index` reconsiders earlier agents whose
  cooldown has expired.
- **Same-agent retry** -- driven by
  `RecoveryController._apply_chain_retry`, which calls
  `AgentChain.with_retry_increment` and re-invokes the same agent in
  place (chain retries is incremented; chain index does not advance).

The invariant is locked at import time in two places:

- `ralph/recovery/controller.py` -- `_assert_two_state_invariant` walks
  the `RecoveryController` class source via `ast` and asserts the two
  required methods are present (`_mark_agent_unavailable` and
  `_apply_chain_retry`). The check uses `if/raise RuntimeError` (NOT
  `assert`) so it survives `python -O` per AGENTS.md.
- `ralph/recovery/agent_unavailability_tracker.py` --
  `_assert_no_permanent_skip_invariant` walks the
  `AgentUnavailabilityTracker` class source and asserts the only two
  public mutators on the unavailable set are `mark_unavailable` and
  `reset_backoff`. Every other public method (`is_available`,
  `earliest_unavailable_wait_ms`, `snapshot`, `scope`) is read-only.
  The constructor (`__init__`) is allowed to seed `_entries` from
  `initial_timeouts` because that is operator-provided config, not a
  runtime mutation.

The never-exit invariant (the pipeline never exits because of agent
unavailability) is implemented by the all-agents-unavailable wait
branch in `RecoveryController._handle_retry_progression`. When every
agent in a chain is on cooldown, the controller returns the canonical
3-tuple `(new_state, effects, failure_event)` with
`is_waiting_state=True`, `last_retry_delay_ms=<earliest_cooldown>`,
and `effects=[]`. The run loop sleeps on `last_retry_delay_ms` and
re-enters the same phase; the pipeline never reaches
`failed_terminal` via this path.

The dumb-kill floor (NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS,
default 120.0s) prevents the watchdog from killing a recently-launched
agent that is doing real thinking work (planning, exploration,
dispatching subagents) but has not yet produced first-party activity
evidence. The field is `float or None` with `gt=0.0` when set; 0.0 is
rejected by pydantic and `TimeoutPolicy.__post_init__`. The
`SESSION_CEILING_EXCEEDED` reason is unaffected by the floor
(operator-set hard cap). See `ralph/timeout_defaults.py` for the
constant and `ralph/agents/idle_watchdog/timeout_policy.py` for the
validator.

**How to evolve.** A future PR that needs to introduce a new recovery
state MUST update the import-time invariant in
`ralph/recovery/controller.py` and the test in
`tests/recovery/test_two_state_invariant.py` in the same commit. The
pipeline never assumes an agent is permanently broken; any new state
must be reversible via cooldown expiry or explicit reset, and the new
state must be added to the canonical 3-tuple return shape of
`RecoveryController.handle` (per `controller.py:144`).

## OpenCode session continuation and completion contract

OpenCode is a session-based agent that may spawn child agents or delegate background work. Ralph Workflow
models its lifecycle explicitly through `OpenCodeExecutionStrategy` in `ralph/agents/execution_state/opencode_execution_strategy.py`:

**Completion contract:** An OpenCode run is only declared terminal-complete when at least one of
these conditions is true:

- The required phase artifact exists on disk (`required_artifact_present=True`), OR
- The agent explicitly called the `declare_complete` MCP tool (`explicit_complete=True`).

A clean process exit (exit code 0) alone is **not** sufficient for success. If neither signal is
present, Ralph Workflow raises `OpenCodeResumableExitError` and the runner retries the same OpenCode session
(preserving `session_id`) rather than restarting from scratch.

**Session continuation:** `OpenCodeResumableExitError.resumable_session_id` carries the session ID
extracted from the NDJSON output. The runner threads this ID into the next invocation via
`InvokeOptions.session_id`, and the OpenCode `session_flag` (`--session {}`) ensures the same
session resumes instead of a new one being created. Budget tracking (`max_same_agent_retries`) caps
resumable retries the same as ordinary failures.

**Stale session detection:** If OpenCode reports a stale or invalid session (messages containing
`'Session not found'`, `'Unknown session'`, `'session does not exist'`, or
`'No conversation found with session ID:'`), `FailureClassifier` maps the failure to
`FailureCategory.AGENT` with `reset_session=True` so the next attempt starts a fresh session.

## MCP multimodal compatibility contract

When modifying the MCP tool surface, maintain these invariants:

### Existing text-only tools unchanged

All existing MCP tools (`read_file`, `write_file`, `list_directory`, etc.) must continue returning text content blocks with the same JSON shape. Any change that alters the wire format of existing text tools is a breaking change.

### Multimodal support is default-on with explicit opt-out

The `read_media` tool (primary) and `read_image` tool (compatibility alias) and the associated `MediaRead` capability:

- Default to enabled (`media.enabled = true`)
- Can be disabled via `[media]` section in `mcp.toml` with `enabled = false`
- Are gated at registration time (tools only registered when `media.enabled = true`)
- Support broad modality classes: images, PDFs, documents, audio, video, and resource/file-reference-based flows
- Automatically detect what the active provider/model supports and select inline vs resource-reference delivery accordingly

### Client capability filtering

When a client sends `initialize` without declaring multimodal support (`capabilities.image`, `capabilities.media`, or `capabilities.multimodal`), multimodal-only tools must not appear in `tools/list`.

### Upstream multimodal boundary

When an upstream MCP server returns a non-text content block, Ralph Workflow normalizes it rather than rejecting it:

- **URI-backed content**: the external URI is preserved as-is in a `resource_reference` block
- **Embedded-data content**: the bytes are stored in the session `MediaManifest` and a `resource_reference` block with a `ralph://media/...` URI is returned

### Dead code policy

Any MCP code that is proven unused during feature work must be either:

1. Wired into the maintained runtime with tests proving real use, or
2. Deleted along with its imports and stale tests/docs

Do not leave "reserved for later" MCP scaffolding behind. If in doubt, remove it — it can be restored from git if needed later.

## MCP server lifecycle contract

When working on `ralph/mcp/server/` or `ralph/pipeline/runner.py`, preserve these invariants:

1. **`RestartAwareMcpBridge` is the only restart mechanism.** MCP servers must not be restarted by calling `start_mcp_server()` again from arbitrary callsites. All restart logic lives inside `RestartAwareMcpBridge._restart_fn()`, which is closed at bridge creation time.
2. **Preflight runs on every spawn.** `_spawn_mcp_process()` calls `deps.preflight()` before returning the new `StandaloneMcpProcess`, whether on initial startup or after a crash restart. Any preflight failure aborts the restart and propagates the error.
3. **Unhealthy means exited OR probe-failed.** `check_health_and_restart_if_needed()` treats the server as unhealthy when either the subprocess has exited **or** the subprocess is alive but the responsiveness probe (`probe_mcp_http_endpoint`) fails. Both cases trigger the same terminate-respawn-preflight path. The probe uses a fresh isolated session and never touches the agent's active session.
4. **Budget exhaustion raises `McpServerError`.** When `RestartAwareMcpBridge._restart_count` reaches `McpRestartPolicy.max_restarts`, `check_health_and_restart_if_needed()` raises `McpServerError(restart_count=n)` instead of attempting another spawn.
5. **`check_mcp_bridge_health` is called per retry attempt.** In `runner.py`, the health check must execute at the top of every retry loop iteration so crashed or hung servers are detected before the agent is invoked, not after.
6. **`ProcessManager` owns all process spawning.** Every subprocess — MCP server or AI agent — must be registered with `ProcessManager`. Do not call `subprocess.Popen` or similar outside `ProcessManager`.

## Recovery architecture contract

Recovery, failure classification, retry counting, and chain fallover each have a single conceptual owner in `ralph/recovery/`. Extend the owner, do not add handlers at call sites. New failure modes are added by extending the `FailureClassifier` in `ralph/recovery/classifier.py`, not by sprinkling classification logic at invoke sites.

### Technical retry contract (mandatory)

All direct technical retries must stay on one shared contract. This includes stale-session recovery, timeouts, transient connectivity failures, ambiguous recoverable faults, and artifact-submission / artifact-validation retries that re-run the same task.

Required invariants:

1. **Single cap family:** direct technical retries must be governed by the shared `general.max_same_agent_retries` cap (threaded into runtime owners), not ad-hoc per-call counters.
2. **Single error format:** retry reprompts and retry hints must use the shared formatter in `ralph/recovery/retry_prompt.py`, with the failure/error block first and prompt/context references secondary.
3. **Loopbacks are separate:** analysis/validation loopbacks are not technical retries and must not silently reuse the technical retry counter path.
4. **Tests are required at both seams:** changes to technical retry behavior must update (a) runner-level tests for direct retry prompt generation and (b) recovery/controller-level tests for cap enforcement and chain progression.
5. **No new retry writers at call sites:** if a new recoverable technical failure is introduced, route it through the shared technical retry owner instead of adding a new retry loop or prompt builder in place.

## Skill bundle maintenance

- Add or adopt a mirrored default skill by updating the mirrored content under `ralph/skills/content/`, adding the name to `BASELINE_SKILL_NAMES` in `ralph/skills/_content.py`, updating the provenance metadata, and mirroring it in `skills-package/bin/skills.js` as `SKILL_NAMES`.
- Rename a mirrored default skill by updating both lists together, renaming the mirrored content file, and updating provenance metadata accordingly.
- Keep `skills-package/` in sync through its `prepack` script, which copies `ralph/skills/content/` into the npm package before publish.
- Run `uv run pytest -q tests/test_skills_package_skill_names_parity.py` after every skill-list change.
- Update `docs/sphinx/modules.rst` whenever you add a new public module under `ralph/skills/`; `tests/test_sphinx_modules_coverage.py` enforces that rule.

## Release and versioning

For the complete release process — version bumping, building, validating, and publishing
to PyPI — see [docs/sphinx/versioning.md](docs/sphinx/versioning.md).

For local validation only:

```bash
cd ralph-workflow
rm -rf dist
uv run hatch build
uv run python -m twine check dist/*
```

## How to add a new agent

The fastest path is the 5-minute [quickstart](docs/agents/quickstart-add-a-new-agent.md) — it shows the opinionated 5-line `register_my_agent` recipe for both headless and interactive agents and is the recommended entry point.

For the 14-kwarg advanced form, CCS aliases, parser authoring, and the full lifecycle, see the canonical reference: [adding-a-new-agent.md](docs/agents/adding-a-new-agent.md).

That reference covers three primary workflows, all reachable from a single first-click:
- **Add**: Registering a new headless or interactive agent using `register_agent_support` (advanced) or `register_my_agent` (the 90% case).
- **Update**: Modifying catalog entries (requiring a prior `remove`) or updating the caller's `AgentRegistry`.
- **Remove**: Deleting catalog entries via `default_catalog().remove(name)` or registry entries via `del registry.agents[name]`.
