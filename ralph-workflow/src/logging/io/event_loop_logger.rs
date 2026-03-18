use crate::reducer::event::PipelinePhase;
use crate::workspace::Workspace;
use chrono::Utc;
use std::path::Path;

/// Parameters for logging an effect execution.
pub struct LogEffectParams<'a> {
    pub workspace: &'a dyn Workspace,
    pub log_path: &'a Path,
    pub phase: PipelinePhase,
    pub effect: &'a str,
    pub primary_event: &'a str,
    pub extra_events: &'a [String],
    pub duration_ms: u64,
    pub context: &'a [(&'a str, &'a str)],
}

/// Pure function to format the log line content.
/// This contains all the policy/logic for building the log line.
fn format_log_line_content(
    seq: u64,
    ts: &str,
    phase: &PipelinePhase,
    effect: &str,
    primary_event: &str,
    extra_events: &[String],
    context: &[(&str, &str)],
    duration_ms: u64,
) -> String {
    let extra = if extra_events.is_empty() {
        String::new()
    } else {
        format!(" extra=[{}]", extra_events.join(","))
    };

    let ctx = if context.is_empty() {
        String::new()
    } else {
        let pairs: Vec<String> = context.iter().map(|(k, v)| format!("{k}={v}")).collect();
        format!(" ctx={}", pairs.join(","))
    };

    format!(
        "{} ts={} phase={} effect={} event={}{}{} ms={}\n",
        seq, ts, phase, effect, primary_event, extra, ctx, duration_ms
    )
}

/// Get the current timestamp in RFC3339 format.
fn get_current_timestamp() -> String {
    Utc::now().to_rfc3339()
}

/// Logger for recording event loop execution.
///
/// This logger writes a human-readable log of the event loop's progression:
/// - which effects ran
/// - what events were emitted
/// - how long each effect took
/// - what phase/iteration/retry context was active
///
/// The log is always-on (not just for crashes) and is written to
/// `.agent/logs-<run_id>/event_loop.log` for easy diagnosis.
///
/// **Redaction:** This logger must never include sensitive content like
/// prompts, agent outputs, secrets, or credentials.
#[derive(Clone)]
pub struct EventLoopLogger {
    seq: u64,
}

impl EventLoopLogger {
    /// Create a new `EventLoopLogger`.
    ///
    /// The sequence counter starts at 1 for the first logged effect.
    #[must_use]
    pub const fn new() -> Self {
        Self { seq: 1 }
    }

    /// Get the current sequence number.
    #[must_use]
    pub const fn seq(&self) -> u64 {
        self.seq
    }

    /// Create a new `EventLoopLogger` that continues from an existing log file.
    ///
    /// This reads the last sequence number from the existing log file
    /// and starts the counter from `last_seq + 1`. This is important
    /// for resume scenarios to maintain monotonically increasing sequence
    /// numbers within a run.
    ///
    /// # Arguments
    ///
    /// * `workspace` - Workspace implementation for reading the log file
    /// * `log_path` - Path to the existing event loop log file
    ///
    /// # Returns
    ///
    /// * `Ok(EventLoopLogger)` - Logger initialized with next sequence number
    /// * `Err(std::io::Error)` - If reading the log file fails
    ///
    /// # Behavior
    ///
    /// - If the log file doesn't exist or is empty, starts at seq=1
    /// - If the log file exists, reads the last line to extract the sequence number
    /// - If the last line doesn't match the expected format, starts at seq=1
    /// - The sequence counter is set to `last_seq + 1` to continue the sequence
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn from_existing_log(
        workspace: &dyn crate::workspace::Workspace,
        log_path: &Path,
    ) -> Result<Self, std::io::Error> {
        if !workspace.exists(log_path) {
            return Ok(Self { seq: 1 });
        }

        let content = workspace.read(log_path)?;
        if content.is_empty() {
            return Ok(Self { seq: 1 });
        }

        let last_seq = content
            .lines()
            .rev()
            .find(|line| !line.trim().is_empty())
            .and_then(|line| line.split_whitespace().next()?.parse::<u64>().ok())
            .unwrap_or(0);

        Ok(Self { seq: last_seq + 1 })
    }

    /// Log an effect execution.
    ///
    /// This writes a single line to the event loop log with the following format:
    /// ```text
    /// <seq> ts=<rfc3339> phase=<Phase> effect=<Effect> event=<Event> [extra=[E1,E2]] [ctx=k1=v1,k2=v2] ms=<N>
    /// ```
    ///
    /// Example:
    /// ```text
    /// 1 ts=2026-02-06T14:03:27.123Z phase=Development effect=InvokePrompt event=PromptCompleted ms=1234
    /// 2 ts=2026-02-06T14:03:28.456Z phase=Development effect=WriteFile event=FileWritten ctx=file=PLAN.md ms=12
    /// ```
    ///
    /// # Best-Effort Logging
    ///
    /// Write failures are returned but do not affect pipeline correctness.
    /// This is intentional: event loop logging is observability-only and must not
    /// affect pipeline correctness. If logging fails (e.g., disk full, permissions),
    /// the pipeline continues execution.
    ///
    /// Callers who want visibility into logging failures should check the return value
    /// and log to the pipeline logger if desired.
    ///
    /// # Returns
    ///
    /// Returns a new logger with the next sequence number.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn log_effect(self, params: &LogEffectParams<'_>) -> Result<(Self, u64), std::io::Error> {
        let ts = get_current_timestamp();

        let line = format_log_line_content(
            self.seq,
            &ts,
            &params.phase,
            params.effect,
            params.primary_event,
            params.extra_events,
            params.context,
            params.duration_ms,
        );

        params
            .workspace
            .append_bytes(params.log_path, line.as_bytes())?;

        let next_seq = self.seq.saturating_add(1);
        let updated_logger = Self { seq: next_seq };
        Ok((updated_logger, next_seq))
    }
}

impl Default for EventLoopLogger {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::WorkspaceFs;

    #[test]
    fn test_event_loop_logger_basic() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");
        let logger = EventLoopLogger::new();

        // Log a few effects
        let (logger, _) = logger
            .log_effect(&LogEffectParams {
                workspace: &workspace,
                log_path,
                phase: PipelinePhase::Development,
                effect: "InvokePrompt",
                primary_event: "PromptCompleted",
                extra_events: &[],
                duration_ms: 1234,
                context: &[("iteration", "1")],
            })
            .unwrap();

        let (_, _) = logger
            .log_effect(&LogEffectParams {
                workspace: &workspace,
                log_path,
                phase: PipelinePhase::Development,
                effect: "WriteFile",
                primary_event: "FileWritten",
                extra_events: &["CheckpointSaved".to_string()],
                duration_ms: 12,
                context: &[],
            })
            .unwrap();

        // Verify log file exists
        assert!(workspace.exists(log_path));

        // Verify content
        let content = workspace.read(log_path).unwrap();
        assert!(content.contains("1 ts="));
        assert!(content.contains("phase=Development"));
        assert!(content.contains("effect=InvokePrompt"));
        assert!(content.contains("event=PromptCompleted"));
        assert!(content.contains("ms=1234"));
        assert!(content.contains("ctx=iteration=1"));

        assert!(content.contains("2 ts="));
        assert!(content.contains("effect=WriteFile"));
        assert!(content.contains("event=FileWritten"));
        assert!(content.contains("extra=[CheckpointSaved]"));
        assert!(content.contains("ms=12"));
    }

    #[test]
    fn test_event_loop_logger_sequence_increment() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");
        let mut logger = EventLoopLogger::new();

        // Log several effects
        for i in 0..5 {
            let (updated_logger, _) = logger
                .log_effect(&LogEffectParams {
                    workspace: &workspace,
                    log_path,
                    phase: PipelinePhase::Planning,
                    effect: "TestEffect",
                    primary_event: "TestEvent",
                    extra_events: &[],
                    duration_ms: 10 * i,
                    context: &[],
                })
                .unwrap();
            logger = updated_logger;
        }

        // Verify sequence numbers
        let content = workspace.read(log_path).unwrap();
        for i in 1..=5 {
            assert!(
                content.contains(&format!("{i} ts=")),
                "Should contain sequence number {i}"
            );
        }
    }

    #[test]
    fn test_event_loop_logger_context_formatting() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");
        let logger = EventLoopLogger::new();

        let (_, _) = logger
            .log_effect(&LogEffectParams {
                workspace: &workspace,
                log_path,
                phase: PipelinePhase::Review,
                effect: "InvokeReviewer",
                primary_event: "ReviewCompleted",
                extra_events: &[],
                duration_ms: 5000,
                context: &[
                    ("reviewer_pass", "2"),
                    ("agent_index", "3"),
                    ("retry_cycle", "1"),
                ],
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        assert!(content.contains("ctx=reviewer_pass=2,agent_index=3,retry_cycle=1"));
    }

    #[test]
    fn test_event_loop_logger_empty_context() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");
        let logger = EventLoopLogger::new();

        let (_, _) = logger
            .log_effect(&LogEffectParams {
                workspace: &workspace,
                log_path,
                phase: PipelinePhase::CommitMessage,
                effect: "GenerateCommit",
                primary_event: "CommitGenerated",
                extra_events: &[],
                duration_ms: 100,
                context: &[],
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        // Should not contain "ctx=" when context is empty
        assert!(!content.contains("ctx="));
        // Should not contain "extra=" when no extra events
        assert!(!content.contains("extra="));
    }

    #[test]
    fn test_event_loop_logger_from_existing_log() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");

        // Write some initial log entries
        {
            let mut logger = EventLoopLogger::new();
            for i in 0..3 {
                let (updated_logger, _) = logger
                    .log_effect(&LogEffectParams {
                        workspace: &workspace,
                        log_path,
                        phase: PipelinePhase::Development,
                        effect: "TestEffect",
                        primary_event: "TestEvent",
                        extra_events: &[],
                        duration_ms: 10 * i,
                        context: &[],
                    })
                    .unwrap();
                logger = updated_logger;
            }
        }

        // Create a new logger from the existing log
        let logger = EventLoopLogger::from_existing_log(&workspace, log_path).unwrap();

        // The next log entry should have seq=4
        let (_, _) = logger
            .log_effect(&LogEffectParams {
                workspace: &workspace,
                log_path,
                phase: PipelinePhase::Review,
                effect: "ResumeEffect",
                primary_event: "ResumeEvent",
                extra_events: &[],
                duration_ms: 100,
                context: &[],
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        // Should contain sequence 1-3 from initial writes
        assert!(content.contains("1 ts="));
        assert!(content.contains("2 ts="));
        assert!(content.contains("3 ts="));
        // Should contain sequence 4 from the resumed logger
        assert!(content.contains("4 ts="));
        // Should NOT contain another sequence 1
        let seq1_count = content.matches("1 ts=").count();
        assert_eq!(seq1_count, 1, "Should only have one '1 ts=' entry");
    }

    #[test]
    fn test_event_loop_logger_from_nonexistent_log() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");

        // Create a logger from a nonexistent log file
        let logger = EventLoopLogger::from_existing_log(&workspace, log_path).unwrap();

        // Should start at seq=1
        let (_, _) = logger
            .log_effect(&LogEffectParams {
                workspace: &workspace,
                log_path,
                phase: PipelinePhase::Development,
                effect: "TestEffect",
                primary_event: "TestEvent",
                extra_events: &[],
                duration_ms: 10,
                context: &[],
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        assert!(content.contains("1 ts="));
    }

    #[test]
    fn test_event_loop_logger_from_empty_log() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");

        // Create an empty log file
        workspace.write(log_path, "").unwrap();

        // Create a logger from an empty log file
        let logger = EventLoopLogger::from_existing_log(&workspace, log_path).unwrap();

        // Should start at seq=1
        let (_, _) = logger
            .log_effect(&LogEffectParams {
                workspace: &workspace,
                log_path,
                phase: PipelinePhase::Development,
                effect: "TestEffect",
                primary_event: "TestEvent",
                extra_events: &[],
                duration_ms: 10,
                context: &[],
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        assert!(content.contains("1 ts="));
    }
}
