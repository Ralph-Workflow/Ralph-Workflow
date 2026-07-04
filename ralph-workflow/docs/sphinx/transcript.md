# Transcript and Display Reference

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

This page explains the terminal transcript Ralph Workflow prints during a run. It is mainly for contributors and operators who want to decode the exact line format, display rules, and lifecycle banners.

If you just need to run Ralph Workflow successfully, you can skip this page and use [Getting Started](getting-started.md), [CLI Reference](cli.md), and [Troubleshooting](troubleshooting.md) instead.

Ralph Workflow emits a structured, line-oriented transcript to stdout. Every line has a fixed format that can be machine-parsed or read directly in a terminal.

## Display Architecture

`DisplayContext` (from `ralph.display`) is the single place where Ralph Workflow decides how output should render: console, theme, terminal width, color policy, display mode, and adaptive character limits.

### Dependency Injection Contract

Every renderer function requires a `display_context: DisplayContext` argument. No renderer should construct its own `rich.Console`. Callers create a `DisplayContext` with `make_display_context()` before invoking any renderer.

```python
from ralph.display import make_display_context

ctx = make_display_context()          # uses terminal width, NO_COLOR, etc.
show_phase_start("planning", display_context=ctx)
```

### Width Precedence

| Priority | Source | Effect |
|----------|--------|--------|
| 1 | `force_width` argument to `make_display_context()` | Overrides all width detection |
| 2 | `COLUMNS=<N>` env var (positive int) | Overrides console.width |
| 3 | `console.width` (actual terminal width) | Default fallback |

### Display mode (single default)

Ralph Workflow exposes exactly ONE display mode: ``default``. There is no
width-based dispatch and no per-mode limits table. The persistent bottom
Status Bar renders all applicable fields (working directory, active phase,
applicable outer development iteration, applicable inner analysis
iteration) at every terminal width where they fit. At widths >= 40 cols
the canonical ``Dev N/cap`` / ``Analysis N/cap`` labels render in full and
only path middle-truncation and phase tail-truncation budgets adapt to
width. Below 40 cols the implementation may degrade to compact
(``D1/3`` / ``A2/5``) or minimal (``1/3`` / ``2/5``) forms to fit. Below
14 cols the iteration segments drop one at a time (outer_dev first, then
inner_analysis, then both) so the bar never overflows the working area;
phase and path remain visible at every applicable width.

The historical env-var override that selected a narrower mode is silently
ignored.

### Color Precedence

| Priority | Env var | Effect |
|----------|---------|--------|
| 1 | `NO_COLOR=<any>` | Disables all ANSI color output |
| 2 | `FORCE_COLOR=<any>` | Forces ANSI color on (even when not a TTY) |

`NO_COLOR` takes precedence over `FORCE_COLOR` per standard CLI conventions.

### Width Refresh (cross-platform)

A width refresher is installed at pipeline start via `install_width_refresher()`. When
the terminal is resized:

1. The refresher calls `DisplayContext.refreshed()` which re-reads the current terminal
   width while preserving the fixed `default` display mode and fixed adaptive limits
   (the single mode invariant: width refresh only mutates `width`).
2. Renderers that buffer adaptive limits (e.g., `PlainLogRenderer`) refresh their context
   at phase boundaries via `flush_blocks()`.
3. The runner keeps its live display object and nested plain renderer synced with the
   refreshed context, so later banners and summaries render against the refreshed width
   inside the same single `default` mode.

On POSIX systems (Linux, macOS) when called from the main thread, the refresher installs
a `SIGWINCH` signal handler. On Windows, or when called from a non-main thread, a
background poll thread monitors width changes instead. The returned stop callback is
invoked at pipeline shutdown to clean up the poll thread when one was started.

<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the older "AI agent
    orchestrator built around a simple ... Ralph-loop core" lead category.
  - Why it belongs here: this page is part of the maintained Sphinx manual;
    it must agree with the README and the manual home so the product story
    is coherent across surfaces (rubric hard failure: surfaces fight each
    other).
  - What was pruned: nothing material; the page's page-specific argument is
    preserved.
  - How the route is clearer: the lead now matches the canonical autopilot
    framing used by the README and the manual home.
  - What was added (wt-028-display): a new `### Status Bar` subsection
    under `## Display Architecture` describing the persistent bottom footer
    composed by `StatusBar` via `ParallelDisplay`. The footer is the single
    owner of run-level layout/color/spacing/truncation/live-update behavior,
    so future display changes have one clear product surface to maintain.
    This page already names `DisplayContext` and the `emit_*` set as the
    single owners of display logic, so the Status Bar owner is named on the
    same surface as a peer reference (not a competing one). The status-bar
    subsection explicitly cross-references `ParallelDisplay.update_status_bar`
    and notes that the per-unit `emit_status_line` and waiting_status_line
    remain orthogonal surfaces.
-->

### Status Bar

A persistent single-line **Status Bar** is pinned to the bottom of the interactive
terminal display during real-TTY runs. It gives operators an immediate, stable answer
to "where am I in this run?" without scrolling or reconstructing state from logs.

**What the Status Bar shows**

| Field | Source | When shown |
|-------|--------|------------|
| Working directory | `workspace_root` from the active `StatusBarModel`, home-relative and middle-truncated to fit within the path budget | Always |
| Active phase label | `PhaseEntryModel.human_label()` (e.g. `Development`, `Development Analysis`) colored by `phase_style_for_phase` | Always |
| `Dev N/cap` | `format_dev_cycle(outer_dev_iteration, outer_dev_cap)` — 1-indexed current cycle from `PhaseEntryModel` (`completed + 1`) | When `outer_dev_iteration is not None` |
| `Analysis N/cap` | `format_analysis_cycle(inner_analysis, inner_analysis_cap)` — 1-indexed current cycle from `AnalysisLoopCounter.display_iteration` | When `inner_analysis is not None` |

Iteration fields are **omitted** (no placeholders, no empty segments) when the active
phase does not carry that iteration context.

**When the Status Bar appears**

The bar is gated on a real-TTY check:

```python
ctx.console.is_terminal and bool(getattr(ctx.console.file, "isatty", lambda: False)())
```

Both conjuncts are required because Rich's `Console.is_terminal` is
`force_terminal OR isatty()`, so a `force_terminal=True` StringIO console reports
`is_terminal == True` and would otherwise start the Live region; the `isatty()` check
keeps the bar out of:

- output redirects (`> file`)
- CI logs and tee pipes
- `StringIO` test consoles
- `force_terminal=True + StringIO` setups (the same shape used by
  `tests/display/test_parallel_display_tty_parity.py`)
- quiet mode (`ParallelDisplay(is_quiet=True)`)

In all of those cases `StatusBar.start()` is a no-op — the lifecycle is silent and the
captured transcript stays clean for machine parsers and post-run review.

**Width-aware truncation**

The bar is mode-agnostic: it renders every applicable field described above
at every terminal width where they fit. At widths >= 40 cols the canonical
``Dev N/cap`` / ``Analysis N/cap`` labels render in full. Below 40 cols the
implementation may degrade to compact (``D1/3`` / ``A2/5``) or minimal
(``1/3`` / ``2/5``) forms to fit. Below 14 cols the iteration segments drop
one at a time (outer_dev first, then inner_analysis, then both) so the bar
never overflows the working area; phase and path remain visible at every
applicable width. Width only influences path middle-truncation and phase
tail-truncation budgets in addition:

- Long paths are middle-truncated (preserve first 8 chars + ellipsis + last segment) to
  fit within `DEFAULT_PATH_BUDGET = 48` chars.
- Long phase labels are tail-truncated (preserve the leading word) to fit within
  `DEFAULT_PHASE_LABEL_BUDGET = 28` chars.

The two budgets are constants on `ralph.display.status_bar`, not a per-mode table, so
the bar layout stays scannable on common laptop widths and stays readable on
external-monitor widths without changing which fields render.

**Single owner and refresh cadence**

The Status Bar owns run-level layout, color, spacing, alignment, truncation, and
live-update behavior. The bar is composed by `ParallelDisplay` (reachable as
`pd.status_bar` and updated via `pd.update_status_bar(model)`); it is **outside** the
canonical one-shot `emit_*` surface because it is a persistent live region with a
start/stop lifecycle rather than a one-shot surface.

The Live region renders at a steady, deliberate cadence:

- `_STATUS_BAR_REFRESH_PER_SECOND = 4.0` (250ms refresh tick), pinned by
  `tests/display/test_status_bar.py::test_status_bar_pins_steady_cadence_config`.
- `_STATUS_BAR_TRANSIENT = True` (frames erased on stop, preserving clean scrollback,
  copy/paste, terminal search, and post-run log review).

The per-unit `emit_status_line` and the transient `waiting_status_line` remain
orthogonal one-shot surfaces for activity breadcrumb rows and waiting-status ticks;
the Status Bar is the **persistent run-level footer** and stays the single owner of
that surface.

**Operators see one coherent run-level surface**

Operators may leave a run unattended for long periods, return to the terminal, and
read the working directory, current phase, and applicable cycle counts at a glance —
without scrollback, copy/paste, terminal search, or post-run log review being worse
than before.

### Status Bar during a missing-plan-handoff recovery loop

When a non-planning phase (e.g. `development`) tries to materialize a prompt but `.agent/PLAN.md` is missing, the runner catches `MissingPlanHandoffError` and routes through `recover_missing_plan_handoff` back to `pipeline_policy.entry_phase` (`"planning"` for the default policy). Operators watching the run see:

- The persistent bottom Status Bar keeps displaying the **active phase** throughout the recovery loop. While `_run_inner_loop` is routing through the recovery helper, the bar updates once per `(phase, outer_dev_iteration, inner_analysis)` signature change so a recovery that returns the state to `"planning"` refreshes the bar rather than leaving a stale `development` footer visible.
- The bar's dedupe contract (`signature != last_sig` in `_push_status_bar_if_changed`) suppresses flicker on unchanged signatures, so the footer stays steady even if the loop re-evaluates the same phase on consecutive ticks.
- The underlying `MissingPlanHandoffError` message is preserved on the recovered state's `last_error` field (matching the `ExitFailureEffect` convention), so the operator transcript and the Status Bar's lifecycle still surface the real cause.
- The recovery is bounded by `pipeline_policy.recovery.cycle_cap` (default 200); when `recovery_epoch >= cycle_cap` the helper routes to `failed_route` instead of re-entering `entry_phase`, so an unbounded loop is impossible. The mechanism itself is documented in `display.rst`; this subsection only describes the operator-visible behavior.

## Line Format

```
<ISO-TS> <LEVEL> <CAT> [<tag>][<unit>] <content>
```

| Field | Example | Notes |
|-------|---------|-------|
| `<ISO-TS>` | `2026-04-25T12:00:00Z` | ISO-8601 timestamp |
| `<LEVEL>` | `INFO` | One of the five levels below |
| `<CAT>` | `META` | `META` or `CONT` |
| `[<tag>]` | `[phase]` | Sub-operation tag (see table below) |
| `[<unit>]` | `[unit-1]` | Work unit ID in parallel runs; omitted otherwise |
| `<content>` | `Planning started` | Human-readable message |

## Levels

| Level | Meaning |
|-------|---------|
| `INFO` | Routine update or progress |
| `SUCCESS` | Phase or pipeline completed successfully |
| `WARN` | Non-fatal issue or degraded state |
| `ERROR` | Fatal error or malformed input |
| `MILESTONE` | Major phase transition (planning, development, commit) |

Verbosity controls which levels are shown. Use `--quiet` to suppress everything except
`ERROR`, or `--debug` to show all levels.

## Categories

| Category | Meaning |
|----------|---------|
| `META` | Workflow metadata: phase transitions, plans, activity, worker events, run summary |
| `CONT` | Agent-produced content: text, thinking blocks, tool calls, tool results, errors |

## Tags

| Tag | Category | Description |
|-----|----------|-------------|
| `phase` | META | Phase start event |
| `phase-close` | META | Phase complete event |
| `plan` | META | Plan summary line |
| `plan-scope` | META | Plan scope field |
| `plan-steps` | META | Plan steps list |
| `activity` | META | Agent activity update |
| `analysis` | META | Analysis decision |
| `worker` | META | Parallel worker event |
| `result` | META | Phase result summary |
| `pr` | META | Pull request / commit reference |
| `artifact` | META | Artifact submission event |
| `progress` | META | Progress report from agent |
| `run-start` | META | Pipeline run start |
| `run-end` | META | Pipeline run end with summary fields |
| `content` | CONT | Agent text content (single-line) |
| `content-start` | CONT | Start of a streaming long-content block |
| `content-continue` | CONT | Continuation chunk of a long-content block |
| `content-end` | CONT | End of a long-content block |
| `content-checkpoint` | CONT | Streaming checkpoint within a long-content block |
| `thinking-start` | CONT | Start of agent thinking block |
| `thinking-continue` | CONT | Continuation of thinking block |
| `thinking-end` | CONT | End of thinking block |
| `tool` | CONT | Tool call event |
| `tool-result` | CONT | Tool result event |
| `error` | CONT | Agent error event |
| `status-content` | CONT | Agent status/progress message |

## Streaming Blocks and Long-Content Display

Long agent outputs (for example code, plans, or long prose) are emitted as streaming blocks bounded by `content-start` / `content-end` tags. Within a block:

- `content-continue` lines carry the raw streamed chunks.
- `content-checkpoint` lines appear at configurable intervals to allow progressive display without buffering the entire block.

Ralph Workflow also applies a deterministic headline summary layer when a completed block exceeds **4000** display cells. That layer is **enabled by default**. It appears before the condensed output so operators get a stable summary instead of scrolling through a giant block.

If no clean headline can be extracted, Ralph Workflow shows **`(no headline available)`**. Inline summary lines are capped at **200** characters, and streaming end-line summaries are capped at **120** characters.

Disable the deterministic headline layer with `RALPH_LONG_CONTENT_SUMMARY` values `0`, `false`, `no`, or `off`. There is no special opt-in value because the feature is already on by default.

When a block ends, Ralph Workflow may append summary lines depending on configuration:

- `⇳ summary:` — static truncation summary (always present for very long blocks)
- `⇳ preview:` — first *N* characters of the block content
- `⇳ ai-summary:` — LLM-generated one-line summary (requires `RALPH_LONG_CONTENT_AI_SUMMARY`)

The optional AI-generated layer is separate from the deterministic headline layer. Use `RALPH_LONG_CONTENT_AI_SUMMARY` only when you want the additional `↳ ai-summary:` style output.

## Phase-Start Banner

Before each phase begins, a phase-start banner is printed to the console.  The single
default-mode layout uses a uniform banner shape across every terminal width: a titled
Rule separator precedes the banner (title = phase label + iteration context), with
`(outer)` / `(inner)` qualifiers appended to the iteration labels when those qualifiers
apply and the agent name shown inline on the banner line.

```
─────────── <Phase Label>  <od_glyph> Dev N/cap  <ia_glyph> Analysis N/cap ───
<glyph> <Phase Label>  <od_glyph> Dev N/cap [(outer)]  <ia_glyph> Analysis N/cap [(inner)]  [N left|last]  [agent=<name>]
```

| Field | Notes |
|-------|-------|
| `<Phase Label>` | Human-readable phase name (e.g. `Development Analysis`) |
| `Dev N/cap` or `Dev #N` | Outer development cycle — 1-indexed current cycle number; shows cap when progress is tracked |
| `(outer)` | Qualifier appended to the outer dev iteration label to clarify which cycle scope this is |
| `Analysis N/cap` or `Analysis #N` | Inner analysis loop iteration — 1-indexed; shows cap when known |
| `(inner)` | Qualifier appended to the inner analysis iteration label to clarify which cycle scope this is |
| `[N left]` | Remaining analysis iterations before cap is reached — shown when the cap is known and iterations remain |
| `[last]` | Shown when the current analysis iteration is the final one allowed by the cap |
| `agent=<name>` | Active agent identity — shown inline on the banner line |

All iteration fields are optional and appear only when the pipeline has that context.
`Dev N/cap` counts from 1: `Dev 1/5` means the pipeline is entering its first development
cycle out of a total budget of 5. `Dev 0/cap` is never shown.

The titled Rule carries the same iteration labels as the banner line so the section
heading is immediately scannable even when the banner itself scrolls out of view.

## Phase-Close Banner

When a phase ends and the pipeline transitions to the next phase, a rich visual
phase-close banner is printed to the console:

```
<success_glyph> <Phase Label>  <od_glyph> Dev N/cap [(outer)]  <ia_glyph> Analysis N/cap [(inner)]  Ns  <arrow> <exit_trigger>
    ↳ stats: content=N thinking=N tools=N [errors=N]         ← when activity > 0
    ↳ artifact: <artifact_outcome>                            ← when artifact produced
  <warning_glyph> debug: waiting: <waiting_status> | failure: <failure_category>   ← only when breadcrumbs exist
──────────── Ns  <arrow> <exit_trigger> ──────────────────── ← titled trailing Rule
```

| Field | Notes |
|-------|-------|
| `<success_glyph>` | `✓` (Unicode) or `[OK]` (ASCII) |
| `<Phase Label>` | Human-readable phase name (e.g. `Development Analysis`) |
| `Dev N/cap` or `Dev #N` | Outer development cycle — 1-indexed; same label as phase-start |
| `(outer)` | Qualifier appended to the outer dev iteration label |
| `Analysis N/cap` or `Analysis #N` | Inner analysis loop iteration — same label as phase-start |
| `(inner)` | Qualifier appended to the inner analysis iteration label |
| `Ns` | Wall-clock elapsed time for the phase, in seconds (omitted when 0) |
| `<arrow> <exit_trigger>` | Why the phase ended — present when an exit trigger is known |
| `↳ stats:` | Phase-level activity counters — shown when any counter is non-zero |
| `↳ artifact:` | What the phase produced (e.g. `plan: 5 step(s), 2 risk(s)`) — shown when set |
| `debug: waiting: …` | Last waiting-status line recorded during this phase (present only when set) |
| `debug: … failure: …` | Last failure category recorded during this phase (present only when set) |
| trailing Rule | Printed after all detail lines with `Ns  → exit_trigger` as the Rule title (or a plain
    Rule when both are absent); symmetrically closes the section opened by the phase-start titled Rule |

All iteration fields are optional and appear only when the pipeline has that context.
This banner is symmetric with the phase-start banner: same field ordering, same glyphs,
same style keys — making before/after pairs easy to read in the terminal.

When a phase ends with a waiting or failure breadcrumb still set (e.g. a timeout or tool
error), an indented debug line appears immediately below the close banner. This makes
failure-related state visible without requiring the completion summary to be read first.
The waiting status is truncated to 80 characters. The debug line appears whenever the
data is present, regardless of terminal width.

## Phase-Transition Banner

When the pipeline advances from one phase to another, Ralph Workflow emits a dedicated
transition separator **between** the previous phase's close banner and the next phase's
start banner. This is a distinct runtime surface, not just a renderer helper.

```
────────── <From Phase> → <To Phase>  (<routing context>) ──────────
```

| Field | Notes |
|-------|-------|
| `<From Phase>` | Human-readable label of the phase that just ended |
| `<To Phase>` | Human-readable label of the phase about to begin |
| `<routing context>` | Optional handoff hints such as `→ approved`, `→ needs changes`, or `final, skipping next` |

The live runner treats phase changes as a coordinated display contract with three rich
surfaces emitted in order:
1. the **phase-close banner** for the phase being left,
2. the **phase-transition banner** explaining the handoff,
3. the **phase-start banner** for the phase being entered.

This ordering is intentional. The close banner owns exit context and counters, the
transition banner owns routing semantics, and the start banner owns entry/iteration
context. Keeping these responsibilities separate prevents any one surface from becoming a
catch-all dump and makes regressions easier to detect in runner-level tests.

## `[phase-close]` Line

At every phase transition, a single `[phase-close]` line is appended to the transcript:

```
<ISO-TS> INFO META [phase-close] <glyph?> phase=<name> [Dev N/cap]? [Analysis N/cap]? <produced> exit=<trigger> (elapsed=Ns, content_blocks=N, thinking_blocks=N, tool_calls=N, errors=N)
```

| Field | Notes |
|-------|-------|
| `<glyph?>` | Milestone glyph (`◆` Unicode, `*` ASCII) for execution/review/fix phases only |
| `phase=<name>` | Name of the phase that just ended |
| `[Dev N/cap]` or `[Dev #N]`, `[Analysis N/cap]` or `[Analysis #N]` | Canonical iteration labels — only present when in a context that tracks them |
| `<produced>` | Human-readable artifact summary (e.g. `5 step(s), 2 risk(s)`, `result produced`, `sha=abc12345`) |
| `exit=<trigger>` | Why the phase ended — omitted when the exit trigger is unknown |
| Counter tuple | Phase-level activity metrics always present |

The `exit=<trigger>` values for phase-close lines:

| Value | Meaning |
|-------|---------|
| `produced` | Phase completed by producing its expected artifact |
| `completed` | Phase ended without producing a tracked artifact (e.g. a pass-through or skipped phase) |

### Canonical iteration labels

All display surfaces (phase-start banners, `[phase-close]` lines, completion panel) use
the same vocabulary for iteration context:

| Label | Meaning | Color |
|-------|---------|-------|
| `Dev N/cap` or `Dev #N` | Outer development cycle (1-indexed); shows cap when the progress cap is known | Bold sky-blue |
| `Analysis N/cap` or `Analysis #N` | Inner analysis loop iteration; shows cap when known | Purple |

## `[run-end]` Panel

At the end of every pipeline run, a `[run-end]` block reports the run summary.
The single default-mode layout renders the multi-line shape with grouped counters and
PR at the end (the same shape used at every terminal width):

```
<ISO-TS> MILESTONE META [run-end] ◆ Ralph Workflow run end
<ISO-TS> INFO     META [run-end] phase=<phase> elapsed=<elapsed>s exit=<exit_trigger> [dev_cycle=N]
<ISO-TS> INFO     META [run-end] agent_calls=N content_blocks=X thinking_blocks=Y tool_calls=Z errors=W
<ISO-TS> INFO     META [run-end] pr=<url>
```

`phase=complete` indicates success; `phase=failed` indicates the pipeline terminated
with an error.  `dev_cycle=N` appears only when at least one outer development cycle
has been completed (i.e., at least one commit was made during the run).

When the MCP server crashed and was automatically restarted at least once during the
run, an additional diagnostic line is appended to the `[run-end]` block:

```
<ISO-TS> INFO     META [run-end] mcp_restarts: N
```

This count is emitted by `PipelineSubscriber.record_mcp_restart()` every time
`McpSupervisor` detects an unexpected MCP server exit and successfully restarts it.
The count accumulates across all retry attempts in the run; if it equals
`McpRestartPolicy.max_restarts` (default 20), the run ended with `McpServerError`.

The `exit` field reports **why** the run ended:

| `exit_trigger` | Meaning |
|---------------|---------|
| `completed` | Pipeline reached its terminal success phase |
| `failed` | Pipeline hit a terminal failure condition |
| `interrupted` | User cancelled the run (SIGINT / keyboard interrupt) |
| `exited` | Pipeline exited for another reason |

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `RALPH_STREAMING_DEDUP` | `1` | Deduplicate identical consecutive streaming chunks |
| `RALPH_STREAMING_CHECKPOINTS` | `0` | Emit `content-checkpoint` lines during streaming |
| `RALPH_LONG_CONTENT_SUMMARY` | `1` | Append `⇳ summary:` after very long content blocks |
| `RALPH_LONG_CONTENT_AI_SUMMARY` | `0` | Append `⇳ ai-summary:` (requires LLM round-trip) |
| `NO_COLOR` | unset | Disable all ANSI colour output (any value) |
| `FORCE_COLOR` | unset | Force ANSI colour even when stdout is not a TTY (any value) |
| `COLUMNS` | unset | Override terminal width; positive integer |

## Related Modules

- `ralph.display` — public display API and `DisplayContext` factory
- `ralph.display.plain_renderer` — line-format renderer with cross-platform width-aware resize
- `ralph.display.long_content_summary` — streaming block summarisation
- `ralph.display.completion_summary` — `[run-end]` panel renderer with the default single-mode layout

## Related pages

- [Concepts](concepts.md) — transcript levels, categories, and verbosity
- [CLI Reference](cli.md) — verbosity flags and output control
- [Troubleshooting](troubleshooting.md) — reading error messages in the transcript
