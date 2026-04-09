// Cross-parser behavior tests.
//
// Tests for verbosity settings, display names, and tool use behavior
// across different parser types.

#[test]
fn test_verbosity_affects_output() {
    let quiet_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Quiet);
    let full_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Full);

    let long_text = "a".repeat(200);
    let json = format!(
        r#"{{"type":"assistant","message":{{"content":[{{"type":"text","text":"{long_text}"}}]}}}}"#
    );

    let quiet_output = quiet_parser.parse_event(&json).unwrap();
    let full_output = full_parser.parse_event(&json).unwrap();

    // Quiet output should be truncated (shorter)
    assert!(quiet_output.len() < full_output.len());
}

#[test]
fn test_tool_use_shows_input_in_verbose_mode() {
    let verbose_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Verbose)
        .with_terminal_mode(TerminalMode::Full);
    let json = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/test.rs"}}]}}"#;
    let output = verbose_parser.parse_event(json).unwrap();
    assert!(output.contains("Tool"));
    assert!(output.contains("Read"));
    assert!(output.contains("file_path=/test.rs"));
}

#[test]
fn test_tool_use_shows_input_in_normal_mode() {
    // Tool inputs are now shown at Normal level for better usability
    let normal_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full);
    let json = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/test.rs"}}]}}"#;
    let output = normal_parser.parse_event(json).unwrap();
    assert!(output.contains("Tool"));
    assert!(output.contains("Read"));
    // Tool inputs are now visible at Normal level
    assert!(output.contains("file_path=/test.rs"));
}

#[test]
fn test_tool_use_hides_input_in_quiet_mode() {
    // Only Quiet mode hides tool inputs
    let quiet_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Quiet);
    let json = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/test.rs"}}]}}"#;
    let output = quiet_parser.parse_event(json).unwrap();
    assert!(output.contains("Tool"));
    assert!(output.contains("Read"));
    // In Quiet mode, input details should not be shown
    assert!(!output.contains("file_path=/test.rs"));
}

#[test]
fn test_parser_uses_custom_display_name_prefix() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full)
        .with_display_name("ccs-glm");
    let json = r#"{"type":"system","subtype":"init","session_id":"abc123"}"#;
    let output = parser.parse_event(json).unwrap();
    assert!(output.contains("[ccs-glm]"));
}

#[test]
fn test_debug_verbosity_is_recognized() {
    let debug_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Debug)
        .with_terminal_mode(TerminalMode::Full);
    // Debug mode should be detectable via is_debug()
    assert!(debug_parser.verbosity.is_debug());
}

/// Claude Code tool_result_block produces at most one "more lines" summary.
#[test]
fn test_claude_tool_result_single_truncation_summary() {
    // Build a 60-line tool result
    let lines: Vec<String> = (1..=60).map(|i| format!("/path/file{i}.rs")).collect();
    let result_content = lines.join("\n");
    // Craft a tool_result message event with the multiline content
    let result_json = serde_json::to_string(&result_content).unwrap();
    let json = format!(
        r#"{{"type":"assistant","message":{{"content":[{{"type":"tool_result","content":{result_json}}}]}}}}"#
    );
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    if let Some(out) = parser.parse_event(&json) {
        let more_count = out.matches("more lines").count();
        assert!(
            more_count <= 1,
            "expected at most one 'more lines' summary, got {more_count}\noutput:\n{out}"
        );
    }
}

/// OpenCode tool output produces at most one "more lines" summary (shared truncation logic).
#[test]
fn test_opencode_tool_output_single_truncation_summary() {
    let lines: Vec<String> = (1..=60).map(|i| format!("/path/file{i}.rs")).collect();
    let output_content = lines.join("\n");
    let output_json = serde_json::to_string(&output_content).unwrap();
    let json = format!(
        r#"{{"type":"tool_use","timestamp":1,"sessionID":"ses","part":{{"type":"tool","tool":"grep","state":{{"status":"completed","input":{{"pattern":"TODO"}},"output":{output_json}}}}}}}"#
    );
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    if let Some(out) = parser.parse_event(&json) {
        let more_count = out.matches("more lines").count();
        assert!(
            more_count <= 1,
            "OpenCode: expected at most one 'more lines' summary, got {more_count}\noutput:\n{out}"
        );
    }
}

/// Codex turn_completed uses middle-dot token format consistent with OpenCode step-finish.
#[test]
fn test_codex_turn_completed_uses_shared_token_format() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json =
        r#"{"type":"response.completed","response":{"usage":{"input_tokens":100,"output_tokens":50}}}"#;
    if let Some(out) = parser.parse_event(json) {
        // Should use "in:100" label (not parenthetical "(in:100)")
        assert!(
            out.contains("in:100"),
            "turn_completed should use shared token format with in: label, got: {out}"
        );
        // Must not use old parenthetical format "(in:100 out:50)"
        assert!(
            !out.contains("(in:"),
            "turn_completed must not use old parenthetical format, got: {out}"
        );
    }
}
