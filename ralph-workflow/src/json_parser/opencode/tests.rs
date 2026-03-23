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
}
