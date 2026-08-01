# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## How to add an entry

> One line per change, under exactly one of the group headers below.
> Link the commit SHA. Name the test that locks the behavior. Keep
> multi-paragraph prose out of the changelog — link to the commit, the
> PR, or the docs page that carries the full story. New work goes under
> `[Unreleased]`; a release renames that section to the version and
> opens a fresh empty `[Unreleased]`.

### Conventions

- **Subject prefix**: `feat(...)`, `fix(...)`, `refactor(...)`,
  `docs(...)`, `chore(...)`, `test(...)` — matching the
  `ralph --generate-commit` subject style.
- **One line per change**: the description, the commit SHA, and the
  test file that pins it. Multi-paragraph prose is pruned to a
  one-line summary; deeper context lives in the commit message and the
  docs.
- **Group headers**: `### Added`, `### Changed`, `### Fixed`,
  `### Removed`, `### Documentation`, `### Migration` (Keep a
  Changelog). Each group appears **at most once** per version section —
  add to the existing header, never open a second one.
- **Test reference**: name the test module / file (e.g.
  `tests/test_verify_invariants.py`) so a reader can jump from the
  changelog to the regression that locks the behavior.

### Versioning anchors

Two upstream tags predate this file: `v0.8.8` (2026-05-28) and
`ralph-workflow-v0.8.8` (2026-05-29). The changelog was started after
them, so there is no per-change record to backfill `## [0.8.x]`
sections from, and inventing entries for those tags would violate the
AGENTS.md fabrication guard. Everything recorded below shipped in
`0.9.0`. Version headings carry no compare links because no `v0.9.0`
tag exists yet — a link to one would be a dead link.

## [Unreleased]

### Fixed

- **fix(opencode): use the currently published `minimax/MiniMax-M3` provider alias for the interactive smoke default** — the smoke command now reaches the live MiniMax route while retaining configurable provider/model aliases. Locked by `tests/test_opencode_defaults.py`.
- **fix(config): keep project-local configuration explicit opt-in** — `ralph --regenerate-config` now rewrites global configuration and refreshes only local TOMLs already present; missing `.agent/` files remain absent and are created only by `ralph --init-local-config` / `--generate-local-config`. Locked by `tests/test_config_bootstrap.py` and `tests/test_cli_commands_2.py`.

### Changed

- **feat(display): redesign production console presentation** — activity rows now use fixed-RGB semantic carriers, previews own complete surfaces, and cell-width layout keeps live and redirected output readable across the supported display matrix ([`a7e0f85c9`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/a7e0f85c9), [`4077a8d3f`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/4077a8d3f), [`09e7cca94`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/09e7cca94)); locked by `tests/test_display_generated_scenes.py`, `tests/test_display_visual_floor.py`, and `tests/test_plain_renderer_level_styling.py`.
- **feat(install): rework dev build into a self-contained snapshot** — `make install` / `make dev` copy the checkout to `~/.local/share/ralph-workflow-dev/current`, launch `rdev` against the copy, and preflight existing global `ralph` installs with continue/remove/abort before writing anything ([`75151e75f`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/75151e75f), [`0adcd32b4`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/0adcd32b4)); locked by `tests/test_install.py` and `tests/test_install_conflicts.py`.
- **feat(install): surface build-flavored versions and manual-wheel install mode** — `--version` reports `-dev` for a dev build, `-build` for a `--from <wheel>` install, and plain for a package-manager install; `make stable` supports `--from` for a hand-built wheel ([`bc5d28ff0`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/bc5d28ff0), [`61594df49`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/61594df49), [`9866492d1`](https://github.com/Ralph-Workflow/Ralph-Workflow/commit/9866492d1)); locked by `tests/test_install.py`.

### Removed

- **refactor(cli)!: remove inline prompt input** — `--prompt`/`-P` and quick positional prompt injection are gone; runs use workspace `PROMPT.md`, and the ordinary preflight/readiness path always applies. Locked by `tests/test_cli_quick_mode_semantics.py` and `tests/project_policy/test_run_integration.py`.

## [0.9.7] - 2026-07-29

Patch release. `__version__` moved from `0.9.6` to `0.9.7` in
`ralph/__init__.py` (the canonical version source `pyproject.toml`
reads via `[tool.hatch.version]`), with `skills-package/package.json`
kept in parity by `tests/test_skills_package_version_parity.py`.
Carries the resource-lifecycle, retention, and display fixes below.

### Fixed

- **fix(display): keep watchdog stall attention live during conflict resolution and policy remediation** — pushed footer models leave `attention` to operator state so Live ticks mirror the watchdog assessment. Locked by `tests/pipeline/test_conflict_status_bar_stall_source.py` and `tests/project_policy/test_remediation_status_bar_stall_source.py`.
- **fix(display): clear `STALLED` when its watchdog invocation ends** — the status slot now mirrors every watchdog event's authoritative stall assessment. Locked by `tests/agents/idle_watchdog/test_stall_lifetime.py` and `tests/display/test_subscriber_stall_mirror.py`.
- **fix(agents): bound Claude text accumulators and partial transcript/PTY tails** — preserves healthy streams while retaining only bounded tails. Locked by `tests/test_bounded_text_buffer.py`, `tests/test_text_accumulator_bound.py`, `tests/test_claude_interactive_transcript_buffer_bound.py`, and `tests/test_pty_line_reader_pending_bound.py`.
- **fix(mcp): cap saturated dispatch admission and use a 305.0s caller deadline** — requests beyond `max_workers` in-flight calls return saturation without queueing or invocation; the named deadline is above the 300s tool clamp and, with drain plus kill, below the 330s client timeout. Locked by `tests/test_mcp_saturated_dispatch_bounded_result.py`.
- **fix(mcp): unlink failed exec spill files** — write errors no longer orphan mkstemp output. Locked by `tests/test_tool_exec_large_output_spill.py`.
- **fix(mcp): unlink standalone session metadata when spawn fails** — failed bridge startup leaves no session JSON behind. Locked by `tests/test_mcp_lifecycle_session_file_cleanup.py`.
- **fix(diagnostics): route mdutil through ProcessManager** — filesystem probes now share tracked child teardown. Locked by `tests/test_fs_health_process_manager.py`.
- **fix(codex): cap outstanding home atexit tracking** — registry eviction never deletes active directories. Locked by `tests/integration/test_codex_home_cap_eviction.py`.
- **fix(retention): reclaim aged crashed-run Codex homes and MCP session files at run start** — best-effort seven-day sweeps remove only stale machine bookkeeping, including symlink-mirrored Codex homes. Locked by `tests/unit/test_agent_dir_retention.py`.
- **fix(artifacts): skip redundant RunStateDB schema initialization** — existing databases use the schema version guard. Locked by `tests/test_state_db_schema_guard.py`.
- **fix(audits): detect unbounded Future results and direct child spawns** — lifecycle regressions now fail verification. Locked by `tests/test_audit_mcp_timeout.py` and `tests/test_audit_resource_lifecycle.py`.

### Documentation

- **docs(lifecycle): document expanded timeout and resource-lifecycle audit roots plus static tuple-key dispatch and marker contracts** — lifecycle guidance now names every default root enforced by verification. Locked by `tests/test_audit_mcp_timeout.py` and `tests/test_audit_resource_lifecycle.py`.
- **docs(lifecycle): record bounded text, dispatch, file, child-process, Codex-home, and schema-init fixes with Future-result and direct-child-spawn audit contracts** — lifecycle guidance now states the shipped caps, caller-release limitation, and explicit upstream-pooling non-goal. Locked by `tests/test_audit_mcp_timeout.py` and `tests/test_audit_resource_lifecycle.py`.

## [0.9.6] - 2026-07-29

Patch release. `__version__` moved from `0.9.5` to `0.9.6` in
`ralph/__init__.py` (the canonical version source `pyproject.toml`
reads via `[tool.hatch.version]`), with `skills-package/package.json`
kept in parity by `tests/test_skills_package_version_parity.py`.
Carries the process-spawn and display fixes below.

### Changed

- **fix(process): strip inherited macOS malloc stack-logging toggles at every spawn-capable entry point** — descendants no longer inherit `MallocStackLogging` noise or stack-logging overhead, while explicit child debug environments remain intact. Locked by `tests/process/test_spawn_env_sanitizer.py` and `tests/process/test_spawn_env_containment.py`.

- **test(display): replay parser-native captures through the shared presentation path** — Claude, Claude Headless, Claude Interactive, Codex, OpenCode, Pi, Cursor, AGY, Nanocoder, Gemini, and generic/malformed fallback captures now pass through parser, normalizer, live display, and rendered record together. Locked by `tests/display/test_universality_replay.py`.

- **fix(opencode): render bare write/edit/append file tools through the shared preview contract** — OpenCode file activity now joins the existing syntax-highlighted and themed preview path without a transport-specific renderer. OpenCode 1.17.x still emits whole text events rather than stream deltas, and subagent work still arrives as `task` calls rather than `child_*` lifecycle events; both are supported current-runtime behaviors. Locked by `tests/display/test_opencode_preview_parity.py` and `tests/display/test_agent_parity_matrix.py`.

- **fix(display): collapse duplicate pi tool-call aliases into one presentation entry** — parser aliases sharing a tool-call id now emit one shared call entry. Commit `3c7d9ef79`. Locked by `tests/display/test_record_surface_residuals.py` and `tests/display/test_real_corpus_regression.py`.

- **fix(display): route successful tool results through the shared preview seam** — successful result previews use the same accessible syntax and contrast path as tool calls; obsolete result-only syntax code was removed. Commit `854eec941`. Locked by `tests/display/test_edit_preview.py`, `tests/display/test_preview_contrast_sweep.py`, and `tests/display/test_syntax_palette_accessibility.py`.

- **fix(display): group rendered-record entries beneath readable phase headers** — event rows now omit repeated phase, cycle, identity, and healthy severity while retaining timestamps, hierarchy, role markers, and explicit failures. Locked by `tests/display/test_record_writer.py`, `tests/display/test_record_surface_residuals.py`, and `tests/display/test_raw_record_regression.py`.

- **fix(display): lock production-path tool-result flood handling and per-agent rendered-record parity** — 120 distinct tool results with progress companions remain distinguishable without progress echoes or contradictory running text; codex, cursor, agy, nanocoder, generic, malformed, claude, and pi fixture streams now exercise both display surfaces. Locked by `tests/display/test_raw_record_regression.py` and `tests/display/test_agent_parity_matrix.py`.

- **fix(display): make tool outcomes self-describing across both surfaces** — tool-result and error bodies no longer restate agent identity, previews remain on the call side, and the shared failure predicate keeps durable records aligned with the live carrier. Locked by `tests/display/test_raw_record_regression.py` and `tests/display/test_agent_event_renderer.py`.

- **refactor(display): guard command output through the shared display surface** — check-policy, commit, diagnose, explain, init, run, and conflict resolution join the existing cleanup, star, contribute, and smoke folds; smoke `EXIT_CODE=N` remains the sole machine-readable exception. Locked by `tests/display/test_parallel_display_drift_prevention.py` and `scripts/wt028-drift-check.sh`.

### Documentation

- **docs(mcp): align the plan-spec contract with PLAN001-only blocking** — the public module docstring now says parsed consumer anchors are best-effort advisory diagnostics, matching the implemented severity policy. Locked by `tests/mcp/test_md_plan_advisory.py` and `tests/mcp/test_md_plan_not_a_plan.py`.

## [0.9.5] - 2026-07-28

Patch release. `__version__` moved from `0.9.4` to `0.9.5` in
`ralph/__init__.py` (the canonical version source `pyproject.toml`
reads via `[tool.hatch.version]`), with `skills-package/package.json`
kept in parity by `tests/test_skills_package_version_parity.py`.
Carries the watchdog and Status Bar fixes below.

### Fixed

- **fix(display): remove the dead Status Bar attention timestamp and document fan-out semantics** — `STALLED` remains exclusively watchdog-sourced; the renderer no longer accepts an unused timestamp, and the shared fan-out slot is explicitly last-writer-wins until concurrent per-unit stalls need separate operator visibility. Locked by `tests/display/test_status_bar_live_activity_anchor.py` and `tests/unit/display/test_parallel_display_t22.py`.

- **fix(display): make the Status Bar `STALLED` slot a pure watchdog transition stream** — the subscriber no longer derives or clears watchdog attention from `SUSPECTED_FROZEN`, `HARD_STOP`, or `EXITED`; only `STALLED` sets it and `STALL_RESUMED` clears it. Locked by `tests/display/test_subscriber.py` and `tests/agents/idle_watchdog/test_stall_status_events.py`.

- **fix(opencode): OpenCode tool calls fingerprint per tool, so the watchdog stops killing healthy agents** — `_resolve_tool_name_and_args` read only the top level, but OpenCode carries the tool name and arguments inside `part`, so every call fingerprinted as `("unknown", {})` and N calls to N *different* tools tripped `REPEATED_IDENTICAL_TOOL_CALL`. Replaying the captured raw logs: 8 of 19 runs tripped before, 0 after (1 distinct fingerprint before, 279 after). Also: an errored tool now classifies as `ERROR_LINE` (so `REPEATED_ERROR_LOOP` is reachable on this transport) and keeps its dispatch visible, `tool_result` no longer double-feeds the breaker, the step id is read from `part.id`, integer epoch timestamps are honored, and OpenCode API errors report `error.data.message` instead of `"APIError"`. Commit `5793de043`. Locked by `tests/agents/idle_watchdog/test_opencode_tool_call_fingerprints.py`, `tests/agents/parsers/test_opencode_uses_ndjson_base.py`, and `tests/agents/parsers/test_parse_error_extraction.py`.

- **fix(watchdog): close the remaining tool-call false-kill paths** — the default `claude` agent (`claude_interactive`) fed the watchdog the argless `claude tool: <name>` marker, so ten different `Bash` commands fingerprinted alike and fired at the seventh call; the window rule counted 8 repeats anywhere in 600s regardless of surrounding work, killing ordinary habits (one captured healthy pi run repeats `ralph_get_plan_draft` 14 times among 124 calls); and pi counted every call twice via `toolcall_end`. Also reverts two regressions from `5793de043` — an errored opencode tool is a TOOL_USE again (as ERROR_LINE a repeated failing command was invisible in both dimensions), and the opencode step id is read from the top level again. Replay of every captured run: 0 false trips on opencode (0/19), pi (0/19), and claude (0/7). Commit `50639824f`. Locked by `tests/agents/idle_watchdog/test_claude_interactive_tool_fingerprints.py`, `tests/agents/idle_watchdog/test_repetition_window_dominance.py`, and `tests/agents/idle_watchdog/test_opencode_tool_call_fingerprints.py`.

- **fix(claude): fingerprint tool calls from the complete assistant message** — Claude opens a tool call with `"input": {}` and streams the arguments afterwards, and that placeholder was the only shape reaching the breaker, so every call of one tool keyed to `<name>|{}`. The extractor now reads the nested `message.content[]` block (which carries the real input) and skips the placeholder. Verified on a live `claude -p` capture: 1 distinct fingerprint before, 4 after. Commit `35a38f130`. Locked by `tests/agents/idle_watchdog/test_mark_tool_call_runtime_reachability.py`.

- **fix(watchdog): feed both repetition dimensions, and detect cycles by call diversity** — a failed tool call is both a call and an error, and each dimension catches a wedge the other cannot (the tool dimension collapses a repeated call whose failure text varies; the error dimension collapses a repeated failure whose args vary, e.g. an `MCP error -32001` storm). `AgentActivitySignal` grows an optional `error_message` so one line feeds both. Also: the window rule now trips only when the window holds at most 4 distinct fingerprints, so a 3-call A/B/C loop is caught while a poll threaded through varied work is not; cursor's per-call `toolCallId` is stripped from `args` (it made identical shell calls look distinct, so the breaker was unreachable there); a Claude message batching parallel calls is skipped rather than fingerprinted from its first block; OpenCode `step_start`/`step_finish` are LIFECYCLE, not OUTPUT_LINE, because a `step_finish` after every call was resetting both streaks; and `_ProcessLineReader` finally routes TOOL_RESULT to `record_tool_result_activity`. Replay of all 45 captured runs (1098 tool calls) across opencode, pi, and claude: 0 false fires. Commits `20c06b2ff`, `d8db731e9`, `671b7aacf`. Locked by `tests/agents/idle_watchdog/test_both_repetition_dimensions.py`, `test_repetition_window_cycle_detection.py`, `test_opencode_step_frames.py`, `test_cursor_tool_fingerprints.py`, and `test_tool_result_routing.py`.

## [0.9.4] - 2026-07-26

Patch release. `__version__` moved from `0.9.3` to `0.9.4` in
`ralph/__init__.py` (the canonical version source `pyproject.toml`
reads via `[tool.hatch.version]`), with `skills-package/package.json`
kept in parity by `tests/test_skills_package_version_parity.py`.
Carries the display, plan-diagnostic, and watchdog changes below.

### Changed

- **fix(display): resolve identity colors from the shared terminal background** — `DisplayContext` now selects the matching dark/light identity palette once, and both event entries and the Status Bar consume it; labels remain the non-color identity carrier. Locked by `tests/display/test_identity_color.py` and `tests/display/test_terminal_background.py`.

- **docs(display): document the responsive Status Bar and canonical activity surfaces** — the maintainer references now describe the 120/80/60/40 width ladder, 12-row behavior, watchdog-owned `STALLED` state, rendered/verbatim record distinction, generic fallback, syntax/file hierarchy, and `NO_COLOR`/ASCII fallbacks; stale private transcript vocabulary and line-number claims are removed. Locked by `tests/display/test_status_bar.py`, `tests/display/test_raw_record_regression.py`, and `tests/display/test_accessibility_matrix.py`.

- **refactor(mcp)!: `PLAN001` is the sole blocking plan diagnostic** — only empty/markup-only, under-100-character, or recognizably non-plan submissions fail. Every parser, schema, reference, and shape finding is actionable advisory, with a concrete run cost and fix; warning-only plans retain canonical content, standard artifact lifecycle support, and recorded override reasons. Locked by `tests/mcp/test_md_plan_not_a_plan.py`, `tests/mcp/test_md_plan_advisory.py`, and `tests/mcp/test_md_plan_chain_e2e.py`.

- **feat(display): Status Bar `STALLED` is now exclusively watchdog-sourced** — the watchdog is the single source of truth for the `STALLED` label. The display-side 30s gap derivation (the `_STALL_THRESHOLD_SECONDS` constant and the gap branch in `StatusBar._resolve_attention_state`) is removed; the watchdog publishes `WaitingStatusKind.STALLED` / `STALL_RESUMED` events on every transition into and out of a stall, the subscriber forwards them to the Status Bar host via a `watchdog_attention_sink`, and the host substitutes the value into the model ONLY when the pushed `attention` is `None` (a pushed `waiting` / `retrying` / `terminated` always wins). Behavior change: a healthy run with quiet stdout no longer flips to `STALLED` after 30s of display quiet, and a stalled run now flips to `STALLED` when the watchdog emits the stall-state transition (the pre-fire `SUSPECTED_FROZEN` suspect-threshold cross, the `HARD_STOP` ceiling cross, or a non-absolute `FIRE` verdict). `SUSPECTED_FROZEN` is a stall assessment (the watchdog has not yet fired) — it pairs with `STALLED` because the operator-visible signal is "the watchdog believes this is a stall", not "the watchdog has actually returned a FIRE verdict". Locked by `tests/agents/idle_watchdog/test_stall_status_events.py`, `tests/display/test_status_bar_liveness.py`, `tests/display/test_status_bar_live_activity_anchor.py`, `tests/display/test_subscriber.py`, `tests/display/test_accessibility_matrix.py`, and `tests/pipeline/test_run_loop_status_bar_wiring.py`.

### Removed

- **refactor(display): delete the display-side 30s stall derivation (`_STALL_THRESHOLD_SECONDS`, `StatusBarModel.last_activity_monotonic`, and the producer write at `ParallelDisplay._emit_activity_event`)** — zero dead code; the watchdog owns the stall label end-to-end and surfaces its state via the host's `watchdog_attention` slot. The `test_status_bar_live_activity_anchor.py` module is rewritten in place to test the new `_model_with_live_attention` host substitution (the old `_model_with_live_activity_anchor` symbol is gone). Locked by `tests/display/test_status_bar_liveness.py` (`test_stall_threshold_named_constant_removed`), `tests/pipeline/test_run_loop_status_bar_wiring.py`, and `tests/display/test_status_bar_live_activity_anchor.py`.

### Fixed

- **fix(idle_watchdog): `SESSION_CEILING_EXCEEDED` FIRE now transitions the runtime stall flag (DA-001)** — the `SESSION_CEILING_EXCEEDED` bypass path in `_gate.py:155` (the only `WatchdogFireReason` that bypasses the `StuckClassifier`) previously returned `WatchdogVerdict.FIRE` without calling `_set_stall(active=True, ...)`, so the watchdog's `STALLED` label was silent on an operator-set session-cap fire even though every other fire path transitioned the flag. The bypass now transitions the runtime flag via `_set_stall(active=True, now=now, idle_elapsed=idle_elapsed)` before returning FIRE; `_set_stall` is idempotent so a redundant call (e.g. a retry on the same tick) emits no duplicate `STALLED` event, preserving the exactly-once contract. The session-ceiling exemption language in `docs/agents/watchdog-spec.md` is updated to reflect the new behavior, and the `STALLED` trigger-sites list gains a sixth bullet naming the SESSION_CEILING bypass. Locked by `tests/agents/idle_watchdog/test_stall_status_events.py::test_fire_session_ceiling_emits_stalled_event`.

## [0.9.3] - 2026-07-25

Patch release. `__version__` moved from `0.9.2` to `0.9.3` in
`ralph/__init__.py`, with `skills-package/package.json` kept in parity by
`tests/test_skills_package_version_parity.py`. Carries the markup-parse
containment fix below.

### Fixed

- **fix(display): bracket-heavy agent output no longer crashes the activity emit path** — `Text.from_markup` rejects an unmatched closing tag (a grep pattern carrying `[/pdf /text /imageb]`) with `MarkupError`, which derives from `ConsoleError` and so slipped past the `except ValueError` guards in `_plain_constants._sanitize` and `parallel_display._strip_markup`, propagating out of `ParallelDisplay.emit_parsed_event`. Both sinks now delegate to the new `ralph/display/line_sanitizer.py::strip_markup_safe` — the single markup-parse site, with a total guard that falls back to literal text and strips terminal control on both paths. The terminal-escape containment audit gained four invariants (the choke point's guard, the two delegating sinks, and a package-wide AST scan rejecting any `from_markup` call on non-literal text outside `_MARKUP_PARSE_ALLOWLIST`), so a new sink cannot re-open the hole with its own guard. Locked by `tests/display/test_markup_parse_containment.py` and `tests/display/test_terminal_escape_containment.py`.

## [0.9.2] - 2026-07-25

Patch release. `__version__` moved from `0.9.1` to `0.9.2` in
`ralph/__init__.py` (the canonical version source `pyproject.toml`
reads via `[tool.hatch.version]`), with `skills-package/package.json`
kept in parity by `tests/test_skills_package_version_parity.py`.

### Removed

- **refactor(mcp)!: retire the plan-specific submission and per-step edit API** — the retired JSON-era tools (`ralph_submit_artifact`, `ralph_submit_plan_section`, `ralph_submit_plan_sections`, `ralph_finalize_plan`, `ralph_get_plan_draft`, `ralph_discard_plan_draft`, `ralph_validate_draft`, `ralph_edit_md_plan_step`, `ralph_patch_step`, `ralph_insert_plan_step`, `ralph_replace_plan_step`, `ralph_remove_plan_step`, `ralph_move_plan_step`) now hard-error with replacement guidance to the standard markdown artifact endpoints (`ralph_stage_md_artifact`, `ralph_get_md_draft`, `ralph_discard_md_draft`, `ralph_verify_md_artifact`, `ralph_finalize_md_artifact`, `ralph_submit_md_artifact`); there are no aliases and no dead handlers. Breaking change for any out-of-tree consumer that still calls the retired names; the in-repo skills, prompts, and tests are updated to the standard path. Locked by `tests/test_tool_bridge_retired_json_tools.py`, `tests/test_tool_bridge_tool_specs_web_search.py`, `tests/test_skill_instructions_round_trip.py`, `tests/test_internal_skills_mcp_prompts.py`, `tests/test_prompts_developer.py`, `tests/test_prompt_template_files.py`, `tests/test_prompt_materialize_1.py`, `tests/test_planning_prompt_step_tools.py`, and `tests/test_plan_artifact_schema_hardening.py`.

- **refactor(skills)!: drop the `submit-plan-step-edits` skill and resync `submit-plan-artifact`** — the per-step edit skill is gone from the packaged baseline (`ralph/skills/content/`); the installer's `_prune_removed_baseline_skills` removes the retired skill from every project's canonical and sibling roots, including broken symlinks, and the user-edit preservation contract is preserved (user-owned skills without the managed marker are never touched). The installed `submit-plan-artifact/SKILL.md` is byte-identical to the packaged source. Locked by `tests/test_skills_installer_project.py::test_install_prunes_managed_skill_removed_from_baseline` and `::test_prune_removes_broken_symlinks_to_retired_baseline_skills`.

- **refactor(mcp): demote remaining shape-level diagnostics to advisory** — PLAN010 (file_change without `Files:`), PLAN011 (verify without `Verify:`/`Location:`), and the PLAN020 concreteness sites no longer block submission; each now follows the `what; the run cost is <cost>; resolve by <fix>` convention and is overridable through `## Validation Overrides`. Locked by `tests/mcp/test_md_plan_advisory.py`, `tests/mcp/test_md_plan_relaxation.py`, and `tests/mcp/test_md_plan_chain_e2e.py`.

## [0.9.1] - 2026-07-25

Patch release. `__version__` moved from `0.9.0` to `0.9.1` in
`ralph/__init__.py` (the canonical version source `pyproject.toml`
reads via `[tool.hatch.version]`), with `skills-package/package.json`
kept in parity by `tests/test_skills_package_version_parity.py`.

### Added

- **feat(cli): `--long` / `-L` depth preset — five developer iterations** — sits between `-Q` (1) and `-T` (10) and pairs with the new default of 2; overrides an explicit `-D`, and the three presets are now mutually exclusive with one another. Locked by `tests/test_cli_long_mode_semantics.py`.

- **feat(mcp): three-severity plan diagnostics with `## Validation Overrides` ledger** — `Diagnostic.severity` now spans `error` / `warning` / `info`; tool payloads on `verify` / `submit` / `finalize` / `draft-status` carry per-severity `counts` and an `overridden` list. Pipeline-consumed anchors stay error-severity and each blocking message names the downstream consumer that cannot proceed (development_result proof in `ralph/phases/execution.py`, fan-out in `ralph/pipeline/work_units.py`, noop routing in `ralph/phases/analysis.py`, bounded-exec safety, the spec registry, the pydantic schema). Shape and meaning heuristics (PLAN010, PLAN011, PLAN020 concreteness sites) demote to advisory. Recording a reason under `## Validation Overrides` partitions a matching advisory diagnostic into the `overridden` list; an override that targets an error becomes `PLAN026` (warning) and the error still blocks; a stale override surfaces as `PLAN025` (info). Locked by `tests/mcp/test_md_plan_advisory.py`, `tests/mcp/test_md_plan_spec.py`, and `tests/mcp/test_md_plan_relaxation.py`.

- **feat(prompts): thinking-first planning prompts with shared thinking partial** — `ralph/prompts/templates/planning.jinja`, `planning_fallback.jinja`, and `planning_edit.jinja` now include the new `shared/_planning_thinking.jinja` partial that leads with how to think about the change (four framing questions, four mandatory phases with no easy-task skip of characterization, concreteness calibration, the floor-not-form document contract including the three severities and the override ledger) before the submission mechanics. The plan-quality rubric, worked plan example, step-type guidance, and prompt-scope classification sections are removed from the planning prompts so the analysis prompt owns the rubric as a single standard stated once; `planning.jinja` is now at or under 170 lines. Locked by `tests/test_planning_prompt_thinking_first.py`, `tests/test_planning_prompt_step_tools.py`, `tests/test_planning_subagent_guidance.py`, `tests/test_planning_prompt_agent_driven_section.py`, and `tests/test_prompts_skills_planning.py`.

- **feat(prompts): planning-analysis prompt reframes as external review against the request and the repository** — the analysis prompt now requires every `## What Came Up Short` entry to cite a concrete cost to this run and every `## How To Fix` entry to state the fix in the plan's own terms; `status: completed` with no findings is documented as a normal, visible outcome. Locked by `tests/test_analysis_prompt_semantics.py` and `tests/test_plumbing_markdown_prompt_contracts.py`.

- **feat(mcp): close the consumer-naming / cost-naming gap on the residue plan diagnostics** — SPEC002 (missing frontmatter), MD006 (duplicate frontmatter), SPEC010 (pydantic schema / plan-size rejection), PLAN009 (unknown field label), and the PLAN020 prose-drop warning now follow the `what; blocking because <consumer>; resolve by <fix>` (errors) and `what; the run cost is <cost>; resolve by <fix>` (advisories) convention. `_spec._missing_frontmatter_message` and `_spec._teach_duplicate_closed_frontmatter_vocabulary` rewrite shared `_spec` / `_fields` diagnostics; `_spec._spec010_message` wraps the pydantic / size rejection at the `parse_and_validate` chokepoint so all markdown artifact specs gain the consumer phrase; `_fields._malformed_field` and `_fields._consume_unlabeled` reword the advisory fall-back wording. Locked by `tests/mcp/test_md_plan_advisory.py`, `tests/mcp/test_md_closed_vocabulary_diagnostics.py`, `tests/mcp/test_md_simple_specs.py`, `tests/mcp/test_md_commit_cleanup_spec.py`, and `tests/mcp/test_md_development_result_spec.py`.

- **feat(prompts): planning prompt now leads with thinking; thinking partial gains a brevity commitment** — the `{% include 'shared/_planning_thinking.j2' %}` directive in `ralph/prompts/templates/planning.jinja` now sits above the `READ-ONLY planning task` block and the `You MUST submit your plan` line so the planner reads how to think before it reads submission mechanics. `shared/_planning_thinking.jinja` adds two brevity lines to the `## Document contract` paragraph: state each commitment once, next to the step it describes; keep the plan short enough to re-read in one pass under context pressure. Locked by `tests/test_planning_prompt_thinking_first.py`.

- **refactor(prompts): deduplicate the planning-analysis prompt and add overrides-respect paragraph** — the second `## Review checklist` section is removed; the unique content (defect-scope classification, shared defect vocabulary, PA-### entry shape, `## Critical Files` cross-section guidance, goal-to-prerequisite reasoning chain) moves next to the `## DECISION ARTIFACT` / `## What Came Up Short` / `## How To Fix` sections it governs. The detailed `### 1..9` dimension definitions move under `## PLAN QUALITY RUBRIC` so the operational review section stays short and the rubric lives in exactly one place. A new paragraph in `## REVIEW CHECKLIST` declares that a `## Validation Overrides` entry in the plan is the planner's recorded judgement: an overridden finding is settled and must not be re-raised in `## What Came Up Short` unless repository evidence proves the recorded reason false. Locked by `tests/test_analysis_prompt_semantics.py`.

- **test(mcp): end-to-end chain demonstration fixture module** — `tests/mcp/test_md_plan_chain_e2e.py` proves the in-session before/after behavior with three fixtures: HOLLOW (every conventional section, vague `Verify:`/`Expect:`) now draws cost-named PLAN020 warnings instead of being silently accepted; UNCONVENTIONAL (custom `## Checklist` heading, no Summary/Scope/Risks, concrete `Verify:`/`Expect:`) now passes silently with zero counts instead of being blocked by required-section rules; GOOD (the conventional medium plan shape) stays silent across severities. The prompt-chain assertions prove the planning prompt orders thinking before submission mechanics and the analysis prompt carries the overrides-respect sentence exactly once. Locked by `tests/mcp/test_md_plan_chain_e2e.py`.

### Changed

- **chore(config): default `developer_iters` is now `2` instead of `5`** — most tasks converge in two outer developer iterations; longer work opts up with `-L` (5) or `-T` (10). Default lives in `ralph/config/general_config.py`; the `-D` table row in `docs/sphinx/cli.md` follows.

- **docs(format): plan format doc now states the floor-not-form philosophy, documents the three severity levels and the `## Validation Overrides` ledger with an example, and rewrites the "Hard contract" list so each entry names its consumer in the same words the validator messages use** — lives at `ralph/mcp/artifacts/format_docs/plan.md`.

- **docs(format): plan format doc merges the duplicated `Descriptive labels and vocabulary are advisory` paragraph under `## Conventional syntax` into a single home** — the union of the two formerly-verbatim copies now appears once with both phrasing variants intact. Lives at `ralph/mcp/artifacts/format_docs/plan.md`; locked by `tests/test_artifact_format_docs.py`.

## [0.9.0] - 2026-07-24

Beta release. `__version__` moved from `0.8.27` to `0.9.0` in
`ralph/__init__.py` (the canonical version source `pyproject.toml`
reads via `[tool.hatch.version]`), with `skills-package/package.json`
kept in parity by `tests/test_skills_package_version_parity.py`.

### Added

- **feat(integrate): background catch-up fast-forward worker** — a bounded daemon thread (`ralph/pipeline/auto_integrate_catchup.py`) that every 30 seconds fast-forwards a clean, commit-free checkout onto the auto-integrate target via `git merge --ff-only`, so divergence never accumulates between seams and no conflict-resolution agent (or its tokens) is needed for a move git can make for free; started/stopped with the run loop and pinned by `tests/test_auto_integrate_catchup.py` and `tests/test_auto_integrate_catchup_e2e.py`.

- **feat(telemetry): attach a metadata-only agent-config snapshot, the project's policy-schema state, and the Ralph/Python versions to the Sentry session** — pinned by `tests/test_telemetry_sentry.py`. The `agent_config` context is set at the pipeline config-load chokepoint so it rides on every event including crashes; user-authored agent names, raw `cmd` strings, and flag values are dropped by `ralph/telemetry/_agent_config_payload.py`. A `model` value that is a filesystem path or a credentialed endpoint URL is rejected as `custom` rather than forwarded.

- **feat(mcp): ship indexed code-exploration substrate (Phases 0-4)** — commits `551e27517`, `1a1ba853c`, `5a576d4fa`, `fda81e2d2`, `b65ed3b89`. Deterministic SQLite+FTS5 store under `.agent/ralph-explore/` (gitignored, disposable) with stdlib `ast` Python structure extraction, Markdown heading/link extraction, and a SQLite edge-table code graph — no LLM, no embedding dependency. Adds the `ralph_index_status`, `ralph_reindex`, and `ralph_graph` MCP tools plus indexed optional arguments on `grep_files` / `search_files` / `read_file` / `read_multiple_files` / `list_directory` / `directory_tree` / `edit_file`, all gated by the shared `use_index` opt-in selector (`auto` / `never` / `always`) with a live-tool fallback, and a deterministic reindex lifecycle with a persistent `dirty_paths` mark-then-refresh queue. Locks behavior in `tests/test_explore_pipeline.py`, `tests/test_explore_store.py`, `tests/test_explore_graph.py`, `tests/test_explore_handlers.py`, `tests/test_explore_lifecycle.py`, `tests/test_explore_dirty_paths.py`, `tests/test_explore_bench_gates.py`, `tests/test_explore_tool_descriptions.py`, and `tests/test_explore_deferred_phases.py` (Phase-5 NetworkX / Kuzu / Tree-sitter / vector deferral).

- **feat(display): add persistent bottom Status Bar showing working directory, active phase, and applicable outer development / inner analysis iterations during interactive runs** — commits `0ea756476`, `2d19483d1`, `666661755`, `ac8013e0a`, `c27b2a933`. Locks behavior in `tests/display/test_status_bar.py`.

- **feat(idle-watchdog): add per-kind workspace event breakdown to `NO_OUTPUT_DEADLINE` fire diagnostic.**

- **feat(idle-watchdog): add `[general] agent_workspace_change_weights` configuration key for per-kind activity weighting.**

- **feat(idle-watchdog): make idle verdict activity-aware by considering stdout, `mcp_tool`, subagent, and workspace evidence channels.** Locks behavior in `tests/agents/test_idle_watchdog_3.py`.

- **feat(mcp): add `unsafe_mode` workflow flag to merge Ralph MCP into agent-native MCP configs instead of overwriting them.**

- **feat(pipeline): add structural restart-from-scratch fingerprint with import-time invariant guard.**

- **feat(pro_support): ship engine-side Pro integration contract implementation (`ralph.pro_support`) with heartbeat, marker watcher, snapshot, and hooks.** Locks behavior in `tests/test_pro_support_*.py`, `tests/test_run_loop_pro_integration.py`, `tests/test_orchestrator_pro_prompt_resolution.py`, and `tests/test_pro_support_contract.py`.

- **feat(mcp): reset session timing budget on every attempt boundary via `McpServer.reset_session_budget()` and bridge `notifications/reset_wrapup`.**

- **feat(skills): stop auto-updating user-global baseline skills on normal runs; only explicit `--force-init-skills` or `--init` overwrites them.**

- **feat(cli): auto-symlink baseline skill bundle into Codex, OpenCode, and AGY roots during `ralph --init`.**

- **feat(plan): add cheap-model shortcut fields and stricter plan-step validators.**

- **feat(plan): add optional `design` sub-section with typed SE sub-models.**

- **feat(agy): make AGY a first-class supported agent path with completion evidence, MCP tooling, and command flag ordering.**

- **feat(agy): add `ralph smoke-interactive-agy` canonical end-to-end verification command with dynamic `agy/<model>` resolver.**

- **feat(pi): add Pi.dev as a first-class supported agent path with parser, registry, command builder, and runtime resolver.** Locks behavior in `tests/agents/test_pi_dev_blackbox.py` and `tests/agents/parsers/test_pi_dev_wire_format_spec.py`.

- **feat(display): add dedicated `waiting_status_line` field on `PipelineSnapshot`.**

- **feat(display): render waiting status lines with kind-specific `[waiting]` tag and level.**

- **feat(display): include `CHILDREN_PERSIST_TOO_LONG` diagnostic in completion summary.**

- **`_dispatch_waiting_event` free function for testable subscriber seam** — extracted from the runner's closure so tests can inject a fake subscriber without a full pipeline.

- **feat(display): introduce `DisplayContext` as single source of truth for rendering.**

- **feat(display): detect terminal mode (`compact`/`wide`) and honor `RALPH_FORCE_NARROW`.** (Both superseded later in this release by the single-mode consolidation under Changed.)

- **display: semantic theme keys replace literal Rich style strings** across `phase_banner.py`, `cli/main.py`, and all display modules, using the Okabe-Ito palette as the single colour source.

- **display: add `RALPH_FORCE_NARROW` env knob** to force compact rendering on wide terminals. (Removed later in this release by the single-mode consolidation.)

- **test(policy): add custom policy workflow contract test** demonstrating fully renamed phase/routing/counter names.

- **feat(cli): add explanation sentences for `bypass_routes` and loopback caps in `ralph --explain-policy`.**

- **feat(cli): add ASCII workflow diagram to `ralph --explain-policy`.**

- **feat(parallel): implement same-workspace parallel workers v1** for disjoint `work_units`.

- **feat(parallel): add runner-boundary parallel preflight** rejecting invalid work-unit plans.

- **feat(parallel): add opt-in serialized post-fanout verification policy field.**

- **feat(parallel): add `PostFanoutVerificationEvent` handled by the reducer.**

- **feat(parallel): write `parallel_development_summary.json` after fan-out completes.**

- **feat(display): add section-rule headers to table/panel `emit_*` methods in non-compact mode.**

- **test(display): add black-box coverage for 11 previously untested `emit_*` methods.**

- **docs(architecture): add ADR-0001 interrupt architecture document.**

- **feat(mcp): add built-in `visit_url` web fetcher MCP tool** gated by `WebVisit` capability and configured via `[web_visit]` in `mcp.toml`.

- **test(idle-watchdog): add AST audit `ralph.testing.audit_activity_aware_watchdog` to enforce activity-aware watchdog wiring invariants.** Locks behavior in `tests/test_audit_activity_aware_watchdog.py` and `tests/agents/test_idle_watchdog_3.py`.

- **`WebVisit` capability** (`web.visit` / `WebVisit`) added to `Capability` and `McpCapability` enums and wired into the capability mapping layer with the same alias and policy evaluation as `WebSearch`.

- **`WebVisitConfig`** pydantic model in `ralph.config.mcp_models` with `enabled`, `timeout_ms`, `max_bytes`, `user_agent`, `allow_private_networks`, and `extract_links` fields. Added as `web_visit` field on `McpConfig`.

- **`[web-visit]` extras group** in `pyproject.toml` (`readability-lxml>=0.8.1`, `selectolax>=0.3.21`).

- **Crawl4AI upstream MCP integration documented** in `docs/mcp/mcp-servers.md` — Ralph already supports upstream MCP servers; new section documents how to run Crawl4AI locally and wire it in for multi-page / JavaScript-rendered crawling. No new code; configuration only.

- **`ralph.agents.execution_state`** — provides `AgentExecutionState` (ACTIVE / WAITING_ON_CHILD / RESUMABLE_CONTINUE / TERMINAL_COMPLETE / FAILED) and per-transport `ExecutionStrategy` classes (`GenericExecutionStrategy`, `OpenCodeExecutionStrategy`) so liveness and completion semantics are isolated behind a transport-aware boundary.

- **`ralph.agents.completion_signals`** — provides `CompletionSignals` dataclass and `evaluate_completion(workspace, phase)` to determine whether an agent run produced the required phase artifact, making artifact submission the primary OpenCode success criterion.

- **`ralph.process.liveness`** — provides the `LivenessProbe` protocol, `DefaultLivenessProbe` (queries `ProcessManager` for active labels), and `FakeLivenessProbe` (injectable test fake) so unit tests can exercise multi-agent tree liveness logic without real subprocesses.

- **`OpenCodeResumableExitError`** in `ralph.agents.invoke` — raised when OpenCode exits with code 0 without producing the required phase artifact, allowing the runner to continue the same session instead of restarting from scratch.

- display: colorize LEVEL and CAT badges on TTY; plain text preserved under NO_COLOR/non-TTY.

- display: emit one-line legend inside the [run-start] block describing LEVEL/CAT/[tag] format.

- logging: register SUCCESS (25) and MILESTONE (35) loguru levels aligned with the transcript vocabulary.

- display: print the Rich completion panel at pipeline stop alongside the existing [run-end] lines.

- **feat(display): surface agent output as kind-tagged content/thinking/tool/tool-result lines** with raw-overflow pointers.

- `ralph.display.content_condenser` — predictable head+tail condensation for oversized content lines.

- `ralph.display.raw_overflow` — per-unit raw NDJSON overflow log writer.

- `ralph.display.completion_summary` — end-of-run panel renderer reused by the runner for both dashboard and lines modes.

- `ralph.display.panels.analysis` and `ralph.display.panels.decision_log` — new first-class dashboard regions.

- `ralph.display.artifact_reader` — tolerant readers for `plan.json` and `*_analysis_decision.json` used by the dashboard subscriber.

- `ParallelDisplay.emit_phase_transition` / `emit_analysis_result` — helpers that route transitions and decisions through both the live dashboard and the subscriber's decision log.

- `LiveDashboard.print_above` — serialised helper for printing banners above the live region without fighting the render thread.

- **feat(mcp): add strict startup validation for custom MCP servers configured via `mcp.toml`,** with `RALPH_MCP_STRICT=0` fallback.

- `ralph --diagnose` renders a `Custom MCP Servers` table (per-server status with tool counts) and an `Agent Transport Compatibility` table (Claude/Codex/OpenCode/AGY reachability per server).

- `ralph --check-mcp` runs the custom MCP startup validation and agent transport probe without starting the pipeline, returning exit code 0/1.

- **feat(display): group consecutive text/thinking lines into streaming blocks with summary metadata.**

- **feat(display): add default-on headline extraction for oversized content.**

- **feat(display): add `RingBuffer.consume_drop_delta()` and debounced `WARN META [progress]` drop notifications.**

- **`render_missing_plan_hint`** in `ralph.display.artifact_renderer` — emits an `INFO META [plan] (no plan artifact on disk)` line when planning completes without a `plan.json`, so the log always contains a plan entry.

- **feat(display): wire raw-overflow callback for malformed parser input.**

- **feat(display): emit empty-state placeholders for plan and activity sections at startup.**

- **`theme.log.*` styles** in `RALPH_THEME` — `theme.log.info`, `theme.log.success`, `theme.log.warn`, `theme.log.error`, and `theme.log.milestone` give Rich TTY output visually distinct level badges while plain ANSI-free output continues to emit literal level strings unchanged.

- **`show_phase_start_from_state(state, phase)`** in `ralph.display.phase_banner` and re-exported from `ralph.display` — convenience helper that extracts iteration, reviewer pass, and analysis iteration counters from any state-like object and delegates to `show_phase_start`.

- **feat(display): emit `[run-start]` orientation line at pipeline start.**

- **feat(display): report timing and activity counters in `[phase-close]` lines.**

- **feat(display): emit `[run-end]` MILESTONE block with total elapsed and aggregate counters.**

- **feat(display): suppress identical consecutive streaming fragments by default.**

#### Upstream MCP server coverage

- **feat(idle-watchdog): extend `mcp_tool` activity channel to cover upstream MCP tool calls.** Locks behavior in `tests/mcp/test_mcp_activity_sink.py`.

### Changed

- **refactor(display): close wt-028-display — wire `scripts/wt028-drift-check.sh` into `make verify-drift` and add an end-to-end Status Bar wiring test that drives `_run_inner_loop` and asserts `_push_status_bar_if_changed` calls `display.update_status_bar`**. Locks behavior in `tests/display/test_status_bar.py`, `tests/display/test_single_mode_anti_drift.py`, `tests/display/test_di_invariants.py`, `tests/test_no_anti_drift_regression.py`, the new `tests/pipeline/test_run_loop_status_bar_wiring.py`, and the new `bash ./scripts/wt028-drift-check.sh` line in the `verify-drift` Makefile target.

- **refactor(display): consolidate to a single display mode** — commit `c07c708fc`. The persistent bottom Status Bar always shows working directory, active phase, and applicable outer development / inner analysis iterations for any applicable terminal width (>= 14 cols); at >= 40 cols the canonical `Dev N/cap` / `Analysis N/cap` labels render in full and only path/phase truncation adapts, below 40 cols they may degrade to `D1/3` / `1/3` forms, and below 14 cols the iteration segments drop one at a time so the bar never overflows. Drops narrow / medium / wide modes, the legacy env-var override, the `force_mode` parameter, and the three-tier path/label/limits tables, consolidating limits into a single owner at `ralph/display/_mode_adaptive_limits.py`. Locks behavior in `tests/display/test_single_mode_anti_drift.py`, `tests/display/test_status_bar.py`, `tests/display/test_mode_adaptiveness.py`, `tests/test_parallel_display.py`, and `tests/test_display_context.py`.

- **feat(watchdog): close wt-021 with consolidated Trustworthy Idle Watchdog acceptance-criteria test** — commits `2a4aaf9cd`, `4ddd85613`, `1bc186c8e`, `1c3d4a2de`. Locks behavior in `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py`.

- **fix(agy): align CLI default and live regression suite on `agy/Gemini 3.5 Flash (Medium)` and accept any canonical AGY model display name.** Locks behavior in `tests/test_agy_live_regression.py`, `tests/test_cli_smoke.py`, `tests/test_smoke_agy_end_to_end.py`.

- **feat(idle-watchdog): fast-fires NO_OUTPUT_AT_START at 30s default instead of 60s** in `ralph/timeout_defaults.py`. Locks behavior in `tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py`.

- **fix(idle-watchdog): preserves the NO_OUTPUT_AT_START baseline across LIFECYCLE frames** so a process-startup event cannot mask a hung agent, modifying `ralph/agents/idle_watchdog/idle_watchdog.py`. Locks behavior in `tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py`.

- **refactor(recovery): extracts UnavailabilityStore Protocol** in `ralph/recovery/agent_unavailability_tracker.py` as a runtime-checkable interface seam. Locks behavior in `tests/recovery/test_unavailability_tracker.py`.

- **feat(pipeline): emits structured WAITING / RESUMED logs** at INFO/DEBUG with recovery binding in `ralph/pipeline/run_loop.py` when all agents in a chain are unavailable. Locks behavior in `tests/pipeline/test_run_loop_waiting_state_logs.py` and `tests/recovery/test_all_agents_unavailable_never_crashes.py`.

- **fix(recovery): tightens FailureClassifier._SUBSCRIPTION_LIMIT_SUBSTRINGS with opencode / Claude Code / generic-API cases** in `ralph/recovery/failure_classifier.py`. Locks behavior in `tests/recovery/test_unavailability_reason.py` and `tests/recovery/test_out_of_credits_fast_fallover.py`.

- **refactor(artifact-submission): harden canonical artifact submission path and add make-verify audit.** Locks behavior in `tests/test_canonical_artifact_submit.py`, `tests/test_commit_plumbing_uses_canonical_submit.py`, `tests/test_smoke_plumbing_uses_canonical_submit.py`, and `tests/test_audit_artifact_submission_canonical_path.py`.

- **refactor(pipeline): route plumbing-direct-call fallback through `DefaultPipelineFactory` and add `pro_hooks` plumbing parameter.** Locks behavior in `tests/test_pipeline_factory_default.py` and `tests/integration/test_plumbing_shared_deps.py`.

- **refactor(pipeline): add `DefaultPipelineFactory` as single composition root for main pipeline and plumbing.** Locks behavior in `tests/test_pipeline_factory_default.py` and `tests/integration/test_cli_plumbing_uses_factory.py`.

- **refactor(idle-watchdog): consolidate watchdog logic, delete legacy root module, and enshrine the two main retry rules.** Locks behavior in `tests/recovery/test_two_main_retry_rules.py` and `tests/agents/idle_watchdog/test_dumb_kill_scenarios.py`.

- **feat(idle-watchdog): weight workspace file changes by class so only source-code changes count for `NO_OUTPUT_DEADLINE` by default. Behavior change:** operators who relied on log-file activity to defer the verdict must opt in via `[general] agent_workspace_change_weights`. Locks behavior in `tests/agents/test_dumb_kill_scenarios.py` and `tests/agents/idle_watchdog/`.

- **refactor(idle-watchdog): tighten `_is_no_progress_quiet` to defer `NO_PROGRESS_QUIET` when the corroborator reports any `alive_by` signal** — `NO_PROGRESS_QUIET` now fires only when the corroborator returns no `alive_by` signal AND no fresh channel evidence is present. Locks behavior in `tests/agents/idle_watchdog/test_dumb_kill_scenarios.py`.

- **feat(recovery): surface `child_alive` on `IdleWatchdogKilledError` to differentiate live-child from dead-child `NO_PROGRESS_QUIET`.** Locks behavior in `tests/recovery/test_two_main_retry_rules.py`.

- **fix(interrupt): rename `bridge_pids` kwarg to `bridge_pgids`**; legacy `bridge_pids=` accepted via deprecation shim. `INTERRUPT_EXIT_CODE` (130) unchanged.

- **Parallel plan execution is now delegated to the executing AI agent** via `dispatch_mode = "agent_subagents"`; Ralph-managed fan-out remains dormant and opt-in via `dispatch_mode = "ralph_fan_out"`. Locks behavior in `tests/test_continuation_template_parallel_guidance.py` and `tests/test_audit_parallelization_dormant.py`.

- **refactor(policy): make `_compute_budget_state` fully policy-driven for any declared budget counter.**

- **feat(cli): render ASCII workflow diagram loopbacks unambiguously with counter annotations.**

- **refactor(pipeline): drive `FanOutDevelopmentEffect.run_post_fanout_verification` from the `parallel_execution.post_fanout_verification` policy field and default it to `False`.**

- **refactor(display): complete `DisplayContext` dependency injection across CLI runner and command modules.**

- **docs: update `getting-started.md`, `concepts.md`, `parallel-mode.md`, and `parallel-fan-out.md` to describe v1 same-workspace behavior.**

- **fix(jinja): correct `worker_developer.jinja` to include `shared/_unattended_mode.jinja` and tell workers they share the checkout.**

- **test(anti-drift): strengthen guard against free-function display imports.**

- OpenCode runs no longer treat foreground process exit as terminal success — completion now requires either an explicit completion signal or the required phase artifact to be present in the workspace.

- Idle/timeout evaluation for OpenCode considers Ralph-tracked agent labels (label prefix `agent:`) via the injectable `LivenessProbe` in addition to OS-level descendants, so quiet parents with live subagent work are not killed prematurely.

- **Missing PROMPT.md error now actionable** — when `ralph` is run without a `PROMPT.md`, the preflight error message includes ``Run `ralph --init` to scaffold PROMPT.md and project config files`` so new users know exactly how to fix it.

- **ASCII banner shown on first run.** The Ralph ASCII banner is now printed above the 'Ralph first-run setup' panel on first invocation (and on `--regenerate-config`). It is suppressed on subsequent runs.

- **Self-teaching PROMPT.md template.** `ralph --init` now seeds `PROMPT.md` with a concrete example (Goal, Context, Acceptance criteria, Notes sections) instead of empty placeholders, so new users immediately see what a usable prompt looks like.

- **`ralph --diagnose` hint in Next steps.** The first-run welcome panel and the fallback next-steps list both now include a step recommending `ralph --diagnose` to validate the environment before the first pipeline run.

- **Install URLs for known missing agents.** When a known agent (`claude`, `opencode`) is reported as missing from PATH in the first-run panel, a one-line install hint with the official documentation URL is shown inline.

- **feat(cli): make verbose output the default** and map `--verbosity normal` to verbose; use `--quiet` / `-q` for minimal output.

- The live dashboard now renders `Plan`, `Analysis`, and `Decision Log` panels backed by `.agent/artifacts/plan.json` and the latest `_analysis_decision` artifacts, not just a prompt preview.

- Phase transitions emitted during a run are both printed above the live region and recorded into the dashboard's decision log.

- Pipelines now end with a `Pipeline Complete` (or `Pipeline Failed`) summary panel that echoes the plan, decision log, metrics, verification status, commit, PR URL, and open risks that the user saw live.

- **feat(logging): extend log format with `MILESTONE` level and `META`/`CONT` category prefix.**

- **feat(display): render completion summary with rule-delimited sections and badges.**

- **feat(display): enforce streaming block global-single invariant across workers.**

- **feat(display): manage raw-overflow reference in `PlainLogRenderer`.**

- **feat(display): enable long-content summary by default for blocks over 4000 display cells.**

- **feat(display): include fragment count and char length on streaming `*-end` lines.**

- **feat(display): add 1-based sequence numbers to streaming continue tags.**

- **`WARN META [progress]` for dropped ring-buffer lines** — debounced dropped-line notification now emitted at `WARN` level (not `INFO`) to match its documented severity.

- **Preflight guards unedited starter PROMPT.md** — when running `ralph` with a `PROMPT.md` that still contains the `<!-- ralph:starter-prompt ... -->` marker emitted by `ralph --init`, preflight fails fast with an error that tells the user to edit the file first, preventing an accidental pipeline run against the example task.

- **`--init` docs now use the real contract** — quick-start examples use bare `ralph --init`, compatibility labels remain deprecated/ignored, rerun output no longer prints a fake template selector.

- **chore(cli): remove hidden `isolation_mode` compatibility plumbing and `--no-isolation` path.**

### Fixed

- **fix(display): show the true policy-remediation attempt ceiling in the Status Bar** — `cli_integration.py` now passes `DEFAULT_MAX_REMEDIATION_ATTEMPTS` (`DEFAULT_ANALYSIS_CAP + 1`) to both `_push_remediation_status_bar` calls, so the final attempt renders `Remediation 4/4`, never `Remediation 4/3`; pinned by `tests/display/test_status_bar_phase_walkthrough.py::test_remediation_push_helper_produces_correct_model`.

- **fix(claude-interactive): stop TUI exit banner and escape residue leaking into agent text** — commit `cb287b112`. `normalize_vt_text` now strips the generic ECMA-35/48 escape forms (charset designations like `ESC(B`, cursor save/restore `ESC7`/`ESC8`, SO/SI shift bytes) and the two-line Claude Code >= 2.1.x resume banner plus slash-command echoes are TUI chrome; regression-guarded by `tests/test_claude_interactive_parser.py`.

- **fix(claude-interactive): emit parallel same-tool calls, surface `is_error` tool results as errors, and harden string-shaped JSON payloads** — commit `03ee17139`. The consecutive-duplicate guard now keys on `tool_use_id`, failed tool calls surface as `type=error` with tool identity (parity with the headless Claude, Cursor, Pi, and Generic parsers), and string-typed assistant content / error payloads are no longer dropped; pinned by `tests/test_claude_interactive_parser.py`.

- **fix(telemetry): keep one random user identity across terminal-specific XDG environments and add queryable session timing plus safe logical-agent, pipeline-profile, and drain-class metrics** — pinned by `tests/test_telemetry_user_identity.py` and `tests/test_telemetry_sentry.py`.

- **fix(claude-interactive): preserve structured transcript envelopes so parallel tool results retain their tool identity and inputs, repeated session IDs stay suppressed, and result labels render once** — regression-guarded by `tests/test_claude_interactive_parser.py` and `tests/test_pty_transcript_tool_result.py`.

- **fix(policy): repair dead/stale research-citation URLs in bundled project-policy starters and add the policy-citation-linkcheck gate + offline citation-structure guard** — replaces 11 non-resolving URLs across 8 starter files with primary sources verified to return HTTP 200 directly, adds `make policy-citation-linkcheck` (on-demand gate reusing `scripts/check_route_page_links.py`), and adds an offline pytest guard asserting every citation block carries publisher/title/review-date fields, an https URL with a non-empty host, and an ISO `YYYY-MM-DD` review date. Pinned by `tests/project_policy/test_starters.py::test_starter_citations_are_structurally_valid`.

- **fix(policy): repair corrupted completion-marker text in all 12 bundled project-policy starter files** — commit `b538b9dea`. The 'Ralph markers' section named a garbled token instead of `ralph-policy-complete`; regression-guarded by `tests/project_policy/test_starters.py`.

- **fix(recovery): route missing plan handoff back to entry phase from any non-planning phase** (development, planning_analysis, review, etc.) by dropping the `failed_route` guard in `ralph/pipeline/runner.py:_handle_inline_effect`. Locks behavior in `tests/pipeline/test_runner_missing_plan_handoff_recovery.py`.

- **fix(display): allowlist `tests/integration/test_status_bar_pty_e2e.py` in `tests/test_process_audit.py:TESTS_ALLOWLIST`** so the production Status Bar PTY regression test can use `subprocess.run` and `os.setsid` to drive the real-kernel-TTY reproduction. Locks behavior in `tests/test_process_audit.py::test_no_direct_subprocess_calls_in_tests`.

- **fix(display): wipe residual display-mode drift** — replaces stale `mode="wide"` / `narrow=False` fixtures across 8 test helper sites with `mode="default"`, drops 16 cosmetic test names still referencing 'wide mode' / 'compact mode' / 'medium mode', and adds `tests/display/test_single_mode_anti_drift_test_fixtures.py` as the AST-level regression lock.

- **fix(display): prove the persistent bottom Status Bar renders through the production entry point and lock its single-owner lifecycle** — adds `tests/integration/test_status_bar_runtime_visibility.py` (Live activation on a tty-like stream, model propagation, workspace/phase/`Dev N/cap`/`Analysis N/cap` content, phase-conditional omission with no `--` placeholder, quiet-mode zero emission, non-tty gate suppression), the real-kernel-TTY reproduction `tests/integration/test_status_bar_pty_e2e.py` at a fixed 120x40 window, and the AST guard `tests/display/test_status_bar_single_owner.py` asserting `ParallelDisplay` is the only class that constructs `StatusBar` and the only caller of its `start()` / `stop()`. Coverage-completion only; no production code changed.

- **fix(agy): make live AGY end-to-end smoke green with deterministic alias, longer timeouts, and parser-classified events.** Locks behavior in `tests/test_agy_live_regression.py` and `tests/test_agy_harness_with_mock.py`.

- **fix(recovery): remove generic `rate_limited` marker from `OUT_OF_CREDITS` substring table.** Locks behavior in `tests/recovery/test_unavailability_reason.py::test_generic_throttling_does_not_classify_as_out_of_credits`.

- **fix(pipeline): bind pre-sleep wait-state DEBUG log with `recovery=True`** — `ralph/pipeline/run_loop.py:403` now uses `logger.bind(recovery=True).debug(...)` so the binding is present on every pre-sleep line (the AC-08 contract). Locks behavior in `tests/pipeline/test_run_loop_waiting_state_logs.py` and `tests/pipeline/test_run_loop_waiting_state_real_controller.py`.

- **fix(recovery): cap `_mark_agent_unavailable` return value at the reason's `max_backoff_ms`.** Locks behavior in `tests/recovery/test_out_of_credits_fast_fallover.py::test_controller_mark_agent_unavailable_caps_return_value_at_30_minutes`.

- **fix(recovery): strengthen wait-state survival test to 30 consecutive `handle()` calls with bounded-stability assertion.** Locks behavior in `tests/recovery/test_all_agents_unavailable_never_crashes.py`.

- **fix(recovery): add import-time AST invariant ensuring `RecoveryController` never exits from the all-agents-unavailable branch.** Locks behavior in `tests/recovery/test_two_state_invariant.py::test_never_exit_invariant_locked_at_import_under_python_O`.

- **fix(pipeline): replace brittle `last_error` text parser with structured `state.is_waiting_state` flag.** Locks behavior in `tests/pipeline/test_run_loop_waiting_state_real_controller.py`, `tests/pipeline/test_run_loop_waiting_state_logs.py`, and `tests/recovery/test_no_progress_quiet.py`.

- **refactor(recovery): inject `UnavailabilityStore` Protocol into `RecoveryControllerOptions` and expose public controller surface.** Locks behavior in `tests/pipeline/test_run_loop_waiting_state_real_controller.py` and `tests/recovery/test_unavailability_tracker.py`.

- **fix(agy): refresh upstream and local AGY source-of-truth and pin live AGY smoke with non-mock regression tests.** Locks behavior in `tests/test_agy_live_regression.py`, `tests/test_smoke_agy_end_to_end.py`, and `tests/test_check_mcp_cli_agy.py`.

- **fix(agy): align smoke harness fixtures, resolver tests, and documented examples with the real AGY v1.0.8 wire format.**

- **fix(agy): add `RALPH_AGY_BINARY` env override, deterministic mock AGY binary, and subprocess_e2e tests proving end-to-end output without a live account.**

- **fix(agy): move `RALPH_AGY_BINARY` override seam to the CLI surface and harden AGY smoke tests/docs.** Locks behavior in `tests/test_agy_harness_with_mock.py` and `tests/test_agy_plumbing_mock.py`.

- **fix(agy): drain the PTY master after child exit so live AGY buffered stdout is captured.** Locks behavior in `tests/test_agy_pty_invocation.py` and `tests/test_agy_live_regression.py`.

- **fix(interrupt): close sync entry-point seam on `handle_keyboard_interrupt` with injected `process_manager` and `poll_interval_s`.** Locks behavior in `tests/test_runner_interrupt.py` and `tests/pipeline/test_run_loop_interrupt.py`.

- **fix(interrupt): make asyncio first-SIGINT handler non-blocking by dispatching `begin_interrupt` and `run_early_escalation_poll` via `loop.run_in_executor` with a done callback.**

- **fix(interrupt): bound `_wait_for_list_active_empty` sleep to the remaining deadline, eliminating the fixed 0.01s spin.** Regression pinned by `test_dispatcher_block_wait_sleep_never_exceeds_remaining_deadline`.

- **fix(interrupt): make `install_signal_handlers` return an idempotent teardown callable and update `fan_out.py` to invoke it on every exit path.**

- **fix(interrupt): remove redundant per-pgid `kill_process_group` loop from `controller.force_interrupt`, relying on `ProcessManager.shutdown_all(0)` for SIGKILL.**

- **fix(commit_cleanup): surface untracked files in the cleanup diff and harden safety rules.** Locks behavior in `tests/test_commit_cleanup_diff.py` and `tests/test_phases_commit_cleanup.py`.

- **fix(invoke): wire subprocess reader `InactivityTimeoutOpts` with `session_resume_safe` and `resumable_session_id`.** Locks behavior in `tests/test_subprocess_reader_resume_safe.py` and `tests/test_pipeline_runner_opencode_resume_build_agent_recovery_plan_resumable_session.py`.

- **fix(effect_executor): make retry prompt action-aware so resume never inlines the original task.** Locks behavior in `tests/test_no_anti_drift_recovery_invariants.py`.

- **fix(interrupt): add black-box tests for second-SIGINT-during-first-SIGINT-executor-body and slow `begin_interrupt` escalation.**

- **fix(interrupt): add SYNC long-running-body test pin, remove dead `grace_period_s`, re-export dispatcher, extract `run_shutdown_block`, and pin import-time constants.** Locks behavior in `tests/test_runner_interrupt.py`, `tests/test_interrupt_constants.py`, and ADR-0001 D7/D8.

- **fix(interrupt): switch production `run_shutdown_block` to liveness-based `_wait_for_list_active_empty` instead of CPU polling.** Locks behavior in `tests/test_runner_interrupt.py` and `tests/test_interrupt_dispatcher.py`.

- **fix(agy): close the real CLI flow with relative `RALPH_AGY_BINARY` normalization and mock-backed parity.** Locks behavior in `tests/test_agy_live_regression.py` and `tests/test_cli_smoke.py`.

- **fix(idle-watchdog): fire OS-descendant-only child evidence at `max_waiting_on_child_no_progress_seconds` (default 600s).**

- **fix(policy): reject commit phases that track a budget counter without matching `post_commit_routes`.**

- **fix(opencode): eliminate false-positive `OpenCodeResumableExitError` by waiting for late completion artifacts and child agents before declaring resumable failure.**

- `-D` / `--developer-iters` and `-R` / `--reviewer-reviews` CLI flags now correctly control the number of dev/review cycles. Previously, the pipeline ran exactly one dev cycle and one review pass regardless of the flag values. The `developer_iters` setting controls dev cycles; `reviewer_reviews` controls review cycles. (Both reviewer flags were later removed — see Removed.)

- `-R=0` now skips the review phase entirely instead of running one forced review pass.

- Analysis loopbacks from `development_analysis` no longer consume a development budget slot. Loopbacks are retries of the same iteration, not new iterations.

- `make typecheck` now runs mypy through `uv run python -m mypy` so it uses the project's virtualenv instead of a system-wide mypy that cannot see project dependencies.

### Removed

- **refactor(display)!: remove dead `force_mode=` keyword argument and its NotImplementedError raise shim** from `make_display_context()` in `ralph/display/context.py`. Breaking change for any caller that explicitly passed `force_mode=` — those callers now raise `TypeError: unexpected keyword argument` instead of `NotImplementedError`. No production caller exists; locks regression in `tests/display/test_single_mode_anti_drift.py` (the two pinning tests removed).

- **refactor(display)!: remove orphaned public exports `RalphProgress` and `get_progress`** from `ralph/display/__init__.py` (and the orphaned `ralph/display/progress.py` + `ralph/display/_progress_singleton.py` + `ralph/display/progress_protocols.py` modules they lived in). Breaking change for any out-of-tree consumer that imported these names; zero production callers in this repo. `tests/test_display_progress.py` and its 5 dummy helper modules are removed in the same change.

- **chore(pipeline): consolidate redundant `_safe_push_status_bar` into the deduping `_push_status_bar_if_changed` push function** in `ralph/pipeline/run_loop.py` (module-private; no public API effect).

- **chore(cli): delete dead local `_status_text` helper** in `ralph/cli/commands/run.py` (module-private; no public API effect).

- **chore(docs): remove obsolete review-era controls from the canonical Agent Compatibility Guide** — `ralph-workflow/docs/sphinx/agent-compatibility.md` no longer documents the removed `--reviewer-agent`, `--reviewer-model`, `--reviewer-reviews`, `--review-depth` CLI flags, the `force_universal_prompt` `[general]` config key, or the `RALPH_REVIEWER_UNIVERSAL_PROMPT` / `RALPH_REVIEWER_REVIEWS` environment variables; the "Universal Review Prompt" and "How to use a different reviewer" sections are gone and analysis-phase routing is driven entirely by `[agent_chains]` + `[agent_drains]` in `~/.config/ralph-workflow.toml`. This entry supersedes the `-D` / `--developer-iters` and `-R` / `--reviewer-reviews` entry under Fixed, which records the earlier addition-and-fix cycle for those flags. Locked by `tests/test_cli_removed_review_flags.py`.

- **fix(interrupt): remove redundant `SignalBridge.pids` registration from `ralph.interrupt.asyncio_bridge`.**

- **Cloud reporting infrastructure removed.** The old cloud reporting package, API shim, config model, and TOML section have been removed in favour of the new `ralph.supervising` trackable instance model.

- **chore(parallel): remove dead worktree-first parallel code paths**; only `ParallelExecutionMode.SAME_WORKSPACE` is supported.

- **chore(config): remove global `[parallel_execution]` block**; parallelization is now configured per phase under `[phases.<phase>.parallelization]`.

- **chore(parallel): remove `commit_sha` field from worker events/state/snapshot** (worktree-era relic).

- `max_dev_continuations` config field (previously declared but never used).

- `_legacy_handle_agent_success` reducer path (unreachable in production — policy is always loaded).

- Unused `developer_iters_option` / `reviewer_reviews_option` decorator stubs in `cli/options.py`.

### Documentation

- **docs(mcp): document the indexed-exploration substrate (Phases 0-4)** — covers `ralph_index_status`, `ralph_reindex`, `ralph_graph`, and the indexed optional arguments on `grep_files` / `search_files` / `read_file` / `read_multiple_files` / `list_directory` / `directory_tree` / `edit_file`, including the `use_index` auto/never/always fallback contract and the deterministic reindex lifecycle with `dirty_paths` mark-then-refresh semantics. Pins documentation synchronization in `tests/test_documentation_command_sync.py`, `tests/test_repo_root_docs_sync.py`, and `tests/test_package_markdown_docs_sync_package_mcp_docs.py`.

- **docs(consolidation): close wt-026 by collapsing overlapping guides and removing process cruft** — the doc surface is reduced to one canonical home per topic. Legacy duplicates are deleted (root `CODE_STYLE.md`, `docs/architecture/overview.md`, `docs/plans/README.md`, `docs/tooling/`, `ralph-workflow/docs/mcp/`, the `X9/` stray directory, `assets/built-with-ralph-loop.svg`, the developer-only `run_ralph.sh` / `run_verify_proof.py` scripts, and the root `checkpoint.json`); the root `CONTRIBUTING.md`, `START_HERE.md`, `README.md`, `PROMPT.md`, the `docs/` map, the `ralph-workflow/docs/README.md` router, the package `README.md`, the Sphinx `index.rst` (a curated 19-entry toctree), and `ralph-workflow/CONTRIBUTING.md` are reduced to their canonical role. Every cross-reference in the surviving docs resolves. Locks the canonical-home mapping in `tests/test_documentation_command_sync.py`, `tests/test_repo_root_docs_sync.py`, `tests/test_package_markdown_docs_sync_package_mcp_docs.py`, and `tests/test_repo_root_operational_docs_sync.py`.

- **docs(pi): close documentation gaps by enumerating `pi` in configuration, concepts, parallel-mode, and advanced-mcp docs.** Locks behavior in `tests/agents/invoke/test_pi_command_builder_and_runtime_resolver.py` and `tests/agents/parsers/test_pi_dev_wire_format_spec.py`.

### Migration

- Users relying on the previously silent default behavior should now pass `--quiet` (or `-q`) explicitly.

- Users currently passing `--verbosity normal` will see verbose output; they will need to switch to `--quiet` to restore the minimal variant.

- Log parsers that assumed a fixed level set `{INFO,WARN,ERROR,SUCCESS}` must now also accept `MILESTONE` on phase-transition rows.

- Log parsers that matched `[content-continue]` or `[thinking-continue]` exactly must update to match `[content-continue#N]` / `[thinking-continue#N]` (where N is a 1-based integer).

- Tooling that expected dropped-buffer notifications at `INFO` level must update to `WARN`.

- Users resuming from a prior buggy checkpoint must delete `.agent/checkpoint.json` before re-running `ralph -D N -R M` from scratch.
