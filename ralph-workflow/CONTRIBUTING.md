# Contributing to Ralph Workflow (Python)

This directory contains the maintained Python package.

## Development setup

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
make dev
```

To refresh the runnable `ralph` executable from the current checkout, run:

```bash
make install
```

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

### Test budget policy — 30 seconds, combined total

`make verify` enforces an **immutable 30-second combined test budget** across all test suites:

| Scope | Limit | Enforcer |
|-------|-------|----------|
| Per individual test | 1 s | `conftest.py` SIGALRM watchdog |
| Per suite invocation | 30 s | `python -m ralph.verify_timeout --suite-timeout 30` |
| All test suites combined (`make test`) | 30 s | `ralph.verify._TOTAL_TEST_BUDGET_SECONDS = 30.0` |

This 30-second combined budget is **absolute** and cannot be circumvented by:
- Splitting tests into more suites or shards
- Moving slow tests to a different target
- Raising `DEFAULT_SUITE_TIMEOUT_SECONDS` or `PYTEST_SUITE_TIMEOUT_SECONDS`

A slow test is a design defect. Fix the production coupling (extract I/O behind `MemoryWorkspace`, use `FakeAgentExecutor`). See `docs/agents/testing-guide.md` for the full no-I/O test policy.

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

- **IdleWatchdog** (`ralph/agents/idle_watchdog.py`) — owns in-stream timeouts:
  - `SESSION_CEILING_EXCEEDED` — absolute wall-clock ceiling; activity cannot reset it.
  - `NO_OUTPUT_DEADLINE` — idle deadline since last output (+ drain window).
  - `CHILDREN_PERSIST_TOO_LONG` — cumulative WAITING_ON_CHILD ceiling.
- **PostExitWatchdog** (`ralph/agents/post_exit_watchdog.py`) — owns post-exit timeouts:
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
Both the in-stream idle-timeout path (`classify_quiet` in `execution_state.py`) and the
post-exit path (`classify_exit` / `_evidence_precedence`) must call this function rather
than reimplementing the precedence rules independently.

See `ralph/agents/post_exit_watchdog.py` for the full post-exit transition matrix and
verdict semantics.

## OpenCode session continuation and completion contract

OpenCode is a session-based agent that may spawn child agents or delegate background work. Ralph Workflow
models its lifecycle explicitly through `OpenCodeExecutionStrategy` in `ralph/agents/execution_state.py`:

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
