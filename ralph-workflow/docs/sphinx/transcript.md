# Transcript and Display Reference

Ralph Workflow emits a structured, line-oriented transcript to stdout. Every line
has a fixed format that can be machine-parsed or read directly in a terminal.

## Display Architecture

`DisplayContext` (from `ralph.display`) is the **single source of truth** for all
display decisions: console, theme, terminal width, color policy, display mode, and
adaptive character limits.

### Dependency Injection Contract

Every renderer function requires a `display_context: DisplayContext` argument.
No renderer may construct its own `rich.Console`. Callers must create a
`DisplayContext` via `make_display_context()` before invoking any renderer.

```python
from ralph.display import make_display_context

ctx = make_display_context()          # uses terminal width, NO_COLOR, etc.
show_phase_start("planning", display_context=ctx)
```

### Width and Mode Precedence

| Priority | Source | Effect |
|----------|--------|--------|
| 1 | `RALPH_FORCE_NARROW=1\|true\|yes\|on` | Forces `compact` mode regardless of width |
| 2 | `force_width` argument to `make_display_context()` | Overrides all width detection |
| 3 | `COLUMNS=<N>` env var (positive int) | Overrides console.width |
| 4 | `console.width` (actual terminal width) | Default fallback |

### Mode Thresholds

| Mode | Width | Description |
|------|-------|-------------|
| `compact` | < 60 cols | Suppresses secondary columns, extra blank lines, descriptive rules |
| `medium` | 60–99 cols | Standard layout with moderate condensing |
| `wide` | ≥ 100 cols | Full multi-line layout with all sections |

### Color Precedence

| Priority | Env var | Effect |
|----------|---------|--------|
| 1 | `NO_COLOR=<any>` | Disables all ANSI color output |
| 2 | `FORCE_COLOR=<any>` | Forces ANSI color on (even when not a TTY) |

`NO_COLOR` takes precedence over `FORCE_COLOR` per standard CLI conventions.

### SIGWINCH Refresh (POSIX)

On POSIX systems (non-Windows), a `SIGWINCH` signal handler is installed at pipeline
start via `install_sigwinch_refresher()`. When the terminal is resized:

1. The signal handler calls `DisplayContext.refreshed()` which re-reads the current
   terminal width and recomputes mode and adaptive limits.
2. Renderers that buffer adaptive limits (e.g., `PlainLogRenderer`) refresh their context
   at phase boundaries via `flush_blocks()`.
3. The runner keeps its live display object and nested plain renderer synced with the
   refreshed context, so later banners and summaries render with the new mode.

The signal handler is installed only from the main thread ( `signal.signal` requires it).
On Windows, the refresher is a no-op.

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

Long agent outputs (e.g., code, plans, long prose) are emitted as streaming blocks
bounded by `content-start` / `content-end` tags. Within a block:

- `content-continue` lines carry the raw streamed chunks.
- `content-checkpoint` lines appear at configurable intervals to allow progressive
  display without buffering the entire block.

When a block ends, Ralph Workflow may append summary lines depending on configuration:

- `⇳ summary:` — static truncation summary (always present for very long blocks)
- `⇳ preview:` — first *N* characters of the block content
- `⇳ ai-summary:` — LLM-generated one-line summary (requires `RALPH_LONG_CONTENT_AI_SUMMARY`)

## `[phase-close]` Line

After each phase completes, a single `[phase-close]` line is appended to the transcript:

```
<ISO-TS> INFO META [phase-close] <glyph?> phase=<name> [Dev #N]? [Analysis N/cap]? <produced> (elapsed=Ns, content_blocks=N, thinking_blocks=N, tool_calls=N, errors=N)
```

| Field | Notes |
|-------|-------|
| `<glyph?>` | Milestone glyph (`◆` Unicode, `*` ASCII) for execution/review/fix phases only |
| `phase=<name>` | Name of the phase that just ended |
| `[Dev #N]`, `[Analysis N/cap]`, `[Fixer #N]`, `[Budget: N left]` | Canonical iteration labels — only present when in a context that tracks them |
| `<produced>` | Human-readable artifact summary (e.g. `plan: 5 step(s), 2 risk(s)`) |
| Counter tuple | Phase-level activity metrics always present |

### Canonical iteration labels

All display surfaces (phase-start banners, `[phase-close]` lines, completion panel) use
the same vocabulary for iteration context:

| Label | Meaning | Color |
|-------|---------|-------|
| `Dev #N` | Outer development cycle (1-indexed) | Bold sky-blue |
| `Analysis N/cap` | Inner analysis cycle with cap, or `Analysis #N` without cap | Purple |
| `Fixer #N` | Fixer iteration within an analysis→fix loop | Vermillion |
| `Budget: N left` | Remaining budget from an active policy counter | Bold orange |

## `[run-end]` Panel

At the end of every pipeline run, a `[run-end]` block reports the run summary.
The format adapts to the display mode:

**Compact mode** (`< 60` cols): abbreviated 3-line summary:
```
<ISO-TS> MILESTONE META [run-end] <phase> | <elapsed>s | <exit_trigger>
<ISO-TS> INFO     META [run-end] agent=N content=X thinking=Y tools=Z errors=W
<ISO-TS> INFO     META [run-end] pr=<url>
```

**Wide mode** (`>= 100` cols): multi-line with grouped counters and PR at end:
```
<ISO-TS> MILESTONE META [run-end] ◆ Ralph Workflow run end
<ISO-TS> INFO     META [run-end] phase=<phase> elapsed=<elapsed>s exit=<exit_trigger>
<ISO-TS> INFO     META [run-end] agent_calls=N content_blocks=X thinking_blocks=Y tool_calls=Z errors=W
<ISO-TS> INFO     META [run-end] pr=<url>
```

`phase=complete` indicates success; `phase=failed` indicates the pipeline terminated
with an error.

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
| `RALPH_FORCE_NARROW` | unset | Force compact mode display; set to `1`, `true`, `yes`, or `on` |
| `COLUMNS` | unset | Override terminal width; positive integer |

## Related Modules

- `ralph.display` — public display API and `DisplayContext` factory
- `ralph.display.plain_renderer` — line-format renderer with SIGWINCH-aware resize
- `ralph.display.long_content_summary` — streaming block summarisation
- `ralph.display.completion_summary` — `[run-end]` panel renderer with mode-adaptive layout

## Related pages

- [Concepts](concepts.md) — transcript levels, categories, and verbosity
- [CLI Reference](cli.md) — verbosity flags and output control
- [Troubleshooting](troubleshooting.md) — reading error messages in the transcript
