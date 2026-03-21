use crate::reducer::event::PipelinePhase;
use crate::workspace::Workspace;
use std::path::Path;

pub struct LogEffectParams<'a> {
    pub workspace: &'a dyn Workspace,
    pub log_path: &'a Path,
    pub phase: PipelinePhase,
    pub effect: &'a str,
    pub primary_event: &'a str,
    pub extra_events: &'a [String],
    pub duration_ms: u64,
    pub context: &'a [(&'a str, &'a str)],
    pub timestamp: &'a str,
}

fn format_log_line_content(seq: u64, params: &LogEffectParams<'_>) -> String {
    let extra = if params.extra_events.is_empty() {
        String::new()
    } else {
        format!(" extra=[{}]", params.extra_events.join(","))
    };

    let ctx = if params.context.is_empty() {
        String::new()
    } else {
        let pairs: Vec<String> = params
            .context
            .iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect();
        format!(" ctx={}", pairs.join(","))
    };

    format!(
        "{} ts={} phase={} effect={} event={}{}{} ms={}\n",
        seq,
        params.timestamp,
        params.phase,
        params.effect,
        params.primary_event,
        extra,
        ctx,
        params.duration_ms
    )
}

#[derive(Clone)]
pub struct EventLoopLogger {
    seq: u64,
}

impl EventLoopLogger {
    #[must_use]
    pub const fn new() -> Self {
        Self { seq: 1 }
    }

    #[must_use]
    pub const fn seq(&self) -> u64 {
        self.seq
    }

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

    pub fn log_effect(self, params: &LogEffectParams<'_>) -> Result<(Self, u64), std::io::Error> {
        let line = format_log_line_content(self.seq, params);

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

    const TEST_TIMESTAMP: &str = "2026-01-01T00:00:00Z";

    #[test]
    fn test_event_loop_logger_basic() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");
        let logger = EventLoopLogger::new();

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
                timestamp: TEST_TIMESTAMP,
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
                timestamp: TEST_TIMESTAMP,
            })
            .unwrap();

        assert!(workspace.exists(log_path));

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

        let _ = (0..5).fold(EventLoopLogger::new(), |logger, i| {
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
                    timestamp: TEST_TIMESTAMP,
                })
                .unwrap();
            updated_logger
        });

        let content = workspace.read(log_path).unwrap();
        (1..=5).for_each(|i| {
            assert!(
                content.contains(&format!("{i} ts=")),
                "Should contain sequence number {i}"
            );
        });
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
                timestamp: TEST_TIMESTAMP,
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
                timestamp: TEST_TIMESTAMP,
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        assert!(!content.contains("ctx="));
        assert!(!content.contains("extra="));
    }

    #[test]
    fn test_event_loop_logger_from_existing_log() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");

        {
            let _ = (0..3).fold(EventLoopLogger::new(), |logger, i| {
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
                        timestamp: TEST_TIMESTAMP,
                    })
                    .unwrap();
                updated_logger
            });
        }

        let logger = EventLoopLogger::from_existing_log(&workspace, log_path).unwrap();

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
                timestamp: TEST_TIMESTAMP,
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        assert!(content.contains("1 ts="));
        assert!(content.contains("2 ts="));
        assert!(content.contains("3 ts="));
        assert!(content.contains("4 ts="));
        let seq1_count = content.matches("1 ts=").count();
        assert_eq!(seq1_count, 1, "Should only have one '1 ts=' entry");
    }

    #[test]
    fn test_event_loop_logger_from_nonexistent_log() {
        let tempdir = tempfile::tempdir().unwrap();
        let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

        let log_path = std::path::Path::new("event_loop.log");

        let logger = EventLoopLogger::from_existing_log(&workspace, log_path).unwrap();

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
                timestamp: TEST_TIMESTAMP,
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

        workspace.write(log_path, "").unwrap();

        let logger = EventLoopLogger::from_existing_log(&workspace, log_path).unwrap();

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
                timestamp: TEST_TIMESTAMP,
            })
            .unwrap();

        let content = workspace.read(log_path).unwrap();
        assert!(content.contains("1 ts="));
    }
}
