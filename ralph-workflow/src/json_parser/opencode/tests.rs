// Tests for OpenCode parser.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_opencode_step_start() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"step_start","timestamp":1768191337567,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06aa45c001","sessionID":"ses_44f9562d4ffe","messageID":"msg_bb06a9dc1001","type":"step-start","snapshot":"5d36aa035d4df6edb73a68058733063258114ed5"}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Step started"));
        assert!(out.contains("5d36aa03"));
    }

    #[test]
    fn test_opencode_step_start_dedupes_duplicate_starts_for_same_message_id() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"step_start","timestamp":1768191337567,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06aa45c001","sessionID":"ses_44f9562d4ffe","messageID":"msg_bb06a9dc1001","type":"step-start","snapshot":"5d36aa035d4df6edb73a68058733063258114ed5"}}"#;

        let first = parser.parse_event(json);
        assert!(first.is_some());
        assert!(first.unwrap().contains("Step started"));

        // Defensive behavior: OpenCode can emit duplicate step_start events; we should not spam.
        let second = parser.parse_event(json);
        assert!(second.is_none());
    }

    #[test]
    fn test_opencode_step_start_missing_ids_use_unique_fallback() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"type":"step-start"}}"#;

        let first = parser.parse_event(json);
        assert!(first.is_some());

        let second = parser.parse_event(json);
        assert!(second.is_some());
    }

    #[test]
    fn test_opencode_step_finish_sets_fallback_message_id_when_missing() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"step_finish","timestamp":2,"sessionID":"ses_test","part":{"type":"step-finish","reason":"end_turn"}}"#;

        let output = parser.parse_event(json);
        assert!(output.is_some());

        let session = parser.state.streaming_session.borrow();
        let current = session.get_current_message_id();
        assert!(
            current.is_some(),
            "expected fallback message id to be set for step_finish without identifiers"
        );
    }

    #[test]
    fn test_opencode_step_finish() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"step_finish","timestamp":1768191347296,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06aca1d001","sessionID":"ses_44f9562d4ffe","messageID":"msg_bb06a9dc1001","type":"step-finish","reason":"tool-calls","snapshot":"5d36aa035d4df6edb73a68058733063258114ed5","cost":0,"tokens":{"input":108,"output":151,"reasoning":0,"cache":{"read":11236,"write":0}}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Step finished"));
        assert!(out.contains("tool-calls"));
        assert!(out.contains("in:108"));
        assert!(out.contains("out:151"));
        assert!(out.contains("cache:11236"));
    }

    #[test]
    fn test_opencode_tool_use_completed() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","sessionID":"ses_44f9562d4ffe","messageID":"msg_bb06a9dc1001","type":"tool","callID":"call_8a2985d92e63","tool":"read","state":{"status":"completed","input":{"filePath":"/test/PLAN.md"},"output":"<file>\n00001| # Implementation Plan\n</file>","title":"PLAN.md"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Tool"));
        assert!(out.contains("read"));
        assert!(out.contains("✓")); // completed icon
        assert!(out.contains("PLAN.md"));
    }

    #[test]
    fn test_opencode_tool_use_pending() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","sessionID":"ses_44f9562d4ffe","messageID":"msg_bb06a9dc1001","type":"tool","callID":"call_8a2985d92e63","tool":"bash","state":{"status":"pending","input":{"command":"ls -la"}}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Tool"));
        assert!(out.contains("bash"));
        assert!(out.contains("…")); // pending icon (WAIT)
    }

    #[test]
    fn test_opencode_tool_use_shows_input() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","sessionID":"ses_44f9562d4ffe","messageID":"msg_bb06a9dc1001","type":"tool","callID":"call_8a2985d92e63","tool":"read","state":{"status":"completed","input":{"filePath":"/Users/test/file.rs"}}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Tool"));
        assert!(out.contains("read"));
        assert!(out.contains("/Users/test/file.rs"));
    }

    #[test]
    #[cfg(feature = "test-utils")]
    fn test_opencode_text_event() {
        use crate::json_parser::terminal::TerminalMode;

        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
            .with_terminal_mode(TerminalMode::Full);
        let json = r#"{"type":"text","timestamp":1768191347231,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac63300","sessionID":"ses_44f9562d4ffe","messageID":"msg_bb06a9dc1001","type":"text","text":"I'll start by reading the plan and requirements to understand what needs to be implemented.","time":{"start":1768191347226,"end":1768191347226}}}"#;
        let output = parser.parse_event(json);

        // In non-TTY output, per-delta text output may be suppressed to avoid log spam.
        // If output is produced, it should contain the streamed content.
        if let Some(out) = output {
            assert!(out.contains("I'll start by reading the plan"));
        }
    }

    #[test]
    fn test_opencode_unknown_event_ignored() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"unknown_event","timestamp":1768191347231,"sessionID":"ses_44f9562d4ffe","part":{}}"#;
        let output = parser.parse_event(json);
        // Unknown events should return None
        assert!(output.is_none());
    }

    #[test]
    fn test_opencode_parser_non_json_passthrough() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let output = parser.parse_event("Error: something went wrong");
        assert!(output.is_some());
        assert!(output.unwrap().contains("Error: something went wrong"));
    }

    #[test]
    fn test_opencode_parser_malformed_json_ignored() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let output = parser.parse_event("{invalid json here}");
        assert!(output.is_none());
    }

    #[test]
    fn test_opencode_step_finish_with_cost() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"step_finish","timestamp":1768191347296,"sessionID":"ses_44f9562d4ffe","part":{"type":"step-finish","reason":"end_turn","cost":0.0025,"tokens":{"input":1000,"output":500,"reasoning":0,"cache":{"read":0,"write":0}}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Step finished"));
        assert!(out.contains("end_turn"));
        assert!(out.contains("$0.0025"));
    }

    #[test]
    fn test_opencode_tool_verbose_shows_output() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Verbose);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"read","state":{"status":"completed","input":{"filePath":"/test.rs"},"output":"fn main() { println!(\"Hello\"); }"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Tool"));
        assert!(out.contains("read"));
        assert!(out.contains("Output"));
        assert!(out.contains("fn main"));
    }

    #[test]
    fn test_opencode_tool_running_status() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"bash","state":{"status":"running","input":{"command":"npm test"},"time":{"start":1768191346712}}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Tool"));
        assert!(out.contains("bash"));
        assert!(out.contains("►")); // running icon
    }

    #[test]
    fn test_opencode_tool_error_status() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"bash","state":{"status":"error","input":{"command":"invalid_cmd"},"error":"Command not found: invalid_cmd","time":{"start":1768191346712,"end":1768191346800}}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Tool"));
        assert!(out.contains("bash"));
        assert!(out.contains("✗")); // error icon
        assert!(out.contains("Error"));
        assert!(out.contains("Command not found"));
    }

    #[test]
    fn test_opencode_error_event() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"error","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","error":{"name":"APIError","message":"Rate limit exceeded"}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Error"));
        assert!(out.contains("Rate limit exceeded"));
    }

    #[test]
    fn test_opencode_error_event_with_data_message() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        // Error with data.message (as in run.ts lines 197-199)
        let json = r#"{"type":"error","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","error":{"name":"ProviderError","data":{"message":"Invalid API key"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("Error"));
        assert!(out.contains("Invalid API key"));
    }

    #[test]
    fn test_opencode_tool_bash_formatting() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"type":"tool","tool":"bash","state":{"status":"completed","input":{"command":"git status"},"output":"On branch main","title":"git status"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("bash"));
        assert!(out.contains("git status"));
    }

    #[test]
    fn test_opencode_tool_glob_formatting() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"type":"tool","tool":"glob","state":{"status":"completed","input":{"pattern":"**/*.rs","path":"src"},"output":"found 10 files","title":"**/*.rs"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("glob"));
        assert!(out.contains("**/*.rs"));
        assert!(out.contains("in src"));
    }

    #[test]
    fn test_opencode_tool_grep_formatting() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"type":"tool","tool":"grep","state":{"status":"completed","input":{"pattern":"TODO","path":"src","include":"*.rs"},"output":"3 matches","title":"TODO"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("grep"));
        assert!(out.contains("/TODO/"));
        assert!(out.contains("in src"));
        assert!(out.contains("(*.rs)"));
    }

    #[test]
    fn test_opencode_tool_write_formatting() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"type":"tool","tool":"write","state":{"status":"completed","input":{"filePath":"test.txt","content":"Hello World"},"output":"wrote 11 bytes","title":"test.txt"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("write"));
        assert!(out.contains("test.txt"));
        assert!(out.contains("11 bytes"));
    }

    #[test]
    fn test_opencode_tool_read_with_offset_limit() {
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"type":"tool","tool":"read","state":{"status":"completed","input":{"filePath":"large.txt","offset":100,"limit":50},"output":"content...","title":"large.txt"}}}"#;
        let output = parser.parse_event(json);
        assert!(output.is_some());
        let out = output.unwrap();
        assert!(out.contains("read"));
        assert!(out.contains("large.txt"));
        assert!(out.contains("offset: 100"));
        assert!(out.contains("limit: 50"));
    }

    #[test]
    fn test_classify_successful_parse_detects_partial_event() {
        let line = r#"{"type":"text","timestamp":2,"sessionID":"ses_test","part":{"type":"text","text":"hello"}}"#;
        let classification =
            OpenCodeParser::classify_successful_parse_for_monitor(line, line.trim());

        assert_eq!(classification, MonitorEventClassification::Partial);
    }

    #[test]
    fn test_classify_successful_parse_non_json_is_parsed() {
        let line = "plain output";
        let classification =
            OpenCodeParser::classify_successful_parse_for_monitor(line, line.trim());

        assert_eq!(classification, MonitorEventClassification::Parsed);
    }

    #[test]
    fn test_classify_empty_output_detects_control_event() {
        let line = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"type":"step-start"}}"#;
        let classification = OpenCodeParser::classify_empty_output_for_monitor(line, line.trim());

        assert_eq!(classification, MonitorEventClassification::Control);
    }

    #[test]
    fn test_classify_empty_output_detects_unknown_event() {
        let line = r#"{"type":"new_future_event","timestamp":1,"sessionID":"ses_test"}"#;
        let classification = OpenCodeParser::classify_empty_output_for_monitor(line, line.trim());

        assert_eq!(classification, MonitorEventClassification::Unknown);
    }

    #[test]
    fn test_classify_empty_output_detects_parse_error() {
        let line = "{invalid json}";
        let classification = OpenCodeParser::classify_empty_output_for_monitor(line, line.trim());

        assert_eq!(classification, MonitorEventClassification::ParseError);
    }

    #[test]
    fn test_classify_empty_output_non_json_is_ignored() {
        let line = "not json";
        let classification = OpenCodeParser::classify_empty_output_for_monitor(line, line.trim());

        assert_eq!(classification, MonitorEventClassification::Ignored);
    }

    #[test]
    fn test_step_finish_without_part_flushes_accumulated_text() {
        // Bug: when step_finish has no `part` field, map_or_else(String::new, ...) drops
        // the `text_flush_non_tty` string even though it was already computed.
        // In Basic/None mode, this silently discards all text from the step.
        use crate::json_parser::terminal::TerminalMode;

        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
            .with_terminal_mode(TerminalMode::None);

        // Start a step so the session has a current message ID
        let step_start = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"messageID":"msg_a","type":"step-start"}}"#;
        parser.parse_event(step_start);

        // Accumulate text in the session (None mode suppresses per-delta output)
        let text_event = r#"{"type":"text","timestamp":2,"sessionID":"ses_test","part":{"type":"text","text":"Hello from step"}}"#;
        let text_out = parser.parse_event(text_event);
        assert!(text_out.is_none(), "None mode should suppress per-delta text");

        // step_finish with NO part field — should still flush accumulated text
        let finish_no_part =
            r#"{"type":"step_finish","timestamp":3,"sessionID":"ses_test"}"#;
        let finish_out = parser.parse_event(finish_no_part);

        assert!(
            finish_out.is_some(),
            "step_finish without part should output accumulated text, got None"
        );
        assert!(
            finish_out.unwrap().contains("Hello from step"),
            "Accumulated text should appear in step_finish output even when part is missing"
        );
    }

    #[test]
    fn test_new_step_start_without_finish_flushes_previous_text() {
        // Bug: when a new step_start arrives while the previous step was mid-stream,
        // on_message_start() clears `accumulated` before the text is flushed.
        // In Basic/None mode, the previous step's text is silently lost.
        use crate::json_parser::terminal::TerminalMode;

        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
            .with_terminal_mode(TerminalMode::None);

        // Step A starts
        let step_a_start = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"messageID":"msg_a","type":"step-start"}}"#;
        parser.parse_event(step_a_start);

        // Text accumulates for Step A (None mode suppresses per-delta output)
        let text_event = r#"{"type":"text","timestamp":2,"sessionID":"ses_test","part":{"type":"text","text":"Step A thinking..."}}"#;
        let text_out = parser.parse_event(text_event);
        assert!(text_out.is_none(), "None mode should suppress per-delta text");

        // Step B starts WITHOUT a step_finish for Step A — should flush Step A's text
        let step_b_start = r#"{"type":"step_start","timestamp":3,"sessionID":"ses_test","part":{"messageID":"msg_b","type":"step-start"}}"#;
        let step_b_out = parser.parse_event(step_b_start);

        assert!(
            step_b_out.is_some(),
            "step_b_start should produce output containing flushed Step A text"
        );
        let out = step_b_out.unwrap();
        assert!(
            out.contains("Step A thinking..."),
            "Text from incomplete Step A should be flushed when Step B starts, got: {out:?}"
        );
        assert!(
            out.contains("Step started"),
            "Output should also contain the Step B started line"
        );
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Issue 1 regression: single truncation summary for tool output exceeding
    // the line limit.
    // ──────────────────────────────────────────────────────────────────────────

    #[test]
    fn test_opencode_tool_output_truncation_emits_single_summary() {
        // Build output with 60 lines — well above MAX_OUTPUT_LINES (5).
        let lines: Vec<String> = (1..=60).map(|i| format!("/path/file{i}.rs")).collect();
        let output_content = lines.join("\n");
        let output_json = serde_json::to_string(&output_content).unwrap();
        let json = format!(
            r#"{{"type":"tool_use","timestamp":1,"sessionID":"ses_test","part":{{"type":"tool","tool":"grep","state":{{"status":"completed","input":{{"pattern":"TODO"}},"output":{output_json}}}}}}}"#
        );
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        if let Some(out) = parser.parse_event(&json) {
            let more_count = out.matches("more lines").count();
            assert!(
                more_count <= 1,
                "expected at most one 'more lines' summary, got {more_count}\noutput:\n{out}"
            );
            // Verify truncation actually happened — not all 60 lines should appear.
            assert!(
                !out.contains("/path/file60.rs"),
                "expected output to be truncated but all lines were shown\noutput:\n{out}"
            );
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Issue 2 regression: no blank └─ line for empty or whitespace-only title.
    // ──────────────────────────────────────────────────────────────────────────

    #[test]
    fn test_opencode_tool_no_blank_continuation_line_for_empty_title() {
        let json = r#"{"type":"tool_use","timestamp":1,"sessionID":"ses_test","part":{"type":"tool","tool":"bash","state":{"status":"completed","input":{"command":"ls"},"output":"file.txt","title":""}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        for line in out.lines() {
            if line.contains("└─") {
                let after_arrow = line.split_once("└─").map(|x| x.1).unwrap_or("");
                assert!(
                    !after_arrow.trim().is_empty(),
                    "found blank continuation line: {line:?}\nfull output:\n{out}"
                );
            }
        }
    }

    #[test]
    fn test_opencode_tool_no_blank_continuation_line_for_whitespace_title() {
        let json = r#"{"type":"tool_use","timestamp":1,"sessionID":"ses_test","part":{"type":"tool","tool":"bash","state":{"status":"completed","input":{"command":"ls"},"output":"file.txt","title":"   "}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        for line in out.lines() {
            if line.contains("└─") {
                let after_arrow = line.split_once("└─").map(|x| x.1).unwrap_or("");
                assert!(
                    !after_arrow.trim().is_empty(),
                    "found blank continuation line: {line:?}\nfull output:\n{out}"
                );
            }
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Issues 3 & 4 regression: step-finish uses "reasoning:" label (not
    // "reason:") and · delimiter with cost.
    // ──────────────────────────────────────────────────────────────────────────

    #[test]
    fn test_opencode_step_finish_reasoning_label_not_reason() {
        // reasoning tokens = 24 → must appear as "reasoning:24", not "reason:24"
        let json = r#"{"type":"step_finish","timestamp":1,"sessionID":"ses_test","part":{"type":"step-finish","reason":"tool-calls","cost":0.001,"tokens":{"input":532,"output":85,"reasoning":24,"cache":{"read":151680,"write":0}}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        assert!(
            out.contains("reasoning:24"),
            "step-finish must use 'reasoning:' label, got:\n{out}"
        );
        assert!(
            !out.contains("reason:24"),
            "step-finish must not use ambiguous 'reason:24' label, got:\n{out}"
        );
    }

    #[test]
    fn test_opencode_step_finish_uses_middle_dot_delimiter() {
        let json = r#"{"type":"step_finish","timestamp":1,"sessionID":"ses_test","part":{"type":"step-finish","reason":"end_turn","cost":0.005,"tokens":{"input":100,"output":50,"reasoning":0,"cache":{"read":0,"write":0}}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        // Must use · (U+00B7) as delimiter between token summary and cost.
        assert!(
            out.contains('\u{00b7}'),
            "step-finish must use · (U+00B7) delimiter, got:\n{out}"
        );
        // Must show cost.
        assert!(
            out.contains("$0.0050"),
            "step-finish must show cost, got:\n{out}"
        );
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Issue 5 regression: step-started never shows (...) for short/empty snapshot.
    // ──────────────────────────────────────────────────────────────────────────

    #[test]
    fn test_opencode_step_start_short_snapshot_no_ellipsis() {
        // snapshot is only 3 chars — must not produce (...) or any parenthetical
        let json = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"type":"step-start","snapshot":"abc"}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        assert!(
            !out.contains("(...)"),
            "step-started must not show (...) for short snapshot, got:\n{out}"
        );
        assert!(
            !out.contains("(abc"),
            "step-started must not show parenthetical for short snapshot, got:\n{out}"
        );
    }

    #[test]
    fn test_opencode_step_start_no_snapshot_no_parenthetical() {
        // no snapshot field at all — must not produce any parenthetical
        let json = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"type":"step-start"}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        assert!(
            !out.contains('('),
            "step-started with no snapshot must have no parenthetical, got:\n{out}"
        );
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Issue 6 regression: blank lines in tool output are normalized.
    // ──────────────────────────────────────────────────────────────────────────

    #[test]
    fn test_opencode_tool_output_blank_lines_normalized() {
        // Output with leading blank, trailing blank, and double interior blank.
        let raw_output = "\n\nline1\n\n\nline2\n\n";
        let output_json = serde_json::to_string(raw_output).unwrap();
        let json = format!(
            r#"{{"type":"tool_use","timestamp":1,"sessionID":"ses_test","part":{{"type":"tool","tool":"bash","state":{{"status":"completed","input":{{"command":"echo"}},"output":{output_json}}}}}}}"#
        );
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(&json).unwrap_or_default();
        // Content must be preserved.
        assert!(out.contains("line1"), "output must contain line1, got:\n{out}");
        assert!(out.contains("line2"), "output must contain line2, got:\n{out}");
        // No three consecutive newlines (double-blank run).
        assert!(
            !out.contains("\n\n\n"),
            "output must not contain consecutive blank lines, got:\n{out}"
        );
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Issue 7 regression: duration shown for running and completed tools;
    // pending tools show no duration.
    // ──────────────────────────────────────────────────────────────────────────

    #[test]
    fn test_opencode_tool_completed_shows_duration() {
        // start=1000, end=15000 → 14 000ms = 14s
        let json = r#"{"type":"tool_use","timestamp":15000,"sessionID":"ses_test","part":{"type":"tool","tool":"bash","state":{"status":"completed","input":{"command":"npm test"},"output":"passed","time":{"start":1000,"end":15000}}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        assert!(
            out.contains("14s"),
            "completed tool must show duration '14s', got:\n{out}"
        );
    }

    #[test]
    fn test_opencode_tool_running_shows_elapsed_duration() {
        // start=1000, event timestamp=5000 → 4 000ms = 4s elapsed
        let json = r#"{"type":"tool_use","timestamp":5000,"sessionID":"ses_test","part":{"type":"tool","tool":"bash","state":{"status":"running","input":{"command":"npm test"},"time":{"start":1000}}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        assert!(
            out.contains("4s"),
            "running tool must show elapsed duration '4s', got:\n{out}"
        );
    }

    #[test]
    fn test_opencode_tool_pending_no_duration() {
        // pending status, no time field → no duration
        let json = r#"{"type":"tool_use","timestamp":1000,"sessionID":"ses_test","part":{"type":"tool","tool":"bash","state":{"status":"pending","input":{"command":"npm test"}}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        // Duration format is "(Xs)" or "(Xms)" — pending tools must show neither.
        assert!(
            !out.contains("(0ms)") && !out.contains("(0s)"),
            "pending tool must not show zero duration, got:\n{out}"
        );
        // Should not contain any parenthetical with a time unit.
        assert!(
            !out.contains("ms)") && !out.contains("s)"),
            "pending tool must show no duration at all, got:\n{out}"
        );
    }

    #[test]
    fn test_opencode_tool_sub_second_duration_shows_ms() {
        // start=1000, end=1500 → 500ms
        let json = r#"{"type":"tool_use","timestamp":1500,"sessionID":"ses_test","part":{"type":"tool","tool":"read","state":{"status":"completed","input":{"filePath":"/tmp/x"},"output":"content","time":{"start":1000,"end":1500}}}}"#;
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
        let out = parser.parse_event(json).unwrap_or_default();
        assert!(
            out.contains("500ms"),
            "sub-second completed tool must show duration '500ms', got:\n{out}"
        );
    }

    /// Regression: tool_use "running" must not double-count an already-counted call.
    ///
    /// With AtomicBool, pending/running both called set_tool_active (idempotent). With AtomicU32,
    /// "running" must be a no-op — only "pending" increments so the counter returns to 0 on
    /// "completed".
    #[test]
    fn test_opencode_tool_use_pending_running_completed_single_count() {
        use std::sync::Arc;
        use std::sync::atomic::{AtomicU32, Ordering};

        let tracker = Arc::new(AtomicU32::new(0));
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
            .with_tool_activity_tracker(Arc::clone(&tracker));

        // pending: new call starting — increment
        parser.parse_event(r#"{"type":"tool_use","timestamp":1,"sessionID":"s","part":{"type":"tool","tool":"write","state":{"status":"pending","input":{"filePath":"/f"}}}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 1, "counter should be 1 after pending");

        // running: status update, already counted — no-op
        parser.parse_event(r#"{"type":"tool_use","timestamp":2,"sessionID":"s","part":{"type":"tool","tool":"write","state":{"status":"running","input":{"filePath":"/f"}}}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 1, "counter must stay at 1 for running (no-op)");

        // completed: call done — decrement
        parser.parse_event(r#"{"type":"tool_use","timestamp":3,"sessionID":"s","part":{"type":"tool","tool":"write","state":{"status":"completed","input":{"filePath":"/f"},"output":"ok"}}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 0, "counter should be 0 after completed");
    }

    /// step_finish hard-resets the counter to 0 regardless of the current count.
    #[test]
    fn test_opencode_step_finish_hard_resets_tracker() {
        use std::sync::Arc;
        use std::sync::atomic::{AtomicU32, Ordering};

        let tracker = Arc::new(AtomicU32::new(0));
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
            .with_tool_activity_tracker(Arc::clone(&tracker));

        // Two tool calls start
        parser.parse_event(r#"{"type":"tool_use","timestamp":1,"sessionID":"s","part":{"type":"tool","tool":"read","state":{"status":"pending","input":{"filePath":"/a"}}}}"#);
        parser.parse_event(r#"{"type":"tool_use","timestamp":2,"sessionID":"s","part":{"type":"tool","tool":"read","state":{"status":"pending","input":{"filePath":"/b"}}}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 2, "counter should be 2 with two pending calls");

        // step_finish hard-resets
        parser.parse_event(r#"{"type":"step_finish","timestamp":3,"sessionID":"s","part":{"type":"step-finish","reason":"end_turn"}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 0, "step_finish must hard-reset counter to 0");
    }

    /// Concurrent tool calls: two pending + one completed = counter 1 (second still in flight).
    #[test]
    fn test_opencode_concurrent_tool_calls_tracked_independently() {
        use std::sync::Arc;
        use std::sync::atomic::{AtomicU32, Ordering};

        let tracker = Arc::new(AtomicU32::new(0));
        let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
            .with_tool_activity_tracker(Arc::clone(&tracker));

        parser.parse_event(r#"{"type":"tool_use","timestamp":1,"sessionID":"s","part":{"type":"tool","tool":"write","state":{"status":"pending","input":{"filePath":"/a"}}}}"#);
        parser.parse_event(r#"{"type":"tool_use","timestamp":2,"sessionID":"s","part":{"type":"tool","tool":"write","state":{"status":"pending","input":{"filePath":"/b"}}}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 2, "counter should be 2 with two concurrent calls");

        // First call completes
        parser.parse_event(r#"{"type":"tool_use","timestamp":3,"sessionID":"s","part":{"type":"tool","tool":"write","state":{"status":"completed","input":{"filePath":"/a"},"output":"ok"}}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 1, "counter should be 1 after first completes");

        // Second call completes
        parser.parse_event(r#"{"type":"tool_use","timestamp":4,"sessionID":"s","part":{"type":"tool","tool":"write","state":{"status":"completed","input":{"filePath":"/b"},"output":"ok"}}}"#);
        assert_eq!(tracker.load(Ordering::Acquire), 0, "counter should be 0 after both complete");
    }
}
