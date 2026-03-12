//! Edge case and complex scenario tests.
//!
//! These tests cover:
//! - Intentional repetition preservation (user wants "echo echo echo")
//! - Multi-block content handling
//! - Real log file regression tests
//! - Complex interleaving scenarios
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../../INTEGRATION_TESTS.md](../../../INTEGRATION_TESTS.md)**.

use super::{create_parser_with_vterm, parse_events};
use crate::test_timeout::with_default_timeout;
use ralph_workflow::workspace::MemoryWorkspace;
use std::io::{BufReader, Cursor};

// =============================================================================
// Intentional Repetition Tests
// =============================================================================

/// Test that intentional repetition is preserved.
///
/// This verifies that when repetition is part of the actual message content,
/// the deduplication system preserves it rather than filtering it out.
#[test]
fn test_intentional_repetition_preserved() {
    with_default_timeout(|| {
        let events = [
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[]}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"echo"}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" "}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"echo"}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" "}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"echo"}}}"#,
            r#"{"type":"stream_event","event":{"type":"message_stop"}}"#,
        ];

        let vterm = parse_events(&events);
        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // "echo" should appear multiple times (intentional repetition)
        let count = vterm_ref.count_visible_pattern("echo");
        assert!(
            count >= 2,
            "Intentional repetition should be preserved. 'echo' appears only {count} times. Output: {visible}"
        );
    });
}

/// Test alternating pattern is not incorrectly deduplicated.
///
/// This verifies that when alternating patterns like "Ping Pong Ping Pong"
/// appear in content, they are preserved and not filtered out.
#[test]
fn test_alternating_pattern_preserved() {
    with_default_timeout(|| {
        let events = [
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[]}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Ping"}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Pong"}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Ping"}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Pong"}}}"#,
            r#"{"type":"stream_event","event":{"type":"message_stop"}}"#,
        ];

        let vterm = parse_events(&events);
        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // Should contain "PingPongPingPong"
        assert!(
            visible.contains("PingPongPingPong"),
            "Alternating pattern should be preserved. Got: {visible}"
        );
    });
}

/// Parse a real captured log and ensure visible output is not corrupted.
///
/// This is a regression test for thinking-delta rendering bugs where late thinking
/// overwrote streamed text output, producing repeated "[ccs/...] Thinking:" lines.
#[test]
fn test_example_log_renders_without_thinking_corruption() {
    with_default_timeout(|| {
        let log = include_str!("../artifacts/example_log.log");

        let (parser, vterm) = create_parser_with_vterm();
        let workspace = MemoryWorkspace::new_test();

        let cursor = Cursor::new(log);
        let reader = BufReader::new(cursor);
        parser
            .parse_stream(reader, &workspace)
            .expect("parse_stream should succeed");

        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // Sanity: the log contained expected streamed assistant text.
        //
        // NOTE: With append-only streaming, the phrase may be split across multiple writes,
        // so we assert on *visible* output rather than individual write history entries.
        assert!(
            visible.contains("Need read complete file contents"),
            "Expected streamed assistant text missing from visible output. Visible output: {visible}"
        );
        assert!(
            visible.contains("Not allowed to explore beyond direct"),
            "Expected streamed assistant text missing from visible output. Visible output: {visible}"
        );

        // Regression: ensure system status output doesn't corrupt the streamed line.
        // Check for known corruption artifacts where system status text merges with streamed content.
        // The specific pattern "statusead" was observed when "[status]" overwrote "Need read" → "statusead".
        let corruption_patterns = ["statusead", "statused", "statusomplete"];
        for pattern in &corruption_patterns {
            assert!(
                !visible.contains(pattern),
                "System output corrupted the streamed line (found '{pattern}'). Output: {visible}"
            );
        }

        // Regression: ensure thinking prefix never appears on the same line as critical text.
        for line in visible.lines() {
            if line.contains("Need read complete file contents") {
                assert!(
                    !line.contains("Thinking:"),
                    "Thinking corrupted text line. Line: {line:?}\nOutput: {visible}"
                );
            }
        }

        // Note: This real log includes repeated system/user lines (e.g., `status`, `compact_boundary`).
        // We only assert that streamed content is present and that status output doesn't corrupt it.
    });
}

// =============================================================================
// Multi-Block Tests
// =============================================================================

/// Test content within a single streaming session accumulates correctly.
///
/// This verifies that when multiple deltas arrive in the same content block,
/// they build up content correctly without duplication.
#[test]
fn test_multiple_deltas_accumulate_correctly() {
    with_default_timeout(|| {
        let events = [
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[]}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"First"}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" Second"}}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" Third"}}}"#,
            r#"{"type":"stream_event","event":{"type":"message_stop"}}"#,
        ];

        let vterm = parse_events(&events);
        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // All content should be accumulated
        assert!(
            visible.contains("First") && visible.contains("Second") && visible.contains("Third"),
            "All deltas should accumulate. Got: {visible:?}"
        );

        // No duplicate lines
        assert!(
            !vterm_ref.has_duplicate_lines(),
            "No duplicate lines from accumulated content"
        );
    });
}

// =============================================================================
// Real Log File Test
// =============================================================================

/// Test GLM bug: assistant event arrives AFTER `MessageStart` but BEFORE streaming deltas.
///
/// This reproduces a specific GLM bug where:
/// 1. `MessageStart` arrives (setting the `message_id`)
/// 2. `ContentBlockStart` arrives with `tool_use` but no input
/// 3. Assistant event arrives with full content (BEFORE any `ToolUseDelta`)
/// 4. `ToolUseDelta` arrives after the assistant event
///
/// The bug: When the assistant event arrives BEFORE any streaming deltas,
/// `has_any_streamed_content()` returns false, so the assistant event is rendered.
/// Then when the `ToolUseDelta` arrives, it's ALSO rendered, causing duplication.
#[test]
fn test_glm_assistant_event_before_streaming_deltas() {
    with_default_timeout(|| {
        let events = [
            // MessageStart with same message_id as assistant event
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_glm_bug_early","type":"message","role":"assistant","model":"glm-4.7","content":[]}}}"#,
            // ContentBlockStart with tool_use
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call_123","name":"Glob"}}}"#,
            // Assistant event arrives BEFORE any ToolUseDelta (message_id matches)
            // At this point, has_any_streamed_content() is false, so this gets rendered
            r#"{"type":"assistant","message":{"id":"msg_glm_bug_early","type":"message","role":"assistant","model":"glm-4.7","content":[{"type":"tool_use","id":"call_123","name":"Glob","input":{"pattern":"**/*.rs"}}]}}"#,
            // ToolUseDelta arrives AFTER the assistant event
            // This should be suppressed since assistant event was already rendered
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"pattern\":\"**/*.rs\"}"}}}"#,
            // ContentBlockStop
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#,
            // MessageStop
            r#"{"type":"stream_event","event":{"type":"message_stop"}}"#,
        ];

        let vterm = parse_events(&events);
        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // "Glob" (the tool name) should appear only once
        let tool_count = vterm_ref.count_visible_pattern("Tool");
        assert!(
            tool_count <= 1,
            "GLM BUG: Tool appears {tool_count} times. Assistant event before streaming deltas should not cause duplication. Output: {visible}"
        );

        // Pattern should appear only once
        let pattern_count = vterm_ref.count_visible_pattern("**/*.rs");
        assert!(
            pattern_count <= 2,
            "GLM BUG: Pattern '**/*.rs' appears {pattern_count} times. Output: {visible}"
        );
    });
}

/// Test GLM bug: assistant event arrives BEFORE tool name is tracked, with DIFFERENT `message_id`.
///
/// This reproduces a specific GLM bug where:
/// 1. `MessageStart` arrives with empty content
/// 2. `ContentBlockStart` arrives with `tool_use` that has no input (input is None)
///    - The pattern `ContentBlock::ToolUse { name, input: Some(i) }` doesn't match
///    - So `set_tool_name` is NOT called
/// 3. `ToolUseDelta` arrives WITHOUT name field (so name is still not tracked)
/// 4. Assistant event arrives with DIFFERENT `message_id` (CCS layer transformation)
///    and full `tool_use` content
///
/// The bug: When the assistant event arrives, `tool_names` doesn't have the tool name,
/// so `is_duplicate_tool_use` produces "`TOOL_USE::`" instead of "`TOOL_USE:Glob`:...",
/// causing the hash comparison to fail and the assistant event to be rendered again.
/// Since the `message_id` is different, the `message_id` check also fails to deduplicate.
#[test]
fn test_glm_assistant_event_before_tool_name_tracked_different_id() {
    with_default_timeout(|| {
        let events = [
            // MessageStart with empty content (no tool_use block, so no name to track)
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_glm_bug_streaming","type":"message","role":"assistant","model":"glm-4.7","content":[]}}}"#,
            // ContentBlockStart with tool_use but NO input field (input is null/None)
            // The pattern `ContentBlock::ToolUse { name, input: Some(i) }` does NOT match
            // so set_tool_name is NOT called here
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call_123","name":"Glob"}}}"#,
            // ToolUseDelta WITHOUT name field (GLM may not send name in delta)
            // This means set_tool_name is still NOT called
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"pattern\":\"**/*.rs\"}"}}}"#,
            // Assistant event arrives with DIFFERENT message_id (CCS layer may have changed it)
            // and full tool_use content (including name)
            // This should be deduplicated via hash, but the bug causes it to be rendered again
            r#"{"type":"assistant","message":{"id":"msg_glm_bug_assistant","type":"message","role":"assistant","model":"glm-4.7","content":[{"type":"tool_use","id":"call_123","name":"Glob","input":{"pattern":"**/*.rs"}}]}}"#,
            // ContentBlockStop
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#,
            // MessageStop
            r#"{"type":"stream_event","event":{"type":"message_stop"}}"#,
        ];

        let vterm = parse_events(&events);
        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // "Glob" (the tool name) should appear only once
        let tool_count = vterm_ref.count_visible_pattern("Tool");
        assert!(
            tool_count <= 1,
            "GLM BUG: Tool appears {tool_count} times. Assistant event should not duplicate streaming output even when tool name wasn't tracked before and message_id differs. Output: {visible}"
        );

        // Pattern should appear only once (or few times for in-place updates)
        let pattern_count = vterm_ref.count_visible_pattern("**/*.rs");
        assert!(
            pattern_count <= 2,
            "GLM BUG: Pattern '**/*.rs' appears {pattern_count} times. Output: {visible}"
        );
    });
}

/// Test GLM bug: assistant event arrives BEFORE tool name is tracked.
///
/// This reproduces a specific GLM bug where:
/// 1. `MessageStart` arrives with empty content
/// 2. `ContentBlockStart` arrives with `tool_use` that has no input (input is None)
///    - The pattern `ContentBlock::ToolUse { name, input: Some(i) }` doesn't match
///    - So `set_tool_name` is NOT called
/// 3. `ToolUseDelta` arrives WITHOUT name field (so name is still not tracked)
/// 4. Assistant event arrives with full `tool_use` content
///
/// The bug: When the assistant event arrives, `tool_names` doesn't have the tool name,
/// so `is_duplicate_tool_use` produces "`TOOL_USE::`" instead of "`TOOL_USE:Glob`:...",
/// causing the hash comparison to fail and the assistant event to be rendered again.
#[test]
fn test_glm_assistant_event_before_tool_name_tracked() {
    with_default_timeout(|| {
        let events = [
            // MessageStart with empty content (no tool_use block, so no name to track)
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_glm_bug_001","type":"message","role":"assistant","model":"glm-4.7","content":[]}}}"#,
            // ContentBlockStart with tool_use but NO input field (input is null/None)
            // The pattern `ContentBlock::ToolUse { name, input: Some(i) }` does NOT match
            // so set_tool_name is NOT called here
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call_123","name":"Glob"}}}"#,
            // ToolUseDelta WITHOUT name field (GLM may not send name in delta)
            // This means set_tool_name is still NOT called
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"pattern\":\"**/*.rs\"}"}}}"#,
            // Assistant event arrives with full tool_use content (including name)
            // This should be deduplicated, but the bug causes it to be rendered again
            r#"{"type":"assistant","message":{"id":"msg_glm_bug_001","type":"message","role":"assistant","model":"glm-4.7","content":[{"type":"tool_use","id":"call_123","name":"Glob","input":{"pattern":"**/*.rs"}}]}}"#,
            // ContentBlockStop
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#,
            // MessageStop
            r#"{"type":"stream_event","event":{"type":"message_stop"}}"#,
        ];

        let vterm = parse_events(&events);
        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // "Glob" (the tool name) should appear only once
        let tool_count = vterm_ref.count_visible_pattern("Tool");
        assert!(
            tool_count <= 1,
            "GLM BUG: Tool appears {tool_count} times. Assistant event should not duplicate streaming output even when tool name wasn't tracked before. Output: {visible}"
        );

        // Pattern should appear only once (or few times for in-place updates)
        let pattern_count = vterm_ref.count_visible_pattern("**/*.rs");
        assert!(
            pattern_count <= 2,
            "GLM BUG: Pattern '**/*.rs' appears {pattern_count} times. Output: {visible}"
        );
    });
}

/// Test GLM bug: multiple content blocks with non-sequential indices cause incorrect deduplication.
///
/// This reproduces a specific bug where when there are multiple text content blocks
/// with indices that don't sort correctly lexicographically (e.g., 0, 1, 10, 2),
/// the deduplication logic incorrectly reconstructs content, causing assistant events
/// to not be recognized as duplicates.
///
/// The bug: Text key sorting used `format!("{:?}-{}", k.0, k.1)` which creates
/// lexicographic sort order: "Text-0", "Text-1", "Text-10", "Text-2".
/// But the correct order is: "Text-0", "Text-1", "Text-2", "Text-10".
///
/// This causes the reconstructed content to have blocks in the wrong order,
/// making hash-based deduplication fail. The fix uses numeric sorting
/// (`k.1.parse::`<u64>()) instead of lexicographic string sorting.
#[test]
fn test_glm_multiple_content_blocks_lexicographic_sort_bug() {
    with_default_timeout(|| {
        let events = [
            // MessageStart with multiple text content blocks
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_glm_sort_bug","type":"message","role":"assistant","model":"glm-4.7","content":[]}}}"#,
            // ContentBlockStart for block 0
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#,
            // Delta for block 0
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Block 0"}}}"#,
            // ContentBlockStart for block 1
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}"#,
            // Delta for block 1
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Block 1"}}}"#,
            // ContentBlockStart for block 10 (this causes lexicographic sort issue)
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":10,"content_block":{"type":"text","text":""}}}"#,
            // Delta for block 10
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":10,"delta":{"type":"text_delta","text":"Block 10"}}}"#,
            // ContentBlockStart for block 2
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"text","text":""}}}"#,
            // Delta for block 2
            r#"{"type":"stream_event","event":{"type":"content_block_delta","index":2,"delta":{"type":"text_delta","text":"Block 2"}}}"#,
            // Assistant event arrives with all accumulated content in correct order
            // The assistant event has blocks in the correct order: 0, 1, 2, 10
            // But deduplication reconstructs as: 0, 1, 10, 2 (lexicographic sort)
            r#"{"type":"assistant","message":{"id":"msg_glm_sort_bug","type":"message","role":"assistant","model":"glm-4.7","content":[{"type":"text","text":"Block 0"},{"type":"text","text":"Block 1"},{"type":"text","text":"Block 2"},{"type":"text","text":"Block 10"}]}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":2}}"#,
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":10}}"#,
            r#"{"type":"stream_event","event":{"type":"message_stop"}}"#,
        ];

        let vterm = parse_events(&events);
        let vterm_ref = vterm.borrow();
        let visible = vterm_ref.get_visible_output();

        // Each block should appear only once (or very few times for in-place updates)
        // The bug causes duplication because the hash comparison fails
        let first_block_count = vterm_ref.count_visible_pattern("Block 0");
        let block_1_count = vterm_ref.count_visible_pattern("Block 1");
        let block_2_count = vterm_ref.count_visible_pattern("Block 2");
        let tenth_block_count = vterm_ref.count_visible_pattern("Block 10");

        // Each block should appear at most 2 times (once for streaming, once for in-place update)
        // The bug causes 3+ appearances when lexicographic sort breaks deduplication
        assert!(
            first_block_count <= 2,
            "GLM BUG: 'Block 0' appears {first_block_count} times (expected <= 2). Lexicographic sort bug causes duplication. Output: {visible}"
        );
        assert!(
            block_1_count <= 2,
            "GLM BUG: 'Block 1' appears {block_1_count} times (expected <= 2). Output: {visible}"
        );
        assert!(
            block_2_count <= 2,
            "GLM BUG: 'Block 2' appears {block_2_count} times (expected <= 2). Output: {visible}"
        );
        assert!(
            tenth_block_count <= 2,
            "GLM BUG: 'Block 10' appears {tenth_block_count} times (expected <= 2). Output: {visible}"
        );

        // Verify no duplicate lines overall
        assert!(
            !vterm_ref.has_duplicate_lines(),
            "Lexicographic sort bug causes duplicate visible lines. Output: {visible}"
        );
    });
}

// NOTE: test_real_log_file_no_visible_duplicates has been moved to
// tests/system_tests/deduplication/mod.rs because it requires real
// filesystem access to a 1.3MB fixture file which is too large
// for include_str! embedding.
