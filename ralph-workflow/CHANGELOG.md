# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`DisplayContext` single source of truth for rendering.** All display code now receives an
  injected `DisplayContext` (frozen dataclass) that owns the Rich console, Okabe-Ito theme,
  resolved terminal width, colour policy, and adaptive layout limits. No renderer constructs
  its own `Console`. Obtain one via `make_display_context()` exported from `ralph.display`.
- **Real terminal-mode detection (`compact` / `wide`).** `detect_mode` now returns
  `'compact'` for terminals narrower than 60 columns and `'wide'` for wider ones.
  `RALPH_FORCE_NARROW=1` forces compact mode regardless of width. `COLUMNS`, `NO_COLOR`,
  and `FORCE_COLOR` are all respected. Adaptive limits (headline cap, condenser thresholds,
  streaming checkpoint size) differ per mode.
- **Semantic theme keys replace literal Rich style strings.** `phase_banner.py`, `cli/main.py`,
  and all display modules now reference theme keys (`theme.text.muted`, `theme.banner.title`,
  etc.) instead of raw colour strings. The Okabe-Ito palette is the single colour source.
- **`RALPH_FORCE_NARROW` env knob.** Set to `1` / `true` / `yes` / `on` to force compact
  rendering on wide terminals (useful for screenshots or constrained CI output).
- **Custom policy workflow contract test (`tests/test_custom_policy_workflow.py`).** Demonstrates that Ralph Workflow routes on user-defined phase, drain, loop counter, and budget counter names (`design`/`build`/`audit`/`sign_off`/`done`, counter `cycles`, loop `audit_round`) without any runtime knowledge of the canonical defaults. Proves the policy-driven contract via `validate_policy_completeness`, `explain_policy`, `resolve_post_commit_phase`, and `with_loop_iteration` on a fully renamed bundle.
- **Explanation sentences for `bypass_routes` and loopback caps in `ralph --explain-policy`.** `_render_explanation_sentences` now emits `Explanation: phase 'X' bypasses to 'Y' when the configured outcome is 'Z'` for every bypass route (sorted for determinism) and `Explanation: phase 'X' loops back to 'Y' until N attempts are exhausted` when a loop cap applies, satisfying Required Product Outcome D for every routing surface.
- **ASCII workflow diagram in `ralph --explain-policy`.** The command now prints a deterministic boxed-node diagram of the active pipeline (entry marker, happy-path arrows, decision branches, loopback edges, terminal success/failure markers, and fan-out/loop annotations) above the existing structural breakdown. Renders pure-ASCII glyphs (no Unicode box characters) so it is safe in code reviews, CI logs, and runbooks. See `docs/sphinx/policy-explanation.md` for the legend.
- **Same-workspace parallel workers v1.** When the planning agent declares two or more disjoint
  `work_units`, Ralph now runs them as parallel workers in the SAME git checkout using
  `ParallelExecutionMode.SAME_WORKSPACE`. Each worker is restricted to its declared
  `allowed_directories`; coordination is by edit-area fencing and per-worker artifact namespaces
  only — no worktrees, no separate git branches for each worker, no post-fanout merge step.
- **Runner-boundary parallel preflight.** `_determine_effect_from_policy` now calls
  `validate_work_units_against_policy` before constructing `FanOutDevelopmentEffect`. Plans with
  overlapping edit areas, missing `allowed_directories`, or reserved paths produce an immediate
  `ExitFailureEffect` with a reason containing `parallel preflight rejected plan:` rather than
  silently degrading.
- **Serialized post-fanout verification (opt-in).** A new `parallel_execution.post_fanout_verification`
  policy field (default `false`) controls whether a workspace-wide `make verify` runs after all
  workers finish. Verification is skipped when any worker failed. The result is captured in a
  `PostFanoutVerificationEvent` that the reducer uses to mark the phase as failed on non-zero exit.
- **`PostFanoutVerificationEvent`.** New event in `ralph.pipeline.events` and handled in the
  reducer. `success=False` routes to `PHASE_FAILED`; `success=True` is a no-op.
- **`parallel_development_summary.json`.** Written to `.agent/artifacts/parallel_development_summary.json`
  after fan-out completes. Records per-worker status (`succeeded`, `failed`, `blocked`, `cancelled`),
  artifact counts, and verification outcome. Worker success is based on worker-local artifact
  evidence only — repo-wide git state is never used.

### Changed
- **`_compute_budget_state` is fully policy-driven.** Budget-state labels (`remaining`/`exhausted`/`no_review`) now work for any counter declared in `[budget_counters]`, not just the canonical `iteration`/`reviewer_pass` names. The function reads only `pipeline_policy.budget_counters` and `state.get_budget_remaining(counter)` — no hardcoded counter names remain in routing-predicate logic.
- **ASCII workflow diagram renders loopbacks unambiguously.** The old `<--[loopback]-- target` glyph is replaced with `    | loop back to target` / `    +---^  (returns to 'target' phase)` below the looping phase box. When the loopback also consumes a loop counter, a third line `    [LOOPBACK: counter=NAME, max=N]` follows so readers can distinguish counted retries from uncounted routing.
- `FanOutDevelopmentEffect.run_post_fanout_verification` now defaults to `False` and is driven by
  the `parallel_execution.post_fanout_verification` policy field. The previous hardcoded `True`
  would have triggered `make verify` in all fan-out runs, including unit tests.
- **Display: completed DisplayContext dependency injection across CLI runner and command modules.**
  Module-level `console` globals removed from `init.py`, `commit.py`, and `runner.py`; all rendering
  now threads `DisplayContext` explicitly. `_LegacyConsoleDisplay` accepts an optional injected
  context and uses it for themed output. Added a real `medium`-mode rendering branch in
  `phase_banner.py` (two `Rule` separators, no description text, no leading blank line) and
  compact-mode column suppression for tables (`show_agents` hides Parser/Can Commit, `show_providers`
  hides Status, `show_checkpoint_summary` hides Review Pass, `show_config` uses plain text instead of Panel).
- Documentation (`getting-started.md`, `concepts.md`, `parallel-mode.md`, `parallel-fan-out.md`)
  truthfully describes v1 same-workspace behavior: no worktrees, no separate git branches for each worker, no
  post-fanout merge step; soft isolation by path fencing and per-worker namespaces only.
- `worker_developer.jinja` now correctly includes `shared/_unattended_mode.jinja` (was
  `_unattended_mode.j2`) and explicitly tells workers they share the checkout.

### Removed
- Dead worktree-first parallel code paths. The only supported parallel execution mode is
  `ParallelExecutionMode.SAME_WORKSPACE`; alternative fan-out paths using git worktrees are not part of the shipped
  product.
- **Global `[parallel_execution]` block removed.** Parallelization is now configured per phase under
  `[phases.<phase>.parallelization]` in `pipeline.toml`. The `ParallelExecutionPolicy` model and the
  `PipelinePolicy.parallel_execution` field are gone. A `ValidationError` is raised for any config that
  still uses the old `[parallel_execution]` block. To migrate, rename the block to
  `[phases.development.parallelization]` and add `mode = "same_workspace"`. The pipeline now fails closed
  when a plan declares 2+ work units for a phase that has no `parallelization` policy.

### Changed
- OpenCode runs no longer treat foreground process exit as terminal success — completion now requires either an explicit completion signal or the required phase artifact to be present in the workspace.
- Idle/timeout evaluation for OpenCode considers Ralph-tracked agent labels (label prefix `agent:`) via the injectable `LivenessProbe` in addition to OS-level descendants, so quiet parents with live subagent work are not killed prematurely.
- **Missing PROMPT.md error now actionable.** When `ralph` is run without a `PROMPT.md`, the preflight error message now includes `Run \`ralph --init\` to scaffold PROMPT.md and project config files` so new users know exactly how to fix it.
- **ASCII banner shown on first run.** The Ralph ASCII banner is now printed above the 'Ralph first-run setup' panel on first invocation (and on `--regenerate-config`). It is suppressed on subsequent runs.
- **Self-teaching PROMPT.md template.** `ralph --init` now seeds `PROMPT.md` with a concrete example (Goal, Context, Acceptance criteria, Notes sections) instead of empty placeholders, so new users immediately see what a usable prompt looks like.
- **`ralph --diagnose` hint in Next steps.** The first-run welcome panel and the fallback next-steps list both now include a step recommending `ralph --diagnose` to validate the environment before the first pipeline run.
- **Install URLs for known missing agents.** When a known agent (`claude`, `opencode`) is reported as missing from PATH in the first-run panel, a one-line install hint with the official documentation URL is shown inline.
- **Verbose output is now the default.** Ralph surfaces phase banners, plan, analysis/decision state, agent activity, retries, and a final summary by default — output is the product of an agent system. Pass `--quiet` (or `-q`) to opt into the minimal, error-only variant. `--verbosity normal` is still accepted but mapped to verbose so existing wrapper scripts keep working.
- The live dashboard now renders `Plan`, `Analysis`, and `Decision Log` panels backed by `.agent/artifacts/plan.json` and the latest `_analysis_decision` artifacts, not just a prompt preview.
- Phase transitions emitted during a run are both printed above the live region and recorded into the dashboard's decision log.
- Pipelines now end with a `Pipeline Complete` (or `Pipeline Failed`) summary panel that echoes the plan, decision log, metrics, verification status, commit, PR URL, and open risks that the user saw live.
- **Log format extended with `MILESTONE` level and `META`/`CONT` category prefix.** Transcript lines now read `<ISO-TS> <LEVEL> <CAT> [<tag>][<unit>] <content>`. Phase transitions (`planning`, `development`, `review`, `fix`) use the `MILESTONE` level with a `◆ ` marker. All tags are classified as `META` (workflow metadata) or `CONT` (agent-produced content). Existing `INFO`/`SUCCESS`/`WARN`/`ERROR` levels are preserved for all other lines.
- **Completion summary uses rule-delimited sections.** `emit_completion_summary` now renders a Rich `Group` with a titled header rule and a separate rule per section (Plan, Metrics, Decisions, Verification, Activity Summary, Commit, Risks, Error). Decision rows carry `[PASS]`/`[WARN]`/`[FAIL]`/`[INFO]` badges. The new `Activity Summary` section reports total agent calls, optional thinking-block count, and optional raw overflow path.
- **Streaming block global-single invariant.** A `[content-start]` or `[thinking-start]` block from worker A is now automatically closed with the matching `*-end` line before a block from worker B can open. Only one streaming block is active at a time across all workers. Non-streaming events (tool use, lifecycle, etc.) also close any open streaming block before they are emitted.
- **Raw-overflow reference managed by `PlainLogRenderer`.** The `[see .agent/raw/<unit>.log]` suffix is now appended by the renderer (`condensed_ref` param on `emit_activity_line`) rather than embedded in the condenser output. `condense_content` called without `overflow_ref` now emits `(truncated)` instead of `(truncated, see raw unavailable)`.
- **Long-content summary is now default-on.** The `↳ summary:` headline line is shown for any content block exceeding 4000 display cells without needing to set an env variable. Set `RALPH_LONG_CONTENT_SUMMARY=0` (or `false`/`no`/`off`) to disable. The summary now uses a sentence-aware extractor (first sentence terminated by `.`, `!`, `?`, or newline), with a 200-character cap for inline summaries and 120 characters for streaming end-line summaries.
- **Streaming `*-end` lines now report fragment count and total char length.** The end-line for a streaming block reads `({n} fragments, {chars} chars)` followed by the headline summary, giving the reader a quick measure of how much content was streamed.
- **Streaming continue tags now carry a 1-based sequence number.** `[content-continue]` is now emitted as `[content-continue#2]`, `[content-continue#3]`, etc. so readers can track the progression of a streaming block.
- **`WARN META [progress]` for dropped ring-buffer lines.** The debounced dropped-line notification is now emitted at `WARN` level (not `INFO`) to match its documented severity and the changelog entry below.

- **Preflight guards unedited starter PROMPT.md.** When running `ralph` with a `PROMPT.md` that still contains the `<!-- ralph:starter-prompt ... -->` marker emitted by `ralph --init`, preflight now fails fast with an error that tells the user to edit the file first, preventing an accidental pipeline run against the example task.
- **`--init` docs now use the real contract.** Quick-start examples now use bare `ralph --init`, compatibility labels remain deprecated/ignored, and rerun output no longer prints a fake template selector.
- **Removed hidden `isolation_mode` compatibility plumbing.** The deprecated `--no-isolation` path is now gone end to end: CLI override input no longer accepts it, config overrides no longer synthesize it, and tests/default guidance only cover the supported execution flags.

### Added
- **`visit_url` MCP tool (built-in web fetcher).** A new `visit_url` tool fetches a single HTTP/HTTPS URL and returns readable extracted text. It is gated by a new `WebVisit` capability that is granted to **all 10 session drains** by default (including `analysis` and `commit` — unlike `web_search`). The tool requires the optional `[web-visit]` extras (`readability-lxml`, `selectolax`); without them, calls return `is_error=true` with a clear install hint. An SSRF guard blocks private/loopback/link-local addresses when `allow_private_networks=false` (default). Configure via `[web_visit]` in `mcp.toml`. See `docs/mcp/web-visit.md` for the full reference.
- **`WebVisit` capability** (`web.visit` / `WebVisit`) added to `Capability` and `McpCapability` enums and wired into the capability mapping layer with the same alias and policy evaluation as `WebSearch`.
- **`WebVisitConfig`** pydantic model in `ralph.config.mcp_models` with `enabled`, `timeout_ms`, `max_bytes`, `user_agent`, `allow_private_networks`, and `extract_links` fields. Added as `web_visit` field on `McpConfig`.
- **`[web-visit]` extras group** in `pyproject.toml` (`readability-lxml>=0.8.1`, `selectolax>=0.3.21`).
- **Crawl4AI upstream MCP integration documented** in `docs/mcp/mcp-servers.md`. Ralph already supports upstream MCP servers — the new section documents how to run Crawl4AI locally and wire it in for multi-page / JavaScript-rendered crawling. No new code; configuration only.
- **`ralph.agents.execution_state`** — provides `AgentExecutionState` (ACTIVE / WAITING_ON_CHILD / RESUMABLE_CONTINUE / TERMINAL_COMPLETE / FAILED) and per-transport `ExecutionStrategy` classes (`GenericExecutionStrategy`, `OpenCodeExecutionStrategy`) so liveness and completion semantics are isolated behind a transport-aware boundary.
- **`ralph.agents.completion_signals`** — provides `CompletionSignals` dataclass and `evaluate_completion(workspace, phase)` to determine whether an agent run produced the required phase artifact, making artifact submission the primary OpenCode success criterion.
- **`ralph.process.liveness`** — provides the `LivenessProbe` protocol, `DefaultLivenessProbe` (queries `ProcessManager` for active labels), and `FakeLivenessProbe` (injectable test fake) so unit tests can exercise multi-agent tree liveness logic without real subprocesses.
- **`OpenCodeResumableExitError`** in `ralph.agents.invoke` — raised when OpenCode exits with code 0 without producing the required phase artifact, allowing the runner to continue the same session instead of restarting from scratch.
- display: colorize LEVEL and CAT badges on TTY; plain text preserved under NO_COLOR/non-TTY.
- display: emit one-line legend inside the [run-start] block describing LEVEL/CAT/[tag] format.
- logging: register SUCCESS (25) and MILESTONE (35) loguru levels aligned with the transcript vocabulary.
- display: print the Rich completion panel at pipeline stop alongside the existing [run-end] lines.
- Surface agent output as kind-tagged `content`/`thinking`/`tool`/`tool-result` lines distinct from workflow metadata lines; oversized content is condensed with a pointer to `.agent/raw/<unit>.log`. Only condensed content and malformed parser input are written to that file — short, non-condensed output is not preserved there.
- `ralph.display.content_condenser` — predictable head+tail condensation for oversized content lines.
- `ralph.display.raw_overflow` — per-unit raw NDJSON overflow log writer.
- `ralph.display.completion_summary` — end-of-run panel renderer reused by the runner for both dashboard and lines modes.
- `ralph.display.panels.analysis` and `ralph.display.panels.decision_log` — new first-class dashboard regions.
- `ralph.display.artifact_reader` — tolerant readers for `plan.json` and `*_analysis_decision.json` used by the dashboard subscriber.
- `ParallelDisplay.emit_phase_transition` / `emit_analysis_result` — helpers that route transitions and decisions through both the live dashboard and the subscriber's decision log.
- `LiveDashboard.print_above` — serialised helper for printing banners above the live region without fighting the render thread.
- Strict startup validation for custom MCP servers configured via `mcp.toml`. Ralph now completes the standard `initialize` → `notifications/initialized` → `tools/list` handshake against every upstream server before phase 1, and after the upstream check it probes the Claude/Codex/OpenCode wiring it would emit against the same server. Failures abort the pipeline with exit code 1 and surface a redacted error (env var values are never logged). Set `RALPH_MCP_STRICT=0` to fall back to the legacy warn-and-skip behaviour for CI smoke runs.
- `ralph --diagnose` renders a `Custom MCP Servers` table (per-server status with tool counts) and an `Agent Transport Compatibility` table (Claude/Codex/OpenCode reachability per server).
- `ralph --check-mcp` runs the custom MCP startup validation and agent transport probe without starting the pipeline, returning exit code 0/1.
- **Streaming block grouping.** Consecutive `text` or `thinking` activity lines from the same worker are now grouped into `[content-start]`/`[content-continue#N]`/`[content-end]` (or `[thinking-start]`/`[thinking-continue#N]`/`[thinking-end]`) sequences. The `*-end` line carries a fragment count, total char length, and one-line headline summary of the accumulated block. `PlainLogRenderer.flush_blocks()` closes open blocks on phase transitions and pipeline stop.
- **`ralph.display.long_content_summary`** — default-on headline extractor for oversized content. When content exceeds 4000 display cells, a `↳ summary:` line with the first sentence of the content (max 200 chars) is prepended before the condensed excerpt. Set `RALPH_LONG_CONTENT_SUMMARY=0` to disable. No external AI call — the upstream provider already produced the text. See README "Long-content display" section.
- **`RingBuffer.consume_drop_delta()`** — thread-safe method that returns and atomically zeroes the dropped-item counter since the last call. `ParallelDisplay` drains this after each activity emission and emits a debounced `WARN META [progress][<unit>] dropped N lines since last flush` line when drops occur (at most one warning per unit per second).
- **`render_missing_plan_hint`** in `ralph.display.artifact_renderer` — emits an `INFO META [plan] (no plan artifact on disk)` line when planning completes without a `plan.json`, so the log always contains a plan entry.
- **Raw-overflow diagnostic for malformed parser input.** `ActivityRouter` now accepts an optional `raw_overflow_callback` that is invoked when a parser raises. `ParallelDisplay` wires this to write the offending raw line to `.agent/raw/<unit>.log` before the ERROR event is emitted, so malformed input is preserved for diagnosis.
- **Empty-state placeholders** for plan and activity sections. When no plan has been loaded yet the log emits `INFO META [plan] (no plan loaded yet)` once; when no agent is active it emits `INFO META [activity] (no active agent yet)` once, so the log never looks broken at startup.
- **`theme.log.*` styles** in `RALPH_THEME` — `theme.log.info`, `theme.log.success`, `theme.log.warn`, `theme.log.error`, and `theme.log.milestone` give Rich TTY output visually distinct level badges while plain ANSI-free output continues to emit literal level strings unchanged.
- **`show_phase_start_from_state(state, phase)`** in `ralph.display.phase_banner` and re-exported from `ralph.display` — convenience helper that extracts iteration, reviewer pass, and analysis iteration counters from any state-like object and delegates to `show_phase_start`.
- **`[run-start]` orientation line** emitted once at pipeline start (MILESTONE header plus INFO continuation lines for prompt path, developer/reviewer agent+model, iterations, parallel worker budget, plan presence, workspace root). Suppressed by `--quiet`.
- **`[phase-close]` now reports timing and activity counters.** Each phase-close line includes elapsed=<N>s and the count of content/thinking/tool_call/error events emitted during that phase (e.g. `plan: 5 step(s), 2 risk(s)`; `review: 3 issue(s)`). Suppressed by `--quiet`.

- **`[run-end]` MILESTONE block** emitted once at pipeline stop with total elapsed and aggregate counters (content_blocks, thinking_blocks, tool_calls, errors, agent_calls). Suppressed by `--quiet`.
- **Same-content streaming fragment suppression (default-on).** Identical consecutive text/thinking deltas no longer emit duplicate `[content-continue#N]` lines. Set `RALPH_STREAMING_DEDUP=0` to disable.

### Migration
- Users relying on the previously silent default behavior should now pass `--quiet` (or `-q`) explicitly.
- Users currently passing `--verbosity normal` will see verbose output; they will need to switch to `--quiet` to restore the minimal variant.
- Log parsers that assumed a fixed level set `{INFO,WARN,ERROR,SUCCESS}` must now also accept `MILESTONE` on phase-transition rows.
- Log parsers that matched `[content-continue]` or `[thinking-continue]` exactly must update to match `[content-continue#N]` / `[thinking-continue#N]` (where N is a 1-based integer).
- Tooling that expected dropped-buffer notifications at `INFO` level must update to `WARN`.

### Fixed
- **Policy validation now rejects pipelines that declare a tracked budget counter without any matching `post_commit_routes` entry.** Previously, a commit-role phase with `tracks_budget = true` but no matching route would silently fall through to `on_success` instead of raising an error. `validate_policy_completeness` now fails closed with: `phases.<phase>: role='commit' tracks budget counter '<counter>' but no post_commit_routes apply to this phase.`
- **OpenCode false-positive retry bug.** When OpenCode foreground exits rc=0 and (a) child agents are still running OR (b) no children are visible at the exact moment of exit but background work is still pending, `_check_process_result` now waits before declaring resumable failure. Two windows govern the wait: a mandatory `parent_exit_grace_seconds` (default 5s) that polls for late-appearing completion artifacts, `explicit_complete` markers, or newly-registered child agents — covering the race where MCP-driven background subagents have been launched but not yet registered with the ProcessManager — and the existing `descendant_wait_timeout_seconds` (default 30s) that engages once children are visible. Previously, when the foreground exited and no children were visible at that exact instant (a common case for late-spawning MCP subagents and async background work), `OpenCodeResumableExitError` was raised within ~1ms, killing the OpenCode parent before background work could complete. The new grace window eliminates this false positive while preserving the fast path (`TERMINAL_COMPLETE` when completion signals are present at exit time).
- `-D` / `--developer-iters` and `-R` / `--reviewer-reviews` CLI flags now correctly control the number of dev/review cycles. Previously, the pipeline ran exactly one dev cycle and one review pass regardless of the flag values. The `developer_iters` setting controls dev cycles; `reviewer_reviews` controls review cycles.
- `-R=0` now skips the review phase entirely instead of running one forced review pass.
- Analysis loopbacks from `development_analysis` no longer consume a development budget slot. Loopbacks are retries of the same iteration, not new iterations.
- `make typecheck` now runs mypy through `uv run python -m mypy` so it uses the project's virtualenv instead of a system-wide mypy that cannot see project dependencies.

### Removed
- `commit_sha` field removed from `WorkerCompletedEvent`, `WorkerState`, and `WorkerSnapshot`. This was a worktree-era relic always set to empty string in same-workspace v1 mode. Checkpoints from earlier versions load cleanly due to `extra="ignore"` on `WorkerState`.
- `max_dev_continuations` config field (previously declared but never used).
- `_legacy_handle_agent_success` reducer path (unreachable in production — policy is always loaded).
- Unused `developer_iters_option` / `reviewer_reviews_option` decorator stubs in `cli/options.py`.

### Migration Notes
- **Users resuming from a checkpoint created by a prior buggy version**: the stored `development_budget_remaining` and `review_budget_remaining` fields will be `0`, causing the fix to silently repeat the old broken behavior. To get the fix, delete the checkpoint before resuming:
  ```bash
  rm .agent/checkpoint.json
  ```
  Then re-run `ralph -D N -R M` from scratch. Affected users already had a broken run (dev ran once), so this is equivalent to restarting the intended work.
