// Tests for memory metrics: snapshots, collectors, and backends.

use crate::checkpoint::execution_history::{
    ExecutionStep, IssuesSummary, ModifiedFilesDetail, StepOutcome,
};
use crate::logger::output::TestLogger;
use crate::reducer::PipelineState;
use std::rc::Rc;

use super::io::SnapshotCounter;

#[test]
fn test_execution_history_heap_estimate_uses_len_not_capacity() {
    let timestamp = String::from("t");
    let file = String::from("f");
    let checkpoint_saved_at = String::from("c");
    let git_commit_oid = String::from("g");
    let prompt_used = String::from("p");
    let issues_desc = String::from("i");
    let added = String::from("a");
    let modified = String::from("m");
    let deleted = String::from("d");

    let step = ExecutionStep {
        phase: std::sync::Arc::from("P"),
        iteration: 0,
        step_type: Box::from("T"),
        timestamp,
        outcome: StepOutcome::Success {
            output: None,
            files_modified: Some(vec![file].into_boxed_slice()),
            exit_code: Some(0),
        },
        agent: Some(std::sync::Arc::from("A")),
        duration_secs: None,
        checkpoint_saved_at: Some(checkpoint_saved_at),
        git_commit_oid: Some(git_commit_oid),
        modified_files_detail: Some(ModifiedFilesDetail {
            added: Some(vec![added].into_boxed_slice()),
            modified: Some(vec![modified].into_boxed_slice()),
            deleted: Some(vec![deleted].into_boxed_slice()),
        }),
        prompt_used: Some(prompt_used),
        issues_summary: Some(IssuesSummary {
            found: 0,
            fixed: 0,
            description: Some(issues_desc),
        }),
    };

    let state = PipelineState::initial(100, 5).with_execution_step(step, 1000);

    let bytes = super::snapshot::estimate_execution_history_heap_size(&state);
    let expected = "P".len()
        + "T".len()
        + "t".len()
        + "A".len()
        + "f".len()
        + "c".len()
        + "g".len()
        + "p".len()
        + "i".len()
        + "a".len()
        + "m".len()
        + "d".len();

    assert_eq!(
        bytes, expected,
        "heap estimate should be a deterministic length-based proxy"
    );
}

#[test]
fn test_memory_snapshot_captures_state() {
    let state = PipelineState::initial(100, 5).with_execution_step(
        ExecutionStep::new(
            "Development",
            0,
            "agent_invoked",
            StepOutcome::success(Some("output".to_string()), vec!["file.rs".to_string()]),
        ),
        1000,
    );

    let snap = MemorySnapshot::from_pipeline_state(&state);

    assert_eq!(snap.iteration, 0);
    assert_eq!(snap.execution_history_len, 1);
    assert!(snap.execution_history_heap_bytes > 0);
}

#[test]
fn test_metrics_collector_respects_interval() {
    let state = PipelineState::initial(100, 5);
    let iterations = [0, 1, 5, 10];
    let collector = iterations
        .into_iter()
        .fold(MemoryMetricsCollector::new(10), |c, i| {
            let s = PipelineState {
                iteration: i,
                ..state.clone()
            };
            c.maybe_record(&s)
        });
    assert_eq!(collector.snapshots().len(), 2);
}

#[test]
fn test_metrics_collector_retains_bounded_snapshots_by_default() {
    let state = PipelineState::initial(100, 5);
    let collector = (1..=2000).fold(MemoryMetricsCollector::new(1), |c, i| {
        let s = PipelineState {
            iteration: i,
            ..state.clone()
        };
        c.maybe_record(&s)
    });

    let snapshots = collector.snapshots();
    assert!(
        snapshots.len() <= 1024,
        "expected default snapshot retention to be bounded"
    );
    assert_eq!(
        snapshots
            .last()
            .expect("should record at least one snapshot")
            .iteration,
        2000
    );
    assert_eq!(
        snapshots
            .first()
            .expect("should record at least one snapshot")
            .iteration,
        2000 - u32::try_from(snapshots.len()).expect("snapshot count fits in u32") + 1
    );
}

#[test]
fn test_telemetry_backend_noop() {
    let backend = NoOpBackend;
    let state = PipelineState::initial(100, 5);
    let snap = MemorySnapshot::from_pipeline_state(&state);

    // Should not panic
    backend.emit_snapshot(&snap);
    backend.emit_warning("test warning");
    backend.flush();
}

#[test]
fn test_record_and_emit_integrates_with_backend() {
    struct CountingBackend {
        snapshot_count: SnapshotCounter,
    }

    impl TelemetryBackend for CountingBackend {
        fn emit_snapshot(&self, _snapshot: &MemorySnapshot) {
            self.snapshot_count.increment();
        }
        fn emit_warning(&self, _message: &str) {}
        fn flush(&self) {}
    }

    let backend = CountingBackend {
        snapshot_count: SnapshotCounter::new(),
    };
    let state = PipelineState::initial(100, 5);
    let iterations = [0, 1, 5, 10];
    let collector = iterations
        .into_iter()
        .fold(MemoryMetricsCollector::new(10), |c, i| {
            let s = PipelineState {
                iteration: i,
                ..state.clone()
            };
            c.record_and_emit(&s, &backend)
        });
    assert_eq!(collector.snapshots().len(), 2);
}

#[test]
fn test_logging_backend_emits_warnings_above_threshold() {
    let logger = Rc::new(TestLogger::new());
    let backend = LoggingBackend::with_logger(100, logger.clone());
    let state = PipelineState::initial(100, 5);
    let iterations = 0..50;
    let _collector = iterations.fold(MemoryMetricsCollector::new(10), |c, i| {
        let s = state.clone().with_execution_step(
            ExecutionStep::new(
                "Development",
                i,
                "agent_invoked",
                StepOutcome::success(
                    Some("output with sufficient content".to_string()),
                    vec!["file.rs".to_string()],
                ),
            ),
            1000,
        );
        c.record_and_emit(&s, &backend)
    });

    let snap = MemorySnapshot::from_pipeline_state(&state);
    assert!(
        snap.execution_history_heap_bytes > 100,
        "Test setup should create heap usage > 100 bytes"
    );

    // This should emit both snapshot and warning
    backend.emit_snapshot(&snap);
    let logs = logger.get_logs();
    assert!(logs.iter().any(|l| l.contains("[METRICS]")));
    assert!(logs.iter().any(|l| l.contains("[METRICS WARNING]")));
}

#[test]
fn test_memory_metrics_library_code_does_not_write_directly_to_stderr() {
    // Writing to stderr from library code bypasses the project's logger and
    // can spam output in production. Logging should route through Loggable.
    let src_mod = include_str!("mod.rs");
    let src_snapshot = include_str!("snapshot.rs");
    let src_collector = include_str!("collector.rs");
    let src_backends = include_str!("backends.rs");
    [
        ("mod.rs", src_mod),
        ("snapshot.rs", src_snapshot),
        ("collector.rs", src_collector),
        ("backends.rs", src_backends),
    ]
    .into_iter()
    .for_each(|(name, src)| {
        assert!(
            !src.contains("eprintln!(\"[METRICS]")
                && !src.contains("eprintln!(\"[METRICS WARNING]"),
            "memory_metrics/{name} should not use eprintln! in library code"
        );
    });
}
