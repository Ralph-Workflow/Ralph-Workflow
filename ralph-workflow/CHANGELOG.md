# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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
- **`--init` help text and README Quick start corrected.** The `--init` flag no longer advertises non-existent template names (e.g. `starter-template`, `feature-spec`); the README Quick start example now uses `ralph --init default` and explicitly notes the template label is cosmetic today.

### Added
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
- `-D` / `--developer-iters` and `-R` / `--reviewer-reviews` CLI flags now correctly control the number of dev/review cycles. Previously, the pipeline ran exactly one dev cycle and one review pass regardless of the flag values. The `developer_iters` setting controls dev cycles; `reviewer_reviews` controls review cycles.
- `-R=0` now skips the review phase entirely instead of running one forced review pass.
- Analysis loopbacks from `development_analysis` no longer consume a development budget slot. Loopbacks are retries of the same iteration, not new iterations.
- `make typecheck` now runs mypy through `uv run python -m mypy` so it uses the project's virtualenv instead of a system-wide mypy that cannot see project dependencies.

### Removed
- `max_dev_continuations` config field (previously declared but never used).
- `_legacy_handle_agent_success` reducer path (unreachable in production — policy is always loaded).
- Unused `developer_iters_option` / `reviewer_reviews_option` decorator stubs in `cli/options.py`.

### Migration Notes
- **Users resuming from a checkpoint created by a prior buggy version**: the stored `development_budget_remaining` and `review_budget_remaining` fields will be `0`, causing the fix to silently repeat the old broken behavior. To get the fix, delete the checkpoint before resuming:
  ```bash
  rm .agent/checkpoint.json
  ```
  Then re-run `ralph -D N -R M` from scratch. Affected users already had a broken run (dev ran once), so this is equivalent to restarting the intended work.
