# RFC-003: AI Agent Streaming Architecture Hardening

**RFC Number**: RFC-003
**Title**: AI Agent Streaming Architecture Hardening
**Status**: Maintained (post-implementation fixes)
**Author**: Architecture Analysis
**Created**: 2026-01-16

> NOTE: RFCs are historical documents. This RFC may not reflect current behavior.
> For up-to-date architecture docs, see `../architecture/event-loop-and-reducers.md` and `../architecture/effect-system.md`.

---

## Abstract

This RFC documents the current streaming architecture for AI agent output in Ralph and proposes a framework for continuous hardening. The streaming system handles real-time NDJSON parsing, multiline cursor-based terminal rendering, and state management across multiple agent types (Claude, Codex, Gemini, OpenCode). This RFC identifies architectural strengths, known edge cases, and establishes principles for ongoing improvement.

---

## Motivation

The streaming system is a critical path for user experience—it's the primary way users observe AI agent activity. Current implementation is production-ready but has accumulated technical debt and edge case handling that would benefit from systematic review and hardening.

### Why This RFC Exists

1. **Document Tribal Knowledge**: The streaming system has sophisticated edge case handling (snapshot-as-delta detection, GLM protocol quirks) that isn't fully documented
2. **Establish Hardening Framework**: Define principles for evaluating and improving streaming reliability
3. **Enable Continuous Improvement**: Create a living document that guides incremental enhancements
4. **Prevent Regression**: Codify invariants that must be maintained across changes

### Current Pain Points

| Issue | User Impact | Frequency |
|-------|-------------|-----------|
| Escape sequences leak to files when piped | Corrupted log output | Common |
| Long lines wrap and break in-place updates | Visual glitches | Occasional |
| Warnings print unconditionally to stderr | Noisy production output | With GLM agents |
| No graceful degradation for non-ANSI terminals | Broken rendering | Rare |

---

## Current Architecture

### Three-Layer Design

```
┌─────────────────────────────────────────────────────────────┐
│                    Execution Layer                          │
│  prompt.rs: spawn_agent_process(), stream_agent_output()    │
│  - StreamingLineReader with 1KB buffer (low latency)        │
│  - Routes to appropriate parser based on JsonParserType     │
│  - Stderr collected in separate thread (512KB max)          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                     Parser Layer                            │
│  claude.rs, codex.rs, gemini.rs, opencode.rs                │
│  - NDJSON deserialization via serde                         │
│  - StreamingSession for state management                    │
│  - Event classification (delta/complete/control/error)      │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Rendering Layer                          │
│  delta_display.rs: DeltaRenderer trait, TextDeltaRenderer   │
│  - Escape sequences for in-place updates                    │
│  - Content sanitization (newlines → spaces)                 │
│  - Prefix formatting with colors                            │
└─────────────────────────────────────────────────────────────┘
```

### Key Files

| File | Lines | Responsibility |
|------|-------|----------------|
| `streaming_state.rs` | ~1,460 | Unified state tracking, snapshot detection, deduplication |
| `delta_display.rs` | ~790 | Terminal rendering, escape sequences, sanitization |
| `claude.rs` | ~800 | Claude protocol parsing, primary parser |
| `prompt.rs` | ~530 | Process spawning, output routing, StreamingLineReader |
| `health.rs` | ~900 | Parser health metrics, quality monitoring |

### Multiline Cursor Pattern

The renderer uses the industry-standard multiline cursor pattern (Rich, Ink, Bubble Tea):

```
[agent] Hello\n\x1b[1A           ← First: content + newline + cursor up
\x1b[2K\r[agent] Hello World\n\x1b[1A  ← Update: clear + rewrite + newline + cursor up
[agent] Hello World!\n\x1b[1B\n       ← Complete: cursor down + newline
```

**Escape Sequence Reference**:

| Sequence | Name | Purpose |
|----------|------|---------|
| `\x1b[2K` | Clear Line | Erase entire line (not just to cursor) |
| `\r` | Carriage Return | Move cursor to column 0 |
| `\x1b[1A` | Cursor Up | Move cursor up 1 line |
| `\x1b[1B` | Cursor Down | Move cursor down 1 line |
| `\n` | Newline | Flush buffer, move to next line |

**Why This Pattern**:
- Newline forces immediate terminal buffer flush
- Cursor repositioning enables reliable in-place rewrites
- Works across terminal emulators with ANSI support

### Streaming State Machine

```
                    ┌──────────┐
                    │   Idle   │◄────────────────────┐
                    └────┬─────┘                     │
                         │ on_message_start()        │
                         ▼                           │
                    ┌──────────┐                     │
              ┌────►│Streaming │◄────┐               │
              │     └────┬─────┘     │               │
              │          │           │               │
    on_text_delta()      │     on_thinking_delta()   │
              │          │           │               │
              └──────────┴───────────┘               │
                         │ on_message_stop()         │
                         ▼                           │
                    ┌──────────┐                     │
                    │Finalized │─────────────────────┘
                    └──────────┘   (next message_start)
```

### Delta Contract

**Invariant**: Every streaming event contains only newly generated text (delta), never full accumulated content (snapshot).

**Violation Detection** (`streaming_state.rs:443-534`):
1. Size threshold: Deltas > 200 chars trigger warning
2. Content matching: If incoming text starts with/contains accumulated, it's a snapshot
3. Pattern detection: 3+ large deltas indicates systematic snapshot-as-delta bug

**Auto-Repair**: When snapshot detected, extract only the truly new portion to prevent duplication.

---

## Known Issues & Current Mitigations

### Issue 1: Snapshot-as-Delta Bug (GLM/ccs-glm)

**Problem**: Some agents send full accumulated content as "delta" instead of incremental chunks.

**Impact**: Exponential content duplication:
```
Delta 1: "Hello"           → Display: "Hello"
Delta 2: "Hello World"     → Display: "HelloHello World" (BUG!)
Delta 3: "Hello World!"    → Display: "HelloHello WorldHello World!" (WORSE!)
```

**Current Mitigation** (`streaming_state.rs:674-778`):
- `is_likely_snapshot()`: Detects if incoming text contains previous accumulated content
- `get_delta_from_snapshot()`: Extracts only the new portion
- Fuzzy matching for >85% content overlap

**Gaps**:
- False positives possible when genuine large delta legitimately starts with previous content
- No metrics on how often auto-repair triggers
- Warnings unconditionally printed to stderr

### Issue 2: Repeated MessageStart During Streaming

**Problem**: GLM sends `MessageStart` mid-stream, resetting state and causing prefix spam.

**Current Mitigation** (`streaming_state.rs:235-273`):
- Detect `Streaming` → `MessageStart` transition
- Preserve `output_started_for_key` to prevent re-displaying prefix
- Clear accumulated content but maintain output tracking

**Gaps**:
- Warning always printed regardless of verbosity level
- No configurable behavior for handling protocol violations

### Issue 3: Duplicate Final Message Display

**Problem**: After streaming deltas complete, the "Assistant" event re-displays same content.

**Current Mitigation** (`claude.rs:281-389`):
- Track message ID via `displayed_final_messages`
- Check `is_duplicate_final_message()` before display
- Fallback to `has_any_streamed_content()` when ID unavailable

**Gaps**:
- Fallback is brittle—any streamed content skips entire message
- Tool use events may still duplicate in edge cases

### Issue 4: Terminal Compatibility

**Problem**: Escape sequences fail or leak in various scenarios:
- Non-ANSI terminals (e.g., `TERM=dumb`)
- Piped output (`ralph | tee log.txt`)
- Very long lines (wrap affects cursor positioning)

**Current Mitigation**: None explicit—assumes ANSI terminal.

**Gaps**:
- No TTY detection
- No graceful degradation
- No line length truncation during streaming

### Issue 5: Content Block Transitions

**Problem**: Without proper finalization, content from different blocks concatenates ("glued text").

**Current Mitigation** (`streaming_state.rs:334-386`):
- `on_content_block_start()` finalizes previous block
- Only clears accumulated when transitioning to different index
- `ContentBlockState` tracks current block and output status

**Gaps**:
- Limited test coverage for rapid index switches
- Edge case: repeated `ContentBlockStart` for same index

---

## Architectural Principles

### Principle 1: Defensive Parsing

> Assume agents will violate the streaming contract. Detect and repair rather than crash.

**Application**:
- Always validate delta size and content patterns
- Auto-repair snapshot-as-delta violations
- Log violations for debugging but continue gracefully

### Principle 2: Progressive Enhancement

> Support the best experience on capable terminals while degrading gracefully.

**Application**:
- Detect terminal capabilities (TTY, ANSI support)
- Provide fallback rendering for limited terminals
- Never corrupt output in non-TTY scenarios

### Principle 3: Observable State

> Make streaming state inspectable for debugging and monitoring.

**Application**:
- Expose `StreamingQualityMetrics` after each run
- Track repair counts, large delta frequencies
- Enable verbose logging conditionally

### Principle 4: Single Source of Truth

> All parsers use `StreamingSession` for state—no parser-specific tracking.

**Application**:
- `StreamingSession` is the only authority on streaming state
- Parsers call lifecycle methods, don't maintain parallel state
- Deduplication logic centralized in session

### Principle 5: Separation of Concerns

> Parsing, state management, and rendering are independent layers.

**Application**:
- Parsers handle protocol deserialization only
- `StreamingSession` handles state transitions only
- `DeltaRenderer` handles terminal output only

---

## Areas for Investigation

This section captures areas that warrant exploration. Each area may spawn specific implementation work or may be deprioritized based on findings.

### Area 1: Terminal Mode Detection

**Question**: How should Ralph detect terminal capabilities and adapt rendering?

**Considerations**:
- `std::io::IsTerminal` (Rust 1.70+) for TTY detection
- `TERM` environment variable parsing
- `NO_COLOR` / `CLICOLOR` compliance (per RFC-002)
- Interaction with `--no-ansi` flag concept

**Exploration Tasks**:
- [x] Survey how production CLIs (gh, cargo) handle terminal detection
- [x] Identify minimum viable terminal mode enum: `Full | Basic | None`
- [x] Prototype `TerminalMode` threading through parser → renderer

### Area 2: Conditional Warning Emission

**Question**: How should streaming warnings integrate with the logging system?

**Considerations**:
- Current `eprintln!` is not verbosity-aware
- Warnings valuable for debugging but noisy in production
- May need separate "streaming diagnostics" verbosity

**Exploration Tasks**:
- [x] Audit all `eprintln!` in `streaming_state.rs` (lines 241, 460, 472, 494)
- [x] Design integration with existing `Logger` or `tracing`
- [x] Define verbosity thresholds for different warning types

**Implementation (2026-01-16)**:
- Added `verbose_warnings: bool` field to `StreamingSession`
- Added `with_verbose_warnings()` builder method
- All four `eprintln!` warnings now conditional on `verbose_warnings`
- Parsers enable `verbose_warnings` only when `Verbosity::Debug` (level 4)
- Added comprehensive test coverage for conditional warning behavior
- Default behavior: warnings suppressed (production-friendly)
- Debug mode: warnings enabled for debugging GLM protocol issues

### Area 3: Streaming Metrics Enhancement

**Question**: What metrics would help diagnose streaming issues in production?

**Current State**: `StreamingQualityMetrics` has basic size statistics.

**Potential Additions**:
- `snapshot_repairs_count`: How often auto-repair triggered
- `large_delta_count`: How many deltas exceeded threshold
- `protocol_violations`: MessageStart-during-Streaming count
- `content_hash`: For deduplication debugging

**Exploration Tasks**:
- [x] Define metric schema that balances detail vs overhead
- [x] Prototype collection points in `StreamingSession`
- [x] Design output format (JSON for machine, text for human)

**Implementation (2026-01-16)**:
- Added three new metrics to `StreamingQualityMetrics`:
  - `snapshot_repairs_count`: Tracks auto-repair triggers for snapshot-as-delta bugs
  - `large_delta_count`: Tracks deltas exceeding `SNAPSHOT_THRESHOLD` (200 chars)
  - `protocol_violations`: Tracks `MessageStart` during `Streaming` state transitions
- Updated `get_streaming_quality_metrics()` to include session-level metrics
- Increment counters in appropriate locations:
  - `snapshot_repairs_count` in snapshot repair success path
  - `large_delta_count` when delta size exceeds threshold
  - `protocol_violations` on mid-stream `MessageStart` detection
- Enhanced `format()` method to display new metrics with colors (yellow for warnings, red for violations)
- Added comprehensive test coverage for all new metrics

### Area 4: Line Length Management

**Question**: How should long lines be handled during streaming?

**Problem**: Lines longer than terminal width wrap, breaking cursor positioning.

**Options**:
1. Truncate displayed content to terminal width with ellipsis
2. Switch to scroll mode for long content
3. Do nothing (current behavior)

**Exploration Tasks**:
- [x] Measure terminal width reliably (`COLUMNS` env, `terminal_size` crate)
- [x] Prototype truncation in `sanitize_for_display()`
- [x] Test visual behavior with wrapped lines

**Implementation (2026-01-16)**:
- Added `TerminalMode::get_width()` method to detect terminal width from `COLUMNS` environment variable
- Fallback to 80 columns when `COLUMNS` not set or invalid
- Updated `sanitize_for_display()` to accept `terminal_mode` and `prefix` parameters
- Added `truncate_to_terminal_width()` function that:
  - Calculates available width (terminal_width - prefix_len - ANSI_overhead - ellipsis_len)
  - Truncates content to available width with "..." ellipsis indicator
  - Only applies in `TerminalMode::Full` (where cursor positioning is used)
- Updated all render functions (`render_first_delta`, `render_subsequent_delta`) to pass terminal mode and prefix
- Added comprehensive tests for truncation behavior in different terminal modes
- Tests verify: truncation in Full mode, no truncation in Basic/None modes, ellipsis display

### Area 5: Content Hash Deduplication

**Question**: Can content hashing improve deduplication when message IDs unavailable?

**Current State**: Fallback uses `has_any_streamed_content()` which is coarse.

**Potential Approach**:
- Hash accumulated content at `message_stop`
- Compare hash when final message arrives
- More precise than "any content was streamed"

**Exploration Tasks**:
- [x] Identify hash algorithm (xxhash for speed?)
- [x] Determine what content to hash (full? first N chars?)
- [x] Evaluate memory/performance overhead

**Implementation (2026-01-16)**:
- Used `std::collections::hash_map::DefaultHasher` (64-bit) for good distribution and performance
- Added `final_content_hash: Option<u64>` field to `StreamingSession`
- Implemented `compute_content_hash()` that:
  - Hashes ALL accumulated content across all content types and indices
  - Sorts keys for consistent hashing regardless of insertion order
  - Returns `None` when no content accumulated
- Implemented `is_duplicate_by_hash()` that:
  - Compares hash of input text content against streamed content hash
  - Only considers text content (ContentType::Text) for comparison
  - Returns `true` only when hashes match exactly
- Integrated into `ClaudeParser` to use hash-based deduplication as fallback when message ID unavailable
- Extracts text content from final message before deduplication check
- Falls back to `has_any_streamed_content()` if no text content available
- Added comprehensive test coverage for hash computation and duplicate detection

### Area 6: Prefix Debouncing

**Question**: Should prefix display frequency be configurable?

**Current State**: Prefix shown on every delta. `PrefixDebouncer` exists but is `#[cfg(test)]` only.

**Considerations**:
- Character-by-character streaming creates visual noise
- Some users prefer always seeing the prefix
- May need time-based and count-based thresholds

**Exploration Tasks**:
- [x] Gather user feedback on prefix repetition
- [ ] Enable `PrefixDebouncer` for production experimentation
- [ ] Design config surface (`streaming.prefix_debounce_ms`?)

**Status**: DEFERRED (P3 - Polish)

**Rationale**: As of 2026-01-16, no user complaints have been received regarding prefix repetition during streaming. The current behavior (showing prefix on every delta) is:
- Production-safe and predictable
- Consistent across all agents
- Provides clear feedback about which agent is generating output

The `PrefixDebouncer` implementation exists and is well-tested in `delta_display.rs:209-282`. When user feedback indicates prefix repetition is problematic, the following steps should be taken:
1. Add configuration option to `StreamingConfig` struct
2. Remove `#[cfg(test)]` from `PrefixDebouncer` and related implementations
3. Integrate debouncer into all parser render loops
4. Add integration tests for debounced behavior
5. Update user documentation with new configuration option

**Revisit Criteria**: Enable this feature if any of the following occur:
- User reports prefix repetition as confusing or distracting
- User feedback indicates visual noise during long streaming sessions
- Multi-agent streaming is implemented (where interleaved prefixes become critical)

### Area 7: Non-TTY Output Mode

**Question**: What should streaming look like when stdout is not a terminal?

**Current State**: Escape sequences leak to files.

**Options**:
1. Disable all in-place updates, use simple line output
2. Strip escape sequences from final output
3. Buffer and emit clean output at end

**Exploration Tasks**:
- [x] Define "clean" output format for non-TTY
- [x] Prototype `DeltaRenderer` variant without escapes
- [x] Test with common non-TTY scenarios (pipes, redirects, CI)

**Note**: This area was completed as part of Area 1 (Terminal Mode Detection). The `TerminalMode::None` mode provides clean output without escape sequences for non-TTY scenarios.

---

## Implementation Priorities

When addressing areas above, prioritize based on:

| Priority | Criteria |
|----------|----------|
| **P0** | Causes data corruption or misleading output |
| **P1** | Affects common user scenarios |
| **P2** | Affects edge cases or power users |
| **P3** | Polish and optimization |

### Current Priority Assessment

| Area | Priority | Rationale |
|------|----------|-----------|
| Terminal Mode Detection | P1 | Piped output is common; escape leakage is confusing |
| Conditional Warnings | P1 | Noisy stderr affects GLM users regularly |
| Streaming Metrics | P2 | Useful for debugging but not user-facing |
| Line Length Management | P2 | Affects occasional long responses |
| Content Hash Dedup | P2 | Current fallback works for most cases |
| Prefix Debouncing | P3 | Cosmetic; current behavior is acceptable |
| Non-TTY Output Mode | P1 | Same as terminal detection; common scenario |

---

## Testing Strategy

### Invariants to Test

1. **Delta Contract**: Accumulated content equals concatenation of all deltas
2. **No Duplication**: Final message display doesn't repeat streamed content
3. **State Reset**: New message clears previous message state
4. **Prefix Logic**: First delta shows prefix; subsequent don't (unless same-index restart)

### Edge Cases to Cover

```rust
// Snapshot-as-delta
session.on_text_delta(0, "Hello");
session.on_text_delta(0, "Hello World");  // Should extract " World"

// Repeated MessageStart
session.on_message_start();
session.on_text_delta(0, "A");
session.on_message_start();  // Mid-stream restart
session.on_text_delta(0, "B");  // Should NOT show prefix again

// Rapid index switches
session.on_content_block_start(0);
session.on_text_delta(0, "X");
session.on_content_block_start(1);
session.on_content_block_start(0);  // Back to 0
session.on_text_delta(0, "Y");  // What's the expected state?

// Very long single delta
session.on_text_delta(0, "x".repeat(10000));  // Warn? Truncate?

// Empty deltas
session.on_text_delta(0, "");
session.on_text_delta(0, "   ");
```

### Test Commands

```bash
# All streaming tests
cargo xtask test -p ralph-workflow streaming

# Snapshot detection tests
cargo xtask test -p ralph-workflow test_snapshot

# Delta display tests
cargo xtask test -p ralph-workflow delta_display

# Full parser integration
cargo xtask test -p ralph-workflow json_parser::tests
```

---

## Success Criteria

### Short-Term (This RFC)
- [x] Document current architecture accurately
- [x] Identify all known edge cases
- [x] Establish investigation areas for future work

### Medium-Term (Next Quarter)
- [x] Terminal mode detection implemented
- [x] Warnings conditional on verbosity
- [x] Non-TTY output clean (no escape leakage)
- [x] Streaming metrics available for debugging
- [x] Line length management for long content

### Long-Term (Ongoing)
- [ ] Zero streaming-related bug reports per release
- [x] Streaming metrics available for debugging (Area 3 completed 2026-01-16)
- [x] Edge cases for rapid index switching covered by tests (2026-01-16)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Regression in snapshot detection | Comprehensive test suite, golden tests |
| Performance impact from metrics | Lazy computation, opt-in detailed metrics |
| Breaking changes to `DeltaRenderer` trait | Versioned trait or feature flag |
| Terminal detection false positives | Conservative defaults, user override flag |

---

## Alternatives Considered

### Alternative 1: Replace Cursor Pattern with Scroll

Instead of in-place updates, always append new lines and let terminal scroll.

**Rejected**: Loses the "building up" visual effect that users expect from streaming.

### Alternative 2: Full TUI Framework

Use `ratatui` or similar for sophisticated rendering.

**Deferred**: Current escape sequence approach is simpler and sufficient. May revisit for complex multi-agent scenarios.

### Alternative 3: Structured Streaming Output

Emit structured events (JSON) and rely on external renderer.

**Rejected**: Breaks the CLI-first philosophy. May add as opt-in mode later.

---

## References

### Internal

| File | Description |
|------|-------------|
| `ralph-workflow/src/json_parser/streaming_state.rs` | Core state management |
| `ralph-workflow/src/json_parser/delta_display.rs` | Rendering implementation |
| `ralph-workflow/src/json_parser/README.md` | Module documentation |
| `ralph-workflow/src/json_parser/claude.rs` | Reference parser implementation |
| `ralph-workflow/src/pipeline/prompt.rs` | Process spawning and routing |

### External

| Resource | Relevance |
|----------|-----------|
| [Rich (Python)](https://rich.readthedocs.io/) | Reference for terminal rendering patterns |
| [Ink (React for CLI)](https://github.com/vadimdemedes/ink) | Similar cursor-based rendering |
| [Bubble Tea (Go)](https://github.com/charmbracelet/bubbletea) | Production CLI framework patterns |
| [ANSI Escape Codes](https://en.wikipedia.org/wiki/ANSI_escape_code) | Escape sequence reference |

---

## Open Questions

1. **Threshold Tuning**: Is 200 chars the right `SNAPSHOT_THRESHOLD`? Should it be configurable?

   **Resolution**: Deferred to P4 (future consideration). The 200-char threshold has proven effective in production. Configuration surface would be added if user feedback indicates need for tuning.

2. **Fuzzy Match Ratio**: Is 85% overlap the right threshold for fuzzy snapshot detection?

   **Resolution**: Deferred to P4 (future consideration). The 85% threshold provides good balance between false positives and missed detections. User feedback has not indicated issues with current behavior.

3. **Warning Aggregation**: Should warnings be collected and summarized at end rather than inline?

   **Resolution**: RESOLVED by Area 2 (Conditional Warning Emission). Warnings are now only emitted when `verbose_warnings` is enabled (debug mode), which eliminates the production noise concern that prompted this question. Inline warnings remain for debugging purposes.

4. **Multi-Agent Streaming**: If Ralph supports parallel agents, how does rendering interleave?

   **Resolution**: Deferred to future RFC on multi-agent architecture. This is out of scope for RFC-003 which focuses on single-agent streaming hardening.

5. **State Persistence**: Should streaming state survive process restart for checkpoint/resume?

   **Resolution**: Deferred to P5 (backlog item). Checkpoint/resume functionality would be a separate feature requiring significant architectural work. Current streaming state is ephemeral by design.

6. **Accessibility**: Should there be a "no animation" mode that disables in-place updates entirely?

   **Resolution**: RESOLVED by Area 1 (Terminal Mode Detection). The `TerminalMode::None` mode (triggered by non-TTY detection or `NO_COLOR=1`) already disables all in-place updates and provides simple line-by-line output. Users who find animations disorienting can use `ralph | cat` or set `NO_COLOR=1`.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-01-16 | Initial draft |
| 2026-01-16 | **Area 2 (Conditional Warning Emission) completed**: Added `verbose_warnings` field to `StreamingSession`, implemented `with_verbose_warnings()` builder, updated all four `eprintln!` calls to respect verbosity, added parser integration for `Verbosity::Debug` mode, added comprehensive tests |
| 2026-01-16 | **Area 1 (Terminal Mode Detection) completed**: Implemented terminal capability detection with three modes (Full, Basic, None), added environment variable support (`NO_COLOR`, `CLICOLOR`, `CLICOLOR_FORCE`, `TERM`), integrated with all parsers (Claude, Codex, Gemini, OpenCode), added comprehensive unit and integration tests for non-full terminal modes, verified clean output without escape sequences in non-TTY scenarios |
| 2026-01-16 | **Area 3 (Streaming Metrics Enhancement) completed**: Added `snapshot_repairs_count`, `large_delta_count`, and `protocol_violations` to `StreamingQualityMetrics`, updated `get_streaming_quality_metrics()` to include session-level metrics, enhanced `format()` to display new metrics with colors, added comprehensive tests |
| 2026-01-16 | **Area 4 (Line Length Management) completed**: Added `TerminalMode::get_width()` to detect terminal width from `COLUMNS` env var, implemented `truncate_to_terminal_width()` for content truncation with ellipsis, updated `sanitize_for_display()` to accept `terminal_mode` and `prefix` parameters, added truncation logic only in `TerminalMode::Full`, added comprehensive tests for different terminal modes |
| 2026-01-16 | **Area 5 (Content Hash Deduplication) completed**: Added `final_content_hash` field to `StreamingSession`, implemented `compute_content_hash()` using `DefaultHasher`, implemented `is_duplicate_by_hash()` for precise deduplication, integrated into `ClaudeParser` as fallback when message ID unavailable, added comprehensive test coverage |
| 2026-01-16 | **RFC-003 Implementation mostly completed**: All P0, P1, and P2 priority items implemented. Short-term goals (architecture documentation, edge case identification, investigation areas) completed. Medium-term goals (terminal mode detection, conditional warnings, non-TTY output, streaming metrics, line length management) completed. Area 6 (Prefix Debouncing) deferred as P3 polish item pending user feedback. |
| 2026-01-16 | **RFC-003 Documentation updates**: Updated Long-Term success criteria to check "Streaming metrics available for debugging" as complete. Resolved all 6 Open Questions with documented resolutions (2 resolved by Areas 1-2, 4 deferred to future RFCs/priorities). Documented Area 6 (Prefix Debouncing) deferral rationale with revisit criteria. |
| 2026-01-16 | **Bug Fix (Issue 5 - Content Block Transitions)**: Fixed bug where `output_started_for_key` and `delta_sizes` tracking sets were not cleared when transitioning between different content block indices. This caused inconsistent prefix display behavior when switching back to a previously used index. The fix ensures all per-index tracking (`accumulated`, `output_started_for_key`, `delta_sizes`, `key_order`) is cleared consistently. Additionally, fixed related bug where `on_content_block_start()` did not update `current_block` to the new index, causing index tracking to be lost when deltas were received via `on_thinking_delta()` (which doesn't update `current_block`). Added comprehensive test coverage for rapid index switching edge cases including `test_rapid_index_switch_with_clear()`, `test_delta_sizes_cleared_on_index_switch()`, `test_rapid_index_switch_with_thinking_content()`, and `test_output_started_for_key_cleared_across_all_content_types()`. |
| 2026-01-16 | **Enhancement: User-facing streaming metrics flag**: Added `--show-streaming-metrics` CLI flag to expose streaming quality metrics to users outside of debug mode. Previously, metrics were only shown in `Verbosity::Debug` (level 4). Users can now enable metrics display with `ralph --show-streaming-metrics` regardless of verbosity level. Updated all four parsers (Claude, Codex, Gemini, OpenCode) to respect the new flag. Documented `RALPH_STREAMING_SNAPSHOT_THRESHOLD` and `RALPH_STREAMING_FUZZY_MATCH_RATIO` environment variables in quick reference documentation. |
| 2026-01-16 | **Fix: Real-time streaming with StreamingLineReader**: Implemented `StreamingLineReader` in `prompt.rs` to replace the default `BufReader::lines()` pattern that was causing output to appear all at once instead of streaming character-by-character. The root cause was that `BufReader::lines()` blocks waiting for newlines, and when agents buffer their stdout (especially Codex), all output would appear at the end. The new `StreamingLineReader`:
  - Uses a smaller 1KB buffer (vs 8KB default) for lower latency
  - Implements `BufRead` trait for compatibility with existing parsers
  - Aggressively fills buffer until a newline is found (up to 8 read attempts)
  - Processes data immediately when newlines arrive, enabling true real-time streaming
  - Maintains the same API as `BufReader` for minimal code changes

  This fixes the user experience issue where Codex showed a blank screen for a long time before displaying all output at once. The fix applies to all parsers (Claude, Codex, Gemini, OpenCode) and ensures character-by-character streaming as intended. |
| 2026-01-16 | **Enhancement: True incremental NDJSON parsing**: Implemented `IncrementalNdjsonParser` in `json_parser/incremental_parser.rs` to enable true real-time streaming without waiting for newlines. The previous approach still used `reader.lines()` which blocks until a complete line is received. The new incremental parser:
  - Processes NDJSON events byte-by-byte, detecting complete JSON objects by tracking brace nesting depth
  - Yields complete JSON events immediately when the closing brace is detected, without waiting for newlines
  - Handles multi-line JSON, embedded strings, escaped quotes, and partial JSON spanning multiple reads
  - Integrated into all four parsers (Claude, Codex, Gemini, OpenCode) replacing `reader.lines()` with `fill_buf()`/`consume()` loop

  This achieves the ChatGPT-like real-time streaming experience where characters appear as they're generated, not all at once at the end. The improvement is especially noticeable for Codex and other agents that buffer their stdout. |
| 2026-01-16 | **Bug Fix: Debug output flush in all parsers**: Fixed critical bug where debug output (`[DEBUG]` prefix) was not flushed immediately, causing it to appear truncated or missing during streaming. The issue occurred because debug JSON was written with `writeln!` but not flushed, while the actual event output was flushed. This mismatch caused debug output to be buffered and potentially overwritten by subsequent event output. Added `writer.flush()?` after all debug `writeln!` calls in all four parsers (Claude, Codex, Gemini, OpenCode). Added comprehensive test coverage `test_all_parsers_flush_debug_output_immediately()` to verify debug output completeness. |
| 2026-01-16 | **Bug Fix: Line truncation with "..." in full terminal mode**: Fixed bug in `truncate_to_terminal_width()` where content was being truncated prematurely with "..." even when it fit within the terminal width. The root cause was `ANSI_ESCAPE_OVERHEAD = 20` which incorrectly reduced available width by 20 characters. ANSI escape sequences (e.g., `\x1b[2m...\x1b[0m`) don't consume visual terminal width, so the overhead should be 0. Changed `ANSI_ESCAPE_OVERHEAD` from 20 to 0, fixing the width calculation: `available_width = terminal_width - prefix_len - 0 - ellipsis_len`. Updated test `test_sanitize_truncates_long_content_in_full_mode` to use a string that actually exceeds terminal width (80 - 8 - 3 = 69 chars available). This fix prevents false truncation of content that would otherwise fit within the terminal. |
| 2026-01-16 | **Bug Fix: Removed streaming truncation that caused cut-off output**: Fixed two critical issues causing output to be cut off with "..." during streaming:
  1. **Terminal width truncation during streaming**: Removed `truncate_to_terminal_width()` call from `sanitize_for_display()` in `delta_display.rs`. Terminal width truncation was being applied to EVERY delta during streaming, causing visible "..." cut-offs as content accumulated. The `sanitize_for_display()` function now only sanitizes whitespace (newlines → spaces, collapse multiple spaces, trim) without any truncation.
  2. **Tool input truncation in Normal/Verbose modes**: Changed `TOOL_INPUT` truncation limits from 200/500 chars to unlimited (999,999) in Normal/Verbose modes in `truncation.rs`. Tool input provides crucial context for understanding agent behavior, and truncating it made it hard to understand what the agent was doing. Tool input is now only truncated in Quiet mode (40 chars).

  **Root causes**:
  - Issue 1: `sanitize_for_display()` applied terminal width truncation in Full mode (lines 112-116), causing every delta to be truncated to ~65 characters during streaming
  - Issue 2: `truncate_text()` with verbosity-based limits cut off tool input like "todos=[6 items" at 200 chars in Normal mode

  **Files modified**:
  - `ralph-workflow/src/json_parser/delta_display.rs`: Removed terminal width truncation from `sanitize_for_display()`, removed unused `truncate_to_terminal_width()` function
  - `ralph-workflow/src/config/truncation.rs`: Set `TOOL_INPUT` to unlimited (999,999) in Normal/Verbose modes
  - `ralph-workflow/src/json_parser/terminal.rs`: Removed unused `get_width()` function and its tests
  - `ralph-workflow/src/config/mod.rs`: Updated `test_truncate_limits` to reflect new limits
  - `ralph-workflow/src/json_parser/tests.rs`: Updated `test_streaming_very_long_text` to expect no truncation during streaming

  **Impact**: Streaming deltas now show full content without mid-stream truncation. Long tool input (like complex JSON parameters) is now fully visible in Normal/Verbose modes, making it much easier to understand agent behavior. |
| 2026-01-16 | **Enhancement: Render deduplication to prevent visual repetition**: Fixed visual repetition bug where identical accumulated content was rendered multiple times, creating a "stuttering" effect. The issue occurred when agents sent empty or no-op deltas that didn't change the accumulated content—rendering would still occur, producing duplicate output. Added `last_rendered: HashMap` field to `StreamingSession` to track the last rendered content for each `(ContentType, index)` key. Implemented `should_skip_render()` method that returns `true` when accumulated content is unchanged, preventing redundant rendering. Implemented `mark_rendered()` method to update tracking after successful rendering. Integrated deduplication into `ClaudeParser`'s `handle_content_block_delta()` and `handle_text_delta()` methods to check for unchanged content before calling renderer. Added comprehensive test coverage: `test_identical_accumulated_content_skips_rendering()`, `test_streaming_session_should_skip_render()`, `test_streaming_session_mark_rendered()`, `test_message_start_clears_last_rendered()`. The fix ensures smooth in-place updates without visual repetition, especially important when agents send no-op or empty deltas during streaming.

  **Files modified**:
  - `ralph-workflow/src/json_parser/streaming_state.rs`: Added `last_rendered` HashMap field, `should_skip_render()` method, `mark_rendered()` method, cleared in `on_message_start()` and `on_content_block_start()` when transitioning to different index
  - `ralph-workflow/src/json_parser/claude.rs`: Updated `handle_content_block_delta()` and `handle_text_delta()` to check `should_skip_render()` and call `mark_rendered()`
  - `ralph-workflow/src/json_parser/tests.rs`: Added 4 new tests for deduplication behavior

  **Impact**: Streaming output now updates smoothly in-place without visual "stuttering" or repetition. Empty deltas and no-op events no longer cause redundant rendering, providing a cleaner user experience that matches ChatGPT-like real-time streaming expectations. |

---

*This RFC is a living document. Update as investigation areas are explored and implementations complete.*
