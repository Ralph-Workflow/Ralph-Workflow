# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **fix(effect_executor): make retry prompt action-aware so resume never inlines the original task** — closes the restart-from-scratch wedge when the idle watchdog fires (`CHILDREN_PERSIST_TOO_LONG`) on a planning-heavy agent. `_write_agent_retry_prompt` now takes a `recovery_action` keyword; `resume` and `new_session_with_id` reference the original prompt by path only and append a `CONTINUE FROM WHERE YOU LEFT OFF` directive; `fresh` keeps the prior inlined-body behavior for new-session retries. `build_agent_recovery_plan` is reordered so `session_id` and `recovery_action` are computed before the prompt-construction call, plumbed through `AgentRecoveryPlan.recovery_action` (the new field on the dataclass) and threaded through `_retry_prompt_file_for_context` to the prompt constructor. The new field defaults to `None` so existing positional construction keeps working. The plumbing routes through `should_reset_tool_registry` and `recovery_action_for_failure_reason` (both already on the canonical recovery-decision seam in `ralph/recovery/failure_classifier.py` and `ralph/agents/invoke/_session_resume.py`), so the 5-actual-sites / 8-file allowlist pin in `tests/test_no_anti_drift_recovery_invariants.py` is preserved.

### Added

- **feat(pipeline): add structural restart-from-scratch fingerprint** so the zero-progress guard catches literal-text-variation restart spirals across retries. New `ralph/pipeline/_restart_from_scratch.py` defines a frozenset of opening-narrative regexes (with an import-time invariant guard: non-empty, type-is-frozenset, `python -O` immune). `retry_failure_signature` uses a structural-dominant composition: when a restart pattern is detected, the structural fingerprint is the returned signature (collapsing path tokens the literal fingerprint cannot); otherwise the literal fingerprint is the fallback so existing tests that assert signature equality across non-narrative pairs still pass. The new module is private (leading underscore) and excluded from the sphinx modules coverage test, and is forward-pinned by the property matrix L row.

- **Session timing budget resets on every attempt boundary.** `McpServer.reset_session_budget()` re-arms the 50-minute soft wrap-up nag (and the 55-minute hard `MAX_SESSION_SECONDS` ceiling) at the start of each attempt, so an agent told to retry after a missing-artifact failure starts with a fresh budget instead of inheriting the prior attempt's elapsed time. `RestartAwareMcpBridge` exposes the same reset hook via a custom JSON-RPC method (`notifications/reset_wrapup`) and the orchestrator wires it at the top of every `effect_executor._run_attempt` boundary. The 60-minute timing budget is now strictly per-invocation (one attempt == one fresh budget). A new bounded HTTP timeout constant (`_RESET_WRAPUP_TIMEOUT_S = 2.0s`, enforced via `if`/`raise RuntimeError` so it survives `python -O`) keeps `audit_mcp_timeout` green. Five new black-box tests cover the in-process McpServer reset, the wire-level JSON-RPC dispatch, the bridge-level HTTP reset against a real `_FallbackStandaloneServer`, the `effect_executor` attempt-boundary re-arm, and the no-op when no provider is configured. The `_session_wrapup` module docstring and `McpServer` class docstring document the per-invocation contract.
- **No-auto-update-policy for user-global baseline skills.** `SkillManager.check_skills_for_updates()` is now surface-only: it records the update signal in capability state (`update_available=True`) but no longer auto-repairs `~/.claude/skills/` or any sibling symlink on a normal `ralph` run. `_sync_shipped_skills_on_pipeline_run` now surfaces a `ralph --force-init-skills` hint on the same non-DEBUG channel used for project-scope conflicts. Only an explicit `ralph --force-init-skills` (or `ralph --init`) invocation overwrites the user-global canonical or sibling symlinks.
- **`ralph --init` now auto-symlinks the baseline skill bundle** into Codex (`~/.codex/skills/`), OpenCode (`~/.config/opencode/skills/`), and AGY (`~/.gemini/antigravity-cli/skills/`) roots in addition to Claude Code. The bundled `.gitignore` now also covers Python, Node, editor, and OS artifacts (`__pycache__/`, `node_modules/`, `.idea/`, `.DS_Store`, etc.).
- **Plan Artifact Optimizations for AI Coding (cheap-model-friendly + executor-strict).** The plan artifact gains two cheap-model shortcut fields on `Summary`: `intent` (≤200-char one-line user-facing outcome, defaults to empty string and dropped from `model_dump(exclude_defaults=True)`, mirroring `context`) and `intent_verb` (closed enum of `add`|`fix`|`refactor`|`migrate`|`document`|`investigate`|`improve`|`configure`|`remove`, stored as `str | None` default `None`, normalized via a before-validator that runs because `Summary` sets `validate_default=True`, lowercases the value before the closed-set check, and rejects explicit `""` with `ValueError("intent_verb must not be empty")`). The rendered plan surfaces the intent as a new `## Intent` heading before the existing `## Summary` block. `PlanStep` gains three optional fields: `satisfies` (list of AC ids matching `^[A-Z]+-\d{2,}$`), `expected_evidence` (per-step completion proof list, deduped case-insensitively, blank entries dropped, last-wins for case collision), and `verify_command` (shell command for verify steps). `AcceptanceCriterion` gains `satisfied_by_steps` (list of positive step numbers, deduped). A new `_validate_step_ac_cross_references` model validator on `PlanArtifact` enforces the 2-way step↔AC link, rejecting orphan satisfies ids, AC references to unknown step numbers, and `satisfies` without a populated `design.acceptance_criteria`. The per-step `_validate_step_type_contract` model validator tightens the step contract: every `file_change` step must declare at least one `targets` entry, and every `verify` step must declare `verify_command` OR `location`. `DesignSection` gains an optional `outcome` (≤500 chars, distinct from `notes`) that renders as a `### Outcome` sub-block at the TOP of the Design section. The new `_remap_ac_step_refs` helper runs at all three call sites of `_reindex_plan_steps` (insert/replace/remove) so `AC.satisfied_by_steps` stays in lockstep with the new step numbering. The bundled `plan.md` format doc has a new `## Tightened step contract` section, expanded Common mistakes and Dumb-proof checklist, and a refreshed Complete example that demonstrates the new fields. The planner template `planning.jinja` has new `## INTENT & INTENT_VERB (cheap-model shortcut)`, `## STEP CONTRACT`, and `## STEP ↔ ACCEPTANCE-CRITERIA LINKING` blocks plus an updated JSON example. `format_plan_for_execution` mirrors the Markdown renderer's new intent and outcome surfaces. ~25 new unit tests cover intent/verb validation, the new contract enforcement, the cross-section validator, the AC-remap helper at all three call sites, the format doc updates, and the renderer changes; full coverage under `make verify`.
- **Plan Artifact Improvements.** The plan artifact now carries an optional `design` sub-section with seven typed SE-opinionated sub-models (Design Constraints, Non-Goals, Dependency Injection, Drift Detection, Testability, Refactor Strategy, Acceptance Criteria) for stronger Software Engineering opinions; registered a bundled `plan.md` format doc and updated the sphinx artifacts doc and the artifact formats index to surface the new format doc alongside the existing per-type docs; updated the planner jinja prompts (`planning.jinja` and `planning_fallback.jinja`) with a `## DESIGN SECTION` block and DESIGN_SECTION_HINTS substring tests; the new sub-section round-trips through `normalize_plan_artifact_content` and `validate_plan_section`, renders in 9-section order in `render_plan_markdown`, and surfaces as a `Design` block between Risks and Verification in `format_plan_for_execution`; 18 new unit tests cover normalize round-trip, partial sub-sections, sub-model rejection, render ordering, and format ordering; full coverage under `make verify`.
- **Google Anti Gravity (AGY) is now a first-class supported agent path.** Ralph Workflow officially supports running Google Anti Gravity alongside Claude, Codex, and OpenCode under the same unattended orchestration contract. AGY sessions require explicit completion evidence (`declare_complete` or a phase artifact), receive MCP tooling through Ralph's capability-gated model, have upstream MCP servers (from `~/.gemini/antigravity-cli/mcp_config.json` or workspace `.agents/mcp_config.json`) normalized and proxied via Ralph's upstream layer, and have multimodal delivery resolved through the Gemini provider profile. Ralph Workflow now automatically injects the run-scoped Ralph MCP endpoint into the workspace-level `.agents/mcp_config.json` before each AGY run using `agy_workspace_mcp_endpoint`, eliminating the prior manual pre-configuration requirement. The provider-visible config contains only the Ralph MCP endpoint (matching Ralph's strict-authority-mode contract); upstream servers are still discovered and re-exposed through Ralph's upstream proxy. AGY command flag ordering is also fixed: `--dangerously-skip-permissions` and other flags now correctly precede `--print <prompt>`. The non-functional `session_flag` (`--conversation {}`) has been removed from the default AGY builtin config because AGY headless mode never outputs conversation IDs (known upstream limitation). Use `ralph --check-mcp` to verify AGY transport compatibility before a run.
- **Dedicated `waiting_status_line` field on `PipelineSnapshot`.** Child-wait status updates
  are now routed to a separate `waiting_status_line` field instead of overwriting
  `last_activity_line`, so agent tool/content activity and waiting status can be displayed
  independently without clobbering each other.
- **Kind-specific `[waiting]` rendering in `PlainLogRenderer`.** Waiting status lines now
  render with a distinct `[waiting]` tag and level: `INFO` for ENTERED/PROGRESS/EXITED,
  `WARN` for SUSPECTED_FROZEN, and `ERROR` for HARD_STOP. The renderer deduplicates
  consecutive identical waiting lines.
- **CHILDREN_PERSIST_TOO_LONG diagnostic in completion summary.** When the pipeline ends
  with a long-child-wait timeout, the completion summary now appends a parsed `Reason:`
  line with `cumulative`, `scoped_child_active`, `oldest_child_seconds`,
  `workspace_event_delta`, and `evidence` fields extracted from the error string.
  The original error text remains unchanged; the reason line is purely additive.
- **`_dispatch_waiting_event` free function for testable subscriber seam.** Extracted
  from the runner's closure so tests can inject a fake subscriber without a full pipeline.
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

### Removed
- **Cloud reporting infrastructure removed.** The old cloud reporting package, API shim, config model, and TOML section have been removed in favour of the new `ralph.supervising` trackable instance model.

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
- Strict startup validation for custom MCP servers configured via `mcp.toml`. Ralph now completes the standard `initialize` → `notifications/initialized` → `tools/list` handshake against every upstream server before phase 1, and after the upstream check it probes the Claude/Codex/OpenCode/AGY wiring it would emit against the same server. Failures abort the pipeline with exit code 1 and surface a redacted error (env var values are never logged). Set `RALPH_MCP_STRICT=0` to fall back to the legacy warn-and-skip behaviour for CI smoke runs.
- `ralph --diagnose` renders a `Custom MCP Servers` table (per-server status with tool counts) and an `Agent Transport Compatibility` table (Claude/Codex/OpenCode/AGY reachability per server).
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
- **OS-descendant-only child evidence now fires at the no-progress waiting ceiling.** When an OpenCode agent enters `WAITING_ON_CHILD` with only OS-level process descendants (no fresh scoped label or registry evidence, `alive_by=os_descendant_only_stale_progress`), the idle watchdog now fires at `max_waiting_on_child_no_progress_seconds` (default 600s) instead of the full `max_waiting_on_child_seconds` ceiling (default 1800s). Previously, `SUSPECTED_FROZEN` would fire correctly at 600s but the watchdog would continue waiting until 1800s, keeping a stuck agent alive 20 minutes longer than intended. The effective ceiling is also surfaced correctly in `PROGRESS` and `HARD_STOP` waiting events so log monitors see the true bound.
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
