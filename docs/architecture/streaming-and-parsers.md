# Streaming and NDJSON Parser Architecture

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document describes how Ralph parses and renders streaming NDJSON output from agent CLIs (Claude, Codex, Gemini, OpenCode). The goals are:

- real-time output when possible
- correct, non-duplicated output across provider quirks
- safe output in non-interactive environments (CI, pipes, logs)

## High-Level Data Flow

At a high level:

1. A byte stream arrives from an agent CLI.
2. `IncrementalNdjsonParser` extracts complete JSON objects without waiting for newline termination.
3. A provider-specific parser deserializes each JSON object into an event and routes it to handlers.
4. Streaming lifecycle + accumulation is managed by `StreamingSession`.
5. Rendering depends on terminal capability (`TerminalMode`).

## TerminalMode (What Can Be Rendered)

Streaming rendering is explicitly capability-gated:

- `TerminalMode::Full`: may use ANSI cursor positioning for in-place updates.
- `TerminalMode::Basic`: may use colors, but must not use cursor positioning.
- `TerminalMode::None`: must not emit ANSI escape sequences.

This is why streaming output is implemented in two layers:

- Per-delta renderers (Full mode): show in-place updates.
- Completion-boundary flush (Basic/None): accumulate deltas silently, then print once when the message/item completes.

If you add a new streaming path, ensure you also add a completion-boundary flush path for `TerminalMode::Basic` and `TerminalMode::None`.

## The Delta Contract (Non-Negotiable)

Streaming text events are expected to be true deltas (only newly generated text), not snapshots (full accumulated content).

If a provider sends snapshots as deltas and we render them naively, output duplicates exponentially. To prevent that, deltas must flow through `StreamingSession` so the contract can be enforced.

Current enforcement (see `ralph-workflow/src/json_parser/streaming_state/`):

- snapshot-as-delta detection + auto-repair (extract only the new suffix when a strong overlap is detected)
- exact-duplicate / resend glitch suppression
- per-session metrics for debugging and hardening

Rule of thumb: do not bypass `StreamingSession` for streamed text/thinking/tool-input updates.

## Deduplication Between Streaming and Final Messages

Most providers emit both:

- many streaming deltas, and later
- a final "assistant" / "completed" message containing the full content

Ralph enforces: content shown during streaming must not be re-shown when the final message arrives.

Dedup is implemented using a mix of:

- message id suppression (when available)
- normalized content hashing (for providers that resend content without stable ids)
- per-(content-type, block-key) rendered-content tracking

When adding a new event type that represents a completion boundary, ensure it triggers the correct dedup and clears any per-key state needed to prevent double-flush.

## Streaming Lifecycle (Messages and Content Blocks)

Providers often structure output into message lifecycles and content blocks (for example: separate indices for text vs tool-use vs thinking). The session tracks this explicitly:

- message start: reset per-message tracking
- content block start: begin tracking a new block key (must not drop previously accumulated blocks)
- deltas: update accumulation and (in Full mode) render
- message stop / completion: flush accumulated content (in Basic/None) and finalize cursor state (in Full)

Key invariant: do not clear prior accumulated blocks when a new content block starts; non-TTY flush needs access to all blocks.

## Where This Lives in Code

- Incremental object extraction: `ralph-workflow/src/json_parser/incremental_parser.rs`
- Terminal capability detection: `ralph-workflow/src/json_parser/terminal.rs`
- Streaming session + contract enforcement: `ralph-workflow/src/json_parser/streaming_state.rs` and `ralph-workflow/src/json_parser/streaming_state/`
- Deduplication algorithms + thresholds: `ralph-workflow/src/json_parser/deduplication.rs` and `ralph-workflow/src/json_parser/deduplication/`
- Full-mode delta rendering (ANSI cursor strategy): `ralph-workflow/src/json_parser/delta_display.rs` and `ralph-workflow/src/json_parser/delta_display/`
- Provider parsers:
  - Claude: `ralph-workflow/src/json_parser/claude/`
  - Codex: `ralph-workflow/src/json_parser/codex/`
  - Gemini: `ralph-workflow/src/json_parser/gemini/`
  - OpenCode: `ralph-workflow/src/json_parser/opencode/`

## Testing Guidance

Most regressions in this area are output-duplication or terminal-mode correctness bugs.

- Prefer tests at the `StreamingSession` level for contract enforcement (snapshot repair, duplicate suppression, per-key behavior).
- Add at least one end-to-end parser test for any new provider event type that changes lifecycle or completion boundaries.
- Ensure `TerminalMode::None` paths never emit ANSI sequences.

## Historical Notes

The RFCs in `docs/RFC/` are kept for historical interest only. Do not treat them as canonical.

- `../RFC/RFC-003-streaming-architecture-hardening.md` (historical notes and incident-driven hardening)
