# JSON Parser Module

This module provides streaming NDJSON parsers for various LLM agents (Claude, Codex, Gemini, OpenCode).

NDJSON (Newline-delimited JSON) is the streaming format used by agent CLIs to provide real-time output.

## Streaming Contract

The parsers enforce a strict **append-only rendering contract** for streaming output to prevent duplication bugs, even when providers send snapshot-style streams.

### Core Principles

1. **Streaming Input Contract (Best Effort)**: Providers *should* stream only newly generated text (true deltas), but many real-world streams are snapshot-style (full accumulated content). The parsers + `StreamingSession` are designed to be robust to both.

2. **Message Lifecycle**: `MessageStart` → (`ContentBlockStart` + deltas)* → `MessageStop`

3. **Deduplication Rule**: Content displayed during streaming is never re-displayed when the complete message arrives.

4. **State Reset**: Streaming state resets on `MessageStart`/Init events.

### Why This Matters

If a parser emits snapshot-style content when deltas are expected, it causes **exponential duplication bugs**. For example:
- First "delta": "Hello"
- Second "delta" (actually snapshot): "Hello World" → displays as "HelloHello World"
- Third "delta" (actually snapshot): "Hello World!" → displays as "HelloHello WorldHello World!"

### Validation

The `StreamingSession` validates incoming content:

1. **Size Threshold**: Deltas exceeding 200 characters trigger a warning (may indicate snapshot-as-delta).

2. **Pattern Detection**: Repeated large deltas (3+ occurrences) trigger a warning indicating likely snapshot-as-delta bug.

3. **Lifecycle Enforcement**: In debug builds, invalid state transitions panic.

## Event Lifecycle

```
┌──────────────────┐
│  MessageStart    │ → Reset state, prepare for new message
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ContentBlockStart│ → Mark beginning of content block
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  TextDelta*      │ → Accumulate deltas, display with in-place updates
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  MessageStop     │ → Finalize message, add final newline
└──────────────────┘
```

## Implementation Guide

When implementing a new parser:

1. **Create a `StreamingSession`** in your parser struct:
   ```rust
   streaming_session: RefCell<StreamingSession>
   ```

2. **Call lifecycle methods** at appropriate events:
   - `on_message_start()` when a new message begins
   - `on_text_delta()` for each text chunk
   - `on_message_stop()` when message completes

3. **Check deduplication** before displaying complete messages:
   ```rust
   if session.has_any_streamed_content() {
       return String::new(); // Skip display, already streamed
   }
   ```

4. **Use the return value** from delta methods to determine prefix display:
   ```rust
   let show_prefix = session.on_text_delta(index, delta);
   ```

## Files

- **`streaming_state.rs`** - Core `StreamingSession` implementation
- **`delta_display.rs`** - `DeltaRenderer` trait for consistent display
- **`deduplication.rs`** - Content deduplication logic
- **`claude.rs`** - Claude API parser (primary parser for CCS agents)
- **`codex.rs`** - Codex API parser
- **`gemini.rs`** - Gemini API parser
- **`opencode.rs`** - OpenCode API parser
- **`printer.rs`** - Output printing utilities (includes `TestPrinter` for tests)
- **`types.rs`** - Shared type definitions
- **`tests.rs`** - Comprehensive test suite

## Testing

Run tests with:
```bash
# All JSON parser tests
cargo xtask test -p ralph-workflow json_parser::tests

# Streaming-specific tests
cargo xtask test -p ralph-workflow streaming

# Snapshot-as-delta detection tests
cargo xtask test -p ralph-workflow test_snapshot_as_delta
```

## Streaming UX Guidelines

### Visual Pattern

The streaming display uses a **true append-only pattern** designed to work in real interactive terminals *and* in common CI/log consoles (including environments that strip/ignore ANSI cursor movement):

```text
[ccs-glm] Hello              ← First delta: prefix + accumulated content (NO newline)
 World                       ← Subsequent deltas: ONLY the new suffix (no prefix rewrite)
!                            ← More suffixes as they arrive
\n                           ← Completion: single newline finalizes the line
```

**Key properties:**
- No cursor movement during streaming deltas (`\x1b[1A` / `\x1b[2K` / `\r` are avoided)
- Wrapping is handled naturally by the terminal
- ANSI-stripping consoles remain readable (no per-delta newline waterfall)

### Prefix Display Strategy

In append-only mode the prefix is emitted once per logical streamed line; subsequent deltas emit only the new suffix.

### Content Sanitization

During streaming, content is sanitized for single-line display:
- Newlines replaced with spaces
- Multiple consecutive whitespace collapsed to single spaces
- Leading and trailing whitespace trimmed

This ensures clean visual output during streaming while preserving the original content in the accumulator.

### Known Limitations

1. **Very rapid deltas** may cause visual flicker on some terminals
2. **Standard ANSI sequences** are assumed - may not work on non-ANSI terminals
3. **Long lines** may wrap, affecting in-place update visual appearance
4. **Non-interactive output** (piped to file) defaults to `TerminalMode::None` and must not emit ANSI escape sequences. (If users force color via `CLICOLOR_FORCE`, output may include color escapes but still must not use cursor positioning.)

### Snapshot-as-Delta Auto-Repair

Some agents (e.g., GLM/CCS) send snapshot-style content instead of true deltas. The streaming session can detect and repair this:

1. **Detection**: Incoming "delta" starts with or contains previously accumulated content
2. **Extraction**: The truly new portion is extracted from the snapshot
3. **Accumulation**: Only the delta portion is accumulated, preventing duplication

This is transparent to parsers - they call `on_text_delta()` normally and the session handles repair internally.

## Debugging

In debug builds, lifecycle violations will panic with detailed error messages showing:
- Expected states
- Actual state
- File and line number of the violation

Warnings about large deltas are always emitted to stderr to help identify potential snapshot-as-delta bugs in production.
