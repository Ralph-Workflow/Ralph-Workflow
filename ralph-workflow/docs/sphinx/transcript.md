# Transcript and Display Reference

Ralph Workflow emits a structured, line-oriented transcript to stdout. Every line
has a fixed format that can be machine-parsed or read directly in a terminal.

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
| `MILESTONE` | Major phase transition (planning, development, review, fix) |

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

- `↳ summary:` — static truncation summary (always present for very long blocks)
- `↳ preview:` — first *N* characters of the block content
- `↳ ai-summary:` — LLM-generated one-line summary (requires `RALPH_LONG_CONTENT_AI_SUMMARY`)

## `[run-end]` Panel

At the end of every pipeline run, a `[run-end]` block reports:

```
MILESTONE META [run-end] ◆ Ralph Workflow run end
INFO      META [run-end] phase=complete
INFO      META [run-end] elapsed=42.3s
INFO      META [run-end] content_blocks=12
INFO      META [run-end] thinking_blocks=4
INFO      META [run-end] tool_calls=28
INFO      META [run-end] errors=0
INFO      META [run-end] agent_calls=7
```

`phase=complete` indicates success; `phase=failed` indicates the pipeline terminated
with an error.

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `RALPH_STREAMING_DEDUP` | `1` | Deduplicate identical consecutive streaming chunks |
| `RALPH_STREAMING_CHECKPOINTS` | `0` | Emit `content-checkpoint` lines during streaming |
| `RALPH_LONG_CONTENT_SUMMARY` | `1` | Append `↳ summary:` after very long content blocks |
| `RALPH_LONG_CONTENT_AI_SUMMARY` | `0` | Append `↳ ai-summary:` (requires LLM round-trip) |
| `NO_COLOR` | unset | Disable all ANSI colour output |
| `FORCE_COLOR` | unset | Force ANSI colour even when stdout is not a TTY |

## Related Modules

- `ralph.display` — public display API
- `ralph.display.plain_renderer` — line-format renderer
- `ralph.display.long_content_summary` — streaming block summarisation
- `ralph.display.completion_summary` — `[run-end]` panel renderer
