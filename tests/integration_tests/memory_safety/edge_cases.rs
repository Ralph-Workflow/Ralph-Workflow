//! Edge case tests for memory safety
//!
//! These tests verify corner cases and extreme scenarios for memory management:
//! - Extreme execution history limits (0, 1, very large)
//! - Checkpoint serialization edge cases
//! - Recovery from failures
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::{create_test_config_struct, create_test_registry, mock_executor_with_success};
use crate::test_timeout::with_default_timeout;
use crate::workflows::resume::make_checkpoint_with_execution_history;
use clap::error::ErrorKind;
use clap::Parser;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::app::{run_with_config_and_handlers, RunWithHandlersParams};
use ralph_workflow::checkpoint::execution_history::StepOutcome as CkptStepOutcome;
use ralph_workflow::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use ralph_workflow::config::MemoryConfigEnvironment;
use ralph_workflow::executor::ProcessExecutor;
use ralph_workflow::phases::PhaseContext;
use ralph_workflow::reducer::effect::{Effect, EffectResult};
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::{EffectHandler, PipelineState as ReducerPipelineState};
use ralph_workflow::workspace::{DirEntry, MemoryWorkspace, Workspace};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex};

/// Workspace wrapper that records file contents when they are removed.
///
/// The production pipeline clears `.agent/checkpoint.json` during finalization.
/// For resume/checkpoint tests, we want to assert on the *content* of the
/// checkpoint that was written right before it was cleared.
struct RecordingWorkspace {
    inner: MemoryWorkspace,
    removed: Mutex<HashMap<PathBuf, Vec<u8>>>,
}

impl RecordingWorkspace {
    fn new(inner: MemoryWorkspace) -> Self {
        Self {
            inner,
            removed: Mutex::new(HashMap::new()),
        }
    }

    fn removed_file_string(&self, path: &str) -> Option<String> {
        let removed = self.removed.lock().ok()?;
        removed
            .get(&PathBuf::from(path))
            .map(|b| String::from_utf8_lossy(b).to_string())
    }
}

impl Workspace for RecordingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> std::io::Result<String> {
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> std::io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> std::io::Result<()> {
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> std::io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> std::io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &Path) -> std::io::Result<()> {
        if self.inner.exists(relative) {
            if let Ok(bytes) = self.inner.read_bytes(relative) {
                if let Ok(mut removed) = self.removed.lock() {
                    removed.insert(relative.to_path_buf(), bytes);
                }
            }
        }
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> std::io::Result<()> {
        if self.inner.exists(relative) {
            self.remove(relative)
        } else {
            Ok(())
        }
    }

    fn remove_dir_all(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> std::io::Result<Vec<DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> std::io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> std::io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> std::io::Result<()> {
        self.inner.set_writable(relative)
    }
}

/// Helper function to create a test execution step.
fn create_test_step(iteration: u32) -> ExecutionStep {
    ExecutionStep::new(
        "Development",
        iteration,
        "agent_invoked",
        StepOutcome::success(Some("output".to_string()), vec!["file.rs".to_string()]),
    )
    .with_agent("test-agent")
    .with_duration(5)
}

/// Build a v3 checkpoint JSON with an oversized execution history.
/// Keep it modest for CI stability while still exceeding our configured limit.
fn make_execution_history_json(step_count: usize) -> String {
    let steps: Vec<serde_json::Value> = (0..step_count)
        .map(|i| {
            serde_json::json!({
                "phase": "Development",
                "iteration": i,
                "step_type": "legacy",
                "timestamp": "2024-01-01 12:00:00",
                "outcome": {"Success": {"output": null, "files_modified": []}},
                "agent": "test-agent",
                "duration_secs": 1
            })
        })
        .collect();

    serde_json::json!({
        "steps": steps,
        "file_snapshots": {}
    })
    .to_string()
}

/// Wrap the standard reducer `MockEffectHandler` but append a step during
/// event loop execution. This must appear in the completion checkpoint.
struct AppendHistoryHandler {
    inner: MockEffectHandler,
    appended: bool,
}

impl AppendHistoryHandler {
    fn new(state: ReducerPipelineState) -> Self {
        Self {
            inner: MockEffectHandler::new(state),
            appended: false,
        }
    }
}

impl EffectHandler<'_> for AppendHistoryHandler {
    fn execute(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> anyhow::Result<EffectResult> {
        if !self.appended {
            self.appended = true;
            let step = ralph_workflow::checkpoint::execution_history::ExecutionStep::new(
                "Development",
                1234,
                "appended_during_loop",
                CkptStepOutcome::success(Some("appended".to_string()), vec![]),
            );
            let _ = ctx
                .execution_history
                .add_step_bounded(step, ctx.config.execution_history_limit);
        }
        self.inner.execute(effect, ctx)
    }
}

impl ralph_workflow::app::event_loop::StatefulHandler for AppendHistoryHandler {
    fn update_state(&mut self, state: ReducerPipelineState) {
        self.inner.update_state(state);
    }
}

const LARGE_OUTPUT_BYTES: usize = 1024 * 1024;
const MANY_FILES_COUNT: usize = 200;
static LARGE_OUTPUT: LazyLock<String> = LazyLock::new(|| "x".repeat(LARGE_OUTPUT_BYTES));
static MANY_FILES: LazyLock<Vec<String>> = LazyLock::new(|| {
    (0..MANY_FILES_COUNT)
        .map(|i| format!("file_{i}.rs"))
        .collect()
});

#[test]
fn test_resume_binds_execution_history_and_completion_checkpoint_uses_updated_history() {
    with_default_timeout(|| {
        let oversized_history_json = make_execution_history_json(50);
        let checkpoint_json = make_checkpoint_with_execution_history(
            "/mock/repo",
            "Development",
            &oversized_history_json,
        );

        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file(
                "PROMPT.md",
                "# Test\n\n## Goal\nTest\n\n## Acceptance\n- Pass",
            )
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let mut config = create_test_config_struct();
        config.execution_history_limit = 10;
        let executor = mock_executor_with_success();

        let mut effect_handler = AppendHistoryHandler::new(ReducerPipelineState::initial(0, 0));

        let arg_vec: Vec<String> = vec!["ralph".to_string(), "--resume".to_string()];
        let parsed_args = match ralph_workflow::cli::Args::try_parse_from(&arg_vec) {
            Ok(parsed_args) => parsed_args,
            Err(e) if matches!(e.kind(), ErrorKind::DisplayVersion | ErrorKind::DisplayHelp) => {
                return;
            }
            Err(e) => panic!("failed to parse parsed_args: {e}"),
        };

        let cwd = app_handler.get_cwd();
        let mut ws = MemoryWorkspace::new(cwd);
        for (path, content) in app_handler.get_all_files() {
            if let Some(path_str) = path.to_str() {
                ws = ws.with_file(path_str, &content);
            }
        }
        let recording = Arc::new(RecordingWorkspace::new(ws));
        let workspace: Arc<dyn Workspace> = recording.clone();

        let registry = create_test_registry();
        let config_env = MemoryConfigEnvironment::new()
            .with_prompt_path(PathBuf::from("/mock/repo/PROMPT.md"))
            .with_unified_config_path(PathBuf::from("/mock/repo/.config/ralph-workflow.toml"));

        run_with_config_and_handlers(RunWithHandlersParams {
            args: parsed_args,
            executor: executor as Arc<dyn ProcessExecutor>,
            config,
            registry,
            path_resolver: &config_env,
            app_handler: &mut app_handler,
            effect_handler: &mut effect_handler,
            workspace: Some(Arc::clone(&workspace)),
            _marker: std::marker::PhantomData,
        })
        .unwrap();

        let saved = recording
            .removed_file_string(".agent/checkpoint.json")
            .unwrap_or_else(|| {
                workspace
                    .read(Path::new(".agent/checkpoint.json"))
                    .expect("expected checkpoint to be saved")
            });

        let value: serde_json::Value =
            serde_json::from_str(&saved).expect("saved checkpoint should be valid JSON");
        let steps = value
            .pointer("/execution_history/steps")
            .and_then(|v| v.as_array())
            .expect("checkpoint should contain execution_history.steps array");

        assert_eq!(
            steps.len(),
            10,
            "completion checkpoint should cap execution history to configured limit"
        );

        let contains_appended = steps
            .iter()
            .any(|s| s.get("step_type").and_then(|v| v.as_str()) == Some("appended_during_loop"));
        assert!(
            contains_appended,
            "completion checkpoint should include steps appended during event loop execution"
        );
    });
}

#[test]
fn test_execution_history_limit_zero_prevents_all_growth() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(100, 5);
        let limit = 0; // extreme case: no history allowed

        // Add 100 entries with limit=0
        for i in 0..100 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // With limit=0, no history should be retained
        assert_eq!(
            state.execution_history.len(),
            0,
            "Execution history with limit=0 should retain no entries"
        );
    });
}

#[test]
fn test_execution_history_limit_one_retains_only_latest() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(100, 5);
        let limit = 1; // extreme case: only keep latest entry

        // Add 100 entries with limit=1
        for i in 0..100 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // With limit=1, only the last entry should be retained
        assert_eq!(
            state.execution_history.len(),
            1,
            "Execution history with limit=1 should retain only 1 entry"
        );

        // Verify it's the most recent entry (iteration 99)
        assert_eq!(
            state.execution_history[0].iteration, 99,
            "Should retain the most recent entry"
        );
    });
}

#[test]
fn test_execution_history_very_large_limit_works() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(100, 5);
        let limit = 1_000_000; // very large limit (unlikely in practice)

        // Add 100 entries with very large limit
        for i in 0..100 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // All entries should be retained since we're under the limit
        assert_eq!(
            state.execution_history.len(),
            100,
            "Execution history should retain all entries when under large limit"
        );
        // Verify first and last entries have expected iterations
        assert_eq!(
            state.execution_history[0].iteration, 0,
            "First entry should have iteration 0"
        );
        assert_eq!(
            state.execution_history[99].iteration, 99,
            "Last entry should have iteration 99"
        );
    });
}

#[test]
fn test_execution_history_bounding_at_exact_limit() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(100, 5);
        let limit = 50;

        // Add exactly 50 entries (at the limit)
        for i in 0..50 {
            state.add_execution_step(create_test_step(i), limit);
        }

        assert_eq!(
            state.execution_history.len(),
            50,
            "Should retain all entries when exactly at limit"
        ); // OK: content checked after bounding below

        // Add one more entry (should trigger bounding)
        state.add_execution_step(create_test_step(50), limit);

        assert_eq!(
            state.execution_history.len(),
            50,
            "Should maintain limit after adding one more entry"
        );

        // First entry should now be iteration 1 (iteration 0 dropped)
        assert_eq!(
            state.execution_history[0].iteration, 1,
            "Oldest entry should have been dropped"
        );
    });
}

#[test]
fn test_execution_history_large_single_step() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(10, 5);
        let limit = 1000;

        // Create a step with large output.
        // Keep this sized for CI stability while still exercising large allocations.
        let large_step = ExecutionStep::new(
            "Development",
            0,
            "agent_invoked",
            StepOutcome::success(Some(LARGE_OUTPUT.clone()), vec!["file.rs".to_string()]),
        );

        state.add_execution_step(large_step, limit);

        // Should handle large individual steps without panic
        assert_eq!(
            state.execution_history.len(),
            1,
            "Should successfully add very large execution step"
        );
        assert_eq!(
            &*state.execution_history[0].phase, "Development",
            "Large step should preserve phase"
        );
    });
}

#[test]
fn test_execution_history_many_files_modified() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(10, 5);
        let limit = 1000;

        // Create a step that modified many files
        let step_with_many_files = ExecutionStep::new(
            "Development",
            0,
            "agent_invoked",
            StepOutcome::success(Some("output".to_string()), MANY_FILES.clone()),
        );

        state.add_execution_step(step_with_many_files, limit);

        // Should handle steps with many files modified
        assert_eq!(
            state.execution_history.len(),
            1,
            "Should successfully add step with many files modified"
        );

        // Verify files_modified is preserved
        if let StepOutcome::Success { files_modified, .. } = &state.execution_history[0].outcome {
            assert_eq!(
                files_modified.as_ref().map_or(0, |files| files.len()),
                MANY_FILES_COUNT,
                "Should preserve all files_modified entries"
            );
        } else {
            panic!("Expected Success outcome");
        }
    });
}

#[test]
fn test_execution_history_rapid_limit_changes() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(100, 5);

        // Add entries with varying limits
        for i in 0..20 {
            state.add_execution_step(create_test_step(i), 100);
        }
        assert_eq!(state.execution_history.len(), 20); // OK: precondition before limit change; content checked after rebounding

        // Switch to smaller limit mid-execution
        for i in 20..40 {
            state.add_execution_step(create_test_step(i), 10);
        }

        // History should be bounded to 10 (the new limit)
        assert_eq!(
            state.execution_history.len(),
            10,
            "Should enforce new smaller limit"
        );

        // Verify we have the most recent entries (30-39)
        assert_eq!(
            state.execution_history[0].iteration, 30,
            "Should have oldest entry from recent window"
        );
        assert_eq!(
            state.execution_history[9].iteration, 39,
            "Should have newest entry from recent window"
        );
    });
}

#[test]
fn test_checkpoint_serialization_with_empty_history() {
    with_default_timeout(|| {
        let state = PipelineState::initial(100, 5);

        // Serialize state with empty execution history
        let json = serde_json::to_string(&state).expect("Should serialize empty state");

        // Deserialize back
        let _deserialized: PipelineState =
            serde_json::from_str(&json).expect("Should deserialize empty state");

        // Empty history should serialize/deserialize correctly
        assert!(json.contains("execution_history"));
    });
}

#[test]
fn test_checkpoint_serialization_roundtrip_preserves_bounded_history() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(100, 5);
        let limit = 50;

        // Add 100 entries with limit=50
        for i in 0..100 {
            state.add_execution_step(create_test_step(i), limit);
        }

        assert_eq!(state.execution_history.len(), 50); // OK: content checked via deserialized iteration values below

        // Serialize
        let json = serde_json::to_string(&state).expect("Should serialize");

        // Deserialize
        let deserialized: PipelineState = serde_json::from_str(&json).expect("Should deserialize");

        // Verify bounded history is preserved
        assert_eq!(
            deserialized.execution_history.len(),
            50,
            "Deserialized state should preserve bounded history length"
        );

        // Verify entries are the most recent ones (50-99)
        assert_eq!(
            deserialized.execution_history[0].iteration, 50,
            "Deserialized state should have oldest entry from bounded window"
        );
        assert_eq!(
            deserialized.execution_history[49].iteration, 99,
            "Deserialized state should have newest entry from bounded window"
        );
    });
}

#[test]
fn test_execution_history_with_all_outcome_types() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(100, 5);
        let limit = 1000;

        // Add steps with different outcome types
        state.add_execution_step(
            ExecutionStep::new(
                "Development",
                0,
                "agent_invoked",
                StepOutcome::success(Some("success output".to_string()), vec![]),
            ),
            limit,
        );

        state.add_execution_step(
            ExecutionStep::new(
                "Review",
                1,
                "review_completed",
                StepOutcome::failure("error message".to_string(), true),
            ),
            limit,
        );

        state.add_execution_step(
            ExecutionStep::new(
                "Development",
                2,
                "continuation",
                StepOutcome::partial("completed part".to_string(), "remaining work".to_string()),
            ),
            limit,
        );

        state.add_execution_step(
            ExecutionStep::new(
                "Review",
                3,
                "skipped",
                StepOutcome::skipped("no review needed".to_string()),
            ),
            limit,
        );

        // All outcome types should be handled correctly
        assert_eq!(
            state.execution_history.len(),
            4,
            "Should handle all outcome types"
        );

        // Verify each outcome type is present with expected data
        assert!(
            matches!(
                &state.execution_history[0].outcome,
                StepOutcome::Success { output, .. } if output.as_deref() == Some("success output")
            ),
            "First entry should be Success with expected output"
        );
        assert!(
            matches!(
                &state.execution_history[1].outcome,
                StepOutcome::Failure { error, .. } if &**error == "error message"
            ),
            "Second entry should be Failure with expected error"
        );
        assert!(
            matches!(
                &state.execution_history[2].outcome,
                StepOutcome::Partial { completed, .. } if &**completed == "completed part"
            ),
            "Third entry should be Partial with expected completed text"
        );
        assert!(
            matches!(
                &state.execution_history[3].outcome,
                StepOutcome::Skipped { reason } if &**reason == "no review needed"
            ),
            "Fourth entry should be Skipped with expected reason"
        );

        // Verify serialization roundtrip preserves all outcome types
        let json = serde_json::to_string(&state).expect("Should serialize all outcome types");
        let deserialized: PipelineState =
            serde_json::from_str(&json).expect("Should deserialize all outcome types");
        assert_eq!(
            deserialized.execution_history.len(),
            4,
            "Deserialized state should preserve all entries"
        );
        assert!(
            matches!(
                &deserialized.execution_history[0].outcome,
                StepOutcome::Success { .. }
            ),
            "Deserialized first entry should remain Success"
        );
        assert!(
            matches!(
                &deserialized.execution_history[3].outcome,
                StepOutcome::Skipped { .. }
            ),
            "Deserialized last entry should remain Skipped"
        );
    });
}
