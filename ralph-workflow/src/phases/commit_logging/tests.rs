// All test code for commit_logging module.
// This file is included via include!() macro from the parent commit_logging.rs module.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    // =========================================================================
    // Tests using MemoryWorkspace (architecture-conformant)
    // =========================================================================

    #[test]
    fn test_attempt_log_write_to_workspace() {
        let workspace = MemoryWorkspace::new_test();
        let log_dir = Path::new(".agent/logs/commit_generation/run_test");

        let mut log = CommitAttemptLog::new(1, "claude", "initial");
        log.set_prompt_size(5000);
        log.set_diff_info(10000, false);
        log = log.with_raw_output("raw agent output here");
        log.add_extraction_attempt(ExtractionAttempt::failure(
            "XML",
            "No <ralph-commit> tag found".to_string(),
        ));
        log.set_outcome(AttemptOutcome::Success("feat: add feature".to_string()));

        let log_path = log.write_to_workspace(log_dir, &workspace).unwrap();
        assert!(workspace.exists(&log_path));

        let content = workspace.read(&log_path).unwrap();
        assert!(content.contains("COMMIT GENERATION ATTEMPT LOG"));
        assert!(content.contains("Attempt:   #1"));
        assert!(content.contains("claude"));
    }

    #[test]
    fn test_attempt_log_write_with_all_fields() {
        let workspace = MemoryWorkspace::new_test();
        let log_dir = Path::new(".agent/logs/commit_generation/run_test");

        let mut log = CommitAttemptLog::new(1, "claude", "initial");
        log.set_prompt_size(5000);
        log.set_diff_info(10000, false);
        log = log.with_raw_output("raw agent output here");
        log.add_extraction_attempt(ExtractionAttempt::failure(
            "XML",
            "No <ralph-commit> tag found".to_string(),
        ));
        log.add_extraction_attempt(ExtractionAttempt::success(
            "JSON",
            "Extracted from JSON".to_string(),
        ));
        log.set_validation_checks(vec![
            ValidationCheck::pass("basic_length"),
            ValidationCheck::fail("no_bad_patterns", "File list pattern detected".to_string()),
        ]);
        log.set_outcome(AttemptOutcome::ExtractionFailed("bad pattern".to_string()));

        let log_path = log.write_to_workspace(log_dir, &workspace).unwrap();
        assert!(workspace.exists(&log_path));

        let content = workspace.read(&log_path).unwrap();
        assert!(content.contains("COMMIT GENERATION ATTEMPT LOG"));
        assert!(content.contains("Attempt:   #1"));
        assert!(content.contains("claude"));
        assert!(content.contains("EXTRACTION ATTEMPTS"));
        assert!(content.contains("VALIDATION RESULTS"));
        assert!(content.contains("OUTCOME"));
    }

    #[test]
    fn test_parsing_trace_write_to_workspace() {
        let workspace = MemoryWorkspace::new_test();
        let log_dir = Path::new(".agent/logs/commit_generation/run_test");

        let mut trace = ParsingTraceLog::new(1, "claude", "initial");
        trace.set_raw_output("raw agent output");
        trace.add_step(
            ParsingTraceStep::new(1, "XML extraction")
                .with_input("input")
                .with_success(true),
        );
        trace.set_final_message("feat: add feature");

        let trace_path = trace.write_to_workspace(log_dir, &workspace).unwrap();
        assert!(workspace.exists(&trace_path));

        let content = workspace.read(&trace_path).unwrap();
        assert!(content.contains("PARSING TRACE LOG"));
        assert!(content.contains("Attempt #001"));
    }

    #[test]
    fn test_parsing_trace_write_with_steps() {
        let workspace = MemoryWorkspace::new_test();
        let log_dir = Path::new(".agent/logs/commit_generation/run_test");

        let mut trace = ParsingTraceLog::new(1, "claude", "initial");
        trace.set_raw_output("raw agent output");
        trace.add_step(
            ParsingTraceStep::new(1, "XML extraction")
                .with_input("input")
                .with_result("result")
                .with_success(true)
                .with_details("success"),
        );
        trace.add_step(
            ParsingTraceStep::new(2, "Validation")
                .with_success(false)
                .with_details("failed"),
        );
        trace.set_final_message("feat: add feature");

        let trace_path = trace.write_to_workspace(log_dir, &workspace).unwrap();
        assert!(workspace.exists(&trace_path));
        assert!(trace_path.to_string_lossy().contains("parsing_trace"));

        let content = workspace.read(&trace_path).unwrap();
        assert!(content.contains("PARSING TRACE LOG"));
        assert!(content.contains("Attempt #001"));
        assert!(content.contains("RAW AGENT OUTPUT"));
        assert!(content.contains("PARSING STEPS"));
        assert!(content.contains("FINAL EXTRACTED MESSAGE"));
    }

    #[test]
    fn test_session_creates_run_directory() {
        let workspace = MemoryWorkspace::new_test();

        let session = CommitLogSession::new(".agent/logs/commit_generation", &workspace).unwrap();
        assert!(workspace.exists(session.run_dir()));
        assert!(session.run_dir().to_string_lossy().contains("run_"));
    }

    #[test]
    fn test_session_increments_attempt_number() {
        let workspace = MemoryWorkspace::new_test();

        let mut session =
            CommitLogSession::new(".agent/logs/commit_generation", &workspace).unwrap();

        assert_eq!(session.next_attempt_number(), 1);
        assert_eq!(session.next_attempt_number(), 2);
        assert_eq!(session.next_attempt_number(), 3);
    }

    #[test]
    fn test_session_new_attempt() {
        let workspace = MemoryWorkspace::new_test();

        let mut session =
            CommitLogSession::new(".agent/logs/commit_generation", &workspace).unwrap();

        let log1 = session.new_attempt("claude", "initial");
        assert_eq!(log1.attempt_number, 1);

        let log2 = session.new_attempt("glm", "strict_json");
        assert_eq!(log2.attempt_number, 2);
    }

    #[test]
    fn test_session_write_summary() {
        let workspace = MemoryWorkspace::new_test();

        let session = CommitLogSession::new(".agent/logs/commit_generation", &workspace).unwrap();
        session
            .write_summary(5, "SUCCESS: feat: add feature", &workspace)
            .unwrap();

        let summary_path = session.run_dir().join("SUMMARY.txt");
        assert!(workspace.exists(&summary_path));

        let content = workspace.read(&summary_path).unwrap();
        assert!(content.contains("Total attempts: 5"));
        assert!(content.contains("SUCCESS"));
    }

    #[test]
    fn test_noop_session_creation() {
        let session = CommitLogSession::noop();
        assert!(session.is_noop());
        assert!(session.run_dir().starts_with("/dev/null"));
    }

    #[test]
    fn test_noop_session_write_summary_succeeds_silently() {
        let workspace = MemoryWorkspace::new_test();
        let session = CommitLogSession::noop();

        // Should succeed without error
        session
            .write_summary(5, "SUCCESS: feat: add feature", &workspace)
            .unwrap();

        // Should not create any files
        let summary_path = session.run_dir().join("SUMMARY.txt");
        assert!(!workspace.exists(&summary_path));
    }

    #[test]
    fn test_noop_session_attempt_counter() {
        let mut session = CommitLogSession::noop();
        assert_eq!(session.next_attempt_number(), 1);
        assert_eq!(session.next_attempt_number(), 2);
        assert_eq!(session.next_attempt_number(), 3);
    }

    #[test]
    fn test_sanitize_agent_name() {
        assert_eq!(sanitize_agent_name("claude"), "claude");
        assert_eq!(sanitize_agent_name("agent/commit"), "agent_commit");
        assert_eq!(sanitize_agent_name("my-agent-v2"), "my_agent_v2");
        // Long names are truncated
        let long_name = "a".repeat(50);
        assert_eq!(sanitize_agent_name(&long_name).len(), 20);
    }

    #[test]
    fn test_large_output_truncation() {
        let mut log = CommitAttemptLog::new(1, "test", "test");
        let large_output = "x".repeat(100_000);
        log = log.with_raw_output(&large_output);

        let output = log.raw_output.unwrap();
        assert!(output.len() < large_output.len());
        assert!(output.contains("[... truncated"));
    }

    #[test]
    fn test_parsing_trace_step_creation() {
        let step = ParsingTraceStep::new(1, "XML extraction");
        assert_eq!(step.step_number, 1);
        assert_eq!(step.description, "XML extraction");
        assert!(!step.success);
        assert!(step.input.is_none());
        assert!(step.result.is_none());
    }

    #[test]
    fn test_parsing_trace_step_builder() {
        let step = ParsingTraceStep::new(1, "XML extraction")
            .with_input("input content")
            .with_result("result content")
            .with_success(true)
            .with_details("extraction successful");

        assert!(step.success);
        assert_eq!(step.input.as_deref(), Some("input content"));
        assert_eq!(step.result.as_deref(), Some("result content"));
        assert_eq!(step.details, "extraction successful");
    }

    #[test]
    fn test_parsing_trace_step_truncation() {
        let large_input = "x".repeat(100_000);
        let step = ParsingTraceStep::new(1, "test").with_input(&large_input);

        assert!(step.input.is_some());
        let input = step.input.as_ref().unwrap();
        assert!(input.len() < large_input.len());
        assert!(input.contains("[... input truncated"));
    }

    #[test]
    fn test_parsing_trace_log_creation() {
        let trace = ParsingTraceLog::new(1, "claude", "initial");
        assert_eq!(trace.attempt_number, 1);
        assert_eq!(trace.agent, "claude");
        assert_eq!(trace.strategy, "initial");
        assert!(trace.raw_output.is_none());
        assert!(trace.steps.is_empty());
        assert!(trace.final_message.is_none());
    }

    #[test]
    fn test_parsing_trace_log_set_raw_output() {
        let mut trace = ParsingTraceLog::new(1, "claude", "initial");
        trace.set_raw_output("test output");

        assert_eq!(trace.raw_output.as_deref(), Some("test output"));
    }

    #[test]
    fn test_parsing_trace_raw_output_truncation() {
        let mut trace = ParsingTraceLog::new(1, "claude", "initial");
        let large_output = "x".repeat(100_000);
        trace.set_raw_output(&large_output);

        let output = trace.raw_output.unwrap();
        assert!(output.len() < large_output.len());
        assert!(output.contains("[... raw output truncated"));
    }

    #[test]
    fn test_parsing_trace_add_step() {
        let mut trace = ParsingTraceLog::new(1, "claude", "initial");
        let step = ParsingTraceStep::new(1, "XML extraction");
        trace.add_step(step);

        assert_eq!(trace.steps.len(), 1);
        assert_eq!(trace.steps[0].description, "XML extraction");
    }

    #[test]
    fn test_parsing_trace_set_final_message() {
        let mut trace = ParsingTraceLog::new(1, "claude", "initial");
        trace.set_final_message("feat: add feature");

        assert_eq!(trace.final_message.as_deref(), Some("feat: add feature"));
    }

    #[test]
    fn test_attempt_log_creation() {
        let log = CommitAttemptLog::new(1, "claude", "initial");
        assert_eq!(log.attempt_number, 1);
        assert_eq!(log.agent, "claude");
        assert_eq!(log.strategy, "initial");
        assert!(log.raw_output.is_none());
        assert!(log.extraction_attempts.is_empty());
        assert!(log.validation_checks.is_empty());
        assert!(log.outcome.is_none());
    }

    #[test]
    fn test_attempt_log_set_values() {
        let mut log = CommitAttemptLog::new(2, "glm", "strict_json");

        log.set_prompt_size(10_000);
        log.set_diff_info(50_000, true);
        log = log.with_raw_output("test output");

        assert_eq!(log.prompt_size_bytes, 10_000);
        assert_eq!(log.diff_size_bytes, 50_000);
        assert!(log.diff_was_truncated);
        assert_eq!(log.raw_output.as_deref(), Some("test output"));
    }

    #[test]
    fn test_extraction_attempt_creation() {
        let success =
            ExtractionAttempt::success("XML", "Found <ralph-commit> at pos 0".to_string());
        assert!(success.success);
        assert_eq!(success.method, "XML");

        let failure = ExtractionAttempt::failure("JSON", "No JSON found".to_string());
        assert!(!failure.success);
        assert_eq!(failure.method, "JSON");
    }

    #[test]
    fn test_validation_check_creation() {
        let pass = ValidationCheck::pass("basic_length");
        assert!(pass.passed);
        assert!(pass.error.is_none());

        let fail = ValidationCheck::fail("no_json_artifacts", "Found JSON in message".to_string());
        assert!(!fail.passed);
        assert!(fail.error.is_some());
    }

    #[test]
    fn test_outcome_display() {
        let success = AttemptOutcome::Success("feat: add feature".to_string());
        assert!(format!("{success}").contains("SUCCESS"));

        let error = AttemptOutcome::ExtractionFailed("extraction failed".to_string());
        assert!(format!("{error}").contains("EXTRACTION_FAILED"));
    }
}
