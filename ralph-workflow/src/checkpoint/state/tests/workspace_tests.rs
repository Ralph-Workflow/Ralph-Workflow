use super::*;
use crate::checkpoint::{
    AgentConfigSnapshot, CliArgsSnapshot, PipelineCheckpointParams, PipelinePhase,
    PipelinePipelineCheckpoint, RebaseState,
};
use crate::workspace::MemoryWorkspace;
use std::path::Path;

/// Helper function to create a checkpoint for workspace tests.
fn make_test_checkpoint_for_workspace(
    phase: PipelinePhase,
    iteration: u32,
) -> PipelinePipelineCheckpoint {
    let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
    let dev_config =
        AgentConfigSnapshot::new("claude".into(), "cmd".into(), "-o".into(), None, true);
    let rev_config =
        AgentConfigSnapshot::new("codex".into(), "cmd".into(), "-o".into(), None, true);
    let run_id = uuid::Uuid::new_v4().to_string();
    PipelinePipelineCheckpoint::from_params(PipelineCheckpointParams {
        phase,
        iteration,
        total_iterations: 5,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        developer_agent: "claude",
        reviewer_agent: "codex",
        cli_args,
        developer_agent_config: dev_config,
        reviewer_agent_config: rev_config,
        rebase_state: RebaseState::default(),
        git_user_name: None,
        git_user_email: None,
        run_id: &run_id,
        parent_run_id: None,
        resume_count: 0,
        actual_developer_runs: iteration,
        actual_reviewer_runs: 0,
        working_dir: "/test/repo".to_string(),
        prompt_md_checksum: None,
        config_path: None,
        config_checksum: None,
    })
}

#[test]
fn test_calculate_file_checksum_with_workspace() {
    let workspace = MemoryWorkspace::new_test().with_file("test.txt", "test content");

    let checksum = calculate_file_checksum_with_workspace(&workspace, Path::new("test.txt"));
    assert!(checksum.is_some());

    // Same content should give same checksum
    let workspace2 = MemoryWorkspace::new_test().with_file("other.txt", "test content");
    let checksum2 = calculate_file_checksum_with_workspace(&workspace2, Path::new("other.txt"));
    assert_eq!(checksum, checksum2);
}

#[test]
fn test_calculate_file_checksum_with_workspace_different_content() {
    let workspace1 = MemoryWorkspace::new_test().with_file("test.txt", "content A");
    let workspace2 = MemoryWorkspace::new_test().with_file("test.txt", "content B");

    let checksum1 = calculate_file_checksum_with_workspace(&workspace1, Path::new("test.txt"));
    let checksum2 = calculate_file_checksum_with_workspace(&workspace2, Path::new("test.txt"));

    assert!(checksum1.is_some());
    assert!(checksum2.is_some());
    assert_ne!(checksum1, checksum2);
}

#[test]
fn test_calculate_file_checksum_with_workspace_nonexistent() {
    let workspace = MemoryWorkspace::new_test();

    let checksum = calculate_file_checksum_with_workspace(&workspace, Path::new("nonexistent.txt"));
    assert!(checksum.is_none());
}

#[test]
fn test_save_checkpoint_with_workspace() {
    let workspace = MemoryWorkspace::new_test();
    let checkpoint = make_test_checkpoint_for_workspace(PipelinePhase::Development, 2);

    save_checkpoint_with_workspace(&workspace, &checkpoint).unwrap();

    assert!(workspace.exists(Path::new(".agent/checkpoint.json")));
}

#[test]
fn test_checkpoint_exists_with_workspace() {
    let workspace = MemoryWorkspace::new_test();

    assert!(!checkpoint_exists_with_workspace(&workspace));

    let checkpoint = make_test_checkpoint_for_workspace(PipelinePhase::Development, 1);
    save_checkpoint_with_workspace(&workspace, &checkpoint).unwrap();

    assert!(checkpoint_exists_with_workspace(&workspace));
}

#[test]
fn test_load_checkpoint_with_workspace_nonexistent() {
    let workspace = MemoryWorkspace::new_test();

    let result = load_checkpoint_with_workspace(&workspace).unwrap();
    assert!(result.is_none());
}

#[test]
fn test_save_and_load_checkpoint_with_workspace() {
    let workspace = MemoryWorkspace::new_test();
    let checkpoint = make_test_checkpoint_for_workspace(PipelinePhase::Review, 5);

    save_checkpoint_with_workspace(&workspace, &checkpoint).unwrap();

    let loaded = load_checkpoint_with_workspace(&workspace)
        .unwrap()
        .expect("checkpoint should exist");

    assert_eq!(loaded.phase, PipelinePhase::Review);
    assert_eq!(loaded.iteration, 5);
    assert_eq!(loaded.developer_agent, "claude");
    assert_eq!(loaded.reviewer_agent, "codex");
}

#[test]
fn test_clear_checkpoint_with_workspace() {
    let workspace = MemoryWorkspace::new_test();
    let checkpoint = make_test_checkpoint_for_workspace(PipelinePhase::Development, 1);

    save_checkpoint_with_workspace(&workspace, &checkpoint).unwrap();
    assert!(checkpoint_exists_with_workspace(&workspace));

    clear_checkpoint_with_workspace(&workspace).unwrap();
    assert!(!checkpoint_exists_with_workspace(&workspace));
}

#[test]
fn test_clear_checkpoint_with_workspace_nonexistent() {
    let workspace = MemoryWorkspace::new_test();

    // Should not error when checkpoint doesn't exist
    clear_checkpoint_with_workspace(&workspace).unwrap();
}

#[test]
fn test_load_checkpoint_rejects_v1_format() {
    // Test that loading a v1 checkpoint is rejected (legacy migration removed)
    let json = r#"{
        "version": 1,
        "phase": "Development",
        "iteration": 1,
        "total_iterations": 1,
        "reviewer_pass": 0,
        "total_reviewer_passes": 0,
        "timestamp": "2024-01-01 12:00:00",
        "developer_agent": "test-agent",
        "reviewer_agent": "test-agent",
        "cli_args": {
            "developer_iters": 1,
            "reviewer_reviews": 0,
            "commit_msg": "",
            "review_depth": null
        },
        "developer_agent_config": {
            "name": "test-agent",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "reviewer_agent_config": {
            "name": "test-agent",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "rebase_state": "NotStarted",
        "config_path": null,
        "config_checksum": null,
        "working_dir": "/some/other/directory",
        "prompt_md_checksum": null,
        "git_user_name": null,
        "git_user_email": null
    }"#;

    let workspace = MemoryWorkspace::new_test().with_file(".agent/checkpoint.json", json);

    let result = load_checkpoint_with_workspace(&workspace);
    assert!(
        result.is_err(),
        "v1 checkpoint should be rejected: {result:?}"
    );
    let err = result.unwrap_err();
    assert!(
        err.to_string().contains("no longer supported"),
        "Error should mention legacy not supported: {err}"
    );
}

#[test]
fn test_load_checkpoint_migrates_v2_to_v3() {
    // v2 checkpoints are still structurally compatible with v3 and can be
    // migrated in-memory by bumping the version while leaving v3-only fields empty.
    let json = r#"{
        "version": 2,
        "phase": "Development",
        "iteration": 2,
        "total_iterations": 3,
        "reviewer_pass": 0,
        "total_reviewer_passes": 1,
        "timestamp": "2026-02-13 12:00:00",
        "developer_agent": "claude",
        "reviewer_agent": "codex",
        "cli_args": {
            "developer_iters": 3,
            "reviewer_reviews": 1,
            "review_depth": null,
            "isolation_mode": true,
            "verbosity": 2,
            "show_streaming_metrics": false,
            "reviewer_json_parser": null
        },
        "developer_agent_config": {
            "name": "claude",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "reviewer_agent_config": {
            "name": "codex",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "rebase_state": "NotStarted",
        "config_path": null,
        "config_checksum": null,
        "working_dir": "/tmp",
        "prompt_md_checksum": null,
        "git_user_name": null,
        "git_user_email": null,
        "run_id": "run-test",
        "parent_run_id": null,
        "resume_count": 0,
        "actual_developer_runs": 1,
        "actual_reviewer_runs": 0
    }"#;

    let workspace = MemoryWorkspace::new_test().with_file(".agent/checkpoint.json", json);

    let loaded = load_checkpoint_with_workspace(&workspace)
        .unwrap()
        .expect("checkpoint should exist");

    assert_eq!(loaded.version, 3, "v2 checkpoint should be migrated to v3");
    assert_eq!(loaded.phase, PipelinePhase::Development);
    assert_eq!(loaded.iteration, 2);
    assert_eq!(loaded.total_iterations, 3);
    assert_eq!(loaded.run_id, "run-test");
    assert!(loaded.execution_history.is_none());
    assert!(loaded.file_system_state.is_none());
}

#[test]
fn test_load_checkpoint_rejects_newer_checkpoint_versions() {
    let json = r#"{
        "version": 4,
        "phase": "Development",
        "iteration": 1,
        "total_iterations": 1,
        "reviewer_pass": 0,
        "total_reviewer_passes": 0,
        "timestamp": "2026-02-13 12:00:00",
        "developer_agent": "claude",
        "reviewer_agent": "codex",
        "cli_args": {
            "developer_iters": 1,
            "reviewer_reviews": 0,
            "review_depth": null,
            "isolation_mode": true,
            "verbosity": 2,
            "show_streaming_metrics": false,
            "reviewer_json_parser": null
        },
        "developer_agent_config": {
            "name": "claude",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "reviewer_agent_config": {
            "name": "codex",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "rebase_state": "NotStarted",
        "config_path": null,
        "config_checksum": null,
        "working_dir": "/tmp",
        "prompt_md_checksum": null,
        "git_user_name": null,
        "git_user_email": null,
        "run_id": "run-test",
        "parent_run_id": null,
        "resume_count": 0,
        "actual_developer_runs": 1,
        "actual_reviewer_runs": 0
    }"#;

    let workspace = MemoryWorkspace::new_test().with_file(".agent/checkpoint.json", json);

    let result = load_checkpoint_with_workspace(&workspace);
    assert!(
        result.is_err(),
        "newer checkpoint versions must be rejected"
    );
    let err = result.unwrap_err();
    assert!(
        err.to_string().contains("newer") || err.to_string().contains("upgrade"),
        "error should suggest upgrading: {err}"
    );
}

#[test]
fn test_load_checkpoint_rejects_legacy_phase_variants() {
    let base_json = r#"{
        "version": 3,
        "phase": "%PHASE%",
        "iteration": 1,
        "total_iterations": 1,
        "reviewer_pass": 0,
        "total_reviewer_passes": 0,
        "timestamp": "2024-01-01 12:00:00",
        "developer_agent": "test-agent",
        "reviewer_agent": "test-agent",
        "cli_args": {
            "developer_iters": 1,
            "reviewer_reviews": 0,
            "commit_msg": "",
            "review_depth": null
        },
        "developer_agent_config": {
            "name": "test-agent",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "reviewer_agent_config": {
            "name": "test-agent",
            "cmd": "echo",
            "output_flag": "",
            "yolo_flag": null,
            "can_commit": false,
            "model_override": null,
            "provider_override": null,
            "context_level": 1
        },
        "rebase_state": "NotStarted",
        "config_path": null,
        "config_checksum": null,
        "working_dir": "/some/other/directory",
        "prompt_md_checksum": null,
        "git_user_name": null,
        "git_user_email": null
    }"#;

    ["Fix", "ReviewAgain"].iter().for_each(|phase_label| {
        let json = base_json.replace("%PHASE%", phase_label);
        let workspace = MemoryWorkspace::new_test().with_file(".agent/checkpoint.json", &json);

        let result = load_checkpoint_with_workspace(&workspace);
        assert!(
            result.is_err(),
            "Legacy phase '{phase_label}' should be rejected"
        );
        let err = result.unwrap_err();
        assert!(
            err.to_string().contains("no longer supported"),
            "Error for '{phase_label}' should mention 'no longer supported': {err}"
        );
    });
}

#[test]
fn test_pipeline_phase_deserialize_rejects_legacy_variants() {
    let fix_result: Result<PipelinePhase, _> = serde_json::from_str("\"Fix\"");
    assert!(fix_result.is_err(), "Fix phase should be rejected");
    let err = fix_result.unwrap_err().to_string();
    assert!(
        err.contains("no longer supported"),
        "Error should mention 'no longer supported': {err}"
    );

    let review_again_result: Result<PipelinePhase, _> = serde_json::from_str("\"ReviewAgain\"");
    assert!(
        review_again_result.is_err(),
        "ReviewAgain phase should be rejected"
    );
    let err = review_again_result.unwrap_err().to_string();
    assert!(
        err.contains("no longer supported"),
        "Error should mention 'no longer supported': {err}"
    );
}

// =========================================================================
// Optimized checkpoint serialization tests (Step 11)
// =========================================================================

#[test]
fn test_optimized_serialization_produces_compact_json() {
    use crate::checkpoint::execution_history::{ExecutionHistory, ExecutionStep, StepOutcome};

    let workspace = MemoryWorkspace::new_test();
    let outcome = StepOutcome::success(Some("test".to_string()), vec![]);
    let step = ExecutionStep::new("Planning", 1, "plan", outcome);
    let history = ExecutionHistory {
        steps: std::collections::VecDeque::from([step]),
        file_snapshots: std::collections::HashMap::new(),
    };
    let checkpoint = PipelineCheckpoint {
        execution_history: Some(history),
        ..make_test_checkpoint_for_workspace(PipelinePhase::Development, 2)
    };

    save_checkpoint_with_workspace(&workspace, &checkpoint).unwrap();

    let saved_json = workspace.read(Path::new(".agent/checkpoint.json")).unwrap();

    // Verify compact JSON: should NOT have pretty-printing indentation
    // (we allow some minimal spaces for JSON structure, but no multi-line pretty formatting)
    let line_count = saved_json.lines().count();
    // Compact JSON should be just a few lines (not hundreds)
    assert!(
        line_count < 10,
        "Compact JSON should have minimal lines, got {line_count}"
    );
}

#[test]
fn test_optimized_serialization_round_trip_preserves_data() {
    use crate::checkpoint::execution_history::{ExecutionHistory, ExecutionStep, StepOutcome};

    let workspace = MemoryWorkspace::new_test();

    let steps: std::collections::VecDeque<ExecutionStep> = (0..10)
        .map(|i| {
            let outcome = StepOutcome::Success {
                output: Some(format!("output{i}").into()),
                files_modified: Some(vec![format!("file{i}.rs", i)].into_boxed_slice()),
                exit_code: Some(0),
            };
            ExecutionStep::new(&format!("Phase{i}"), i, &format!("step{i}"), outcome)
                .with_agent(&format!("agent{i}"))
                .with_duration(100 + u64::from(i))
        })
        .collect();
    let history = ExecutionHistory {
        steps,
        file_snapshots: std::collections::HashMap::new(),
    };
    let checkpoint = PipelineCheckpoint {
        execution_history: Some(history),
        ..make_test_checkpoint_for_workspace(PipelinePhase::Review, 3)
    };

    // Save with optimized serialization
    save_checkpoint_with_workspace(&workspace, &checkpoint).unwrap();

    // Load and verify data preservation
    let loaded = load_checkpoint_with_workspace(&workspace)
        .unwrap()
        .expect("checkpoint should exist");

    assert_eq!(loaded.phase, checkpoint.phase);
    assert_eq!(loaded.iteration, checkpoint.iteration);
    assert_eq!(loaded.developer_agent, checkpoint.developer_agent);

    // Verify execution history preservation
    let loaded_history = loaded.execution_history.expect("history should exist");
    assert_eq!(loaded_history.steps.len(), 10);

    loaded_history
        .steps
        .iter()
        .enumerate()
        .for_each(|(i, step)| {
            assert_eq!(step.phase.as_ref(), format!("Phase{i}"));
            assert_eq!(step.iteration, u32::try_from(i).expect("value fits in u32"));
            assert_eq!(step.step_type.as_ref(), format!("step{i}"));
            assert_eq!(
                step.agent.as_ref().map(std::convert::AsRef::as_ref),
                Some(format!("agent{i}").as_str())
            );
            assert_eq!(step.duration_secs, Some(100 + i as u64));

            if let StepOutcome::Success {
                output,
                files_modified,
                exit_code,
            } = &step.outcome
            {
                assert_eq!(
                    output.as_ref().map(std::convert::AsRef::as_ref),
                    Some(format!("output{i}").as_str())
                );
                assert_eq!(
                    files_modified.as_ref().map(|f| f
                        .iter()
                        .map(std::string::String::as_str)
                        .collect::<Vec<_>>()),
                    Some(vec![format!("file{i}.rs").as_str()])
                );
                assert_eq!(*exit_code, Some(0));
            } else {
                panic!("Expected Success outcome");
            }
        });
}

#[test]
fn test_estimate_checkpoint_size_is_reasonable() {
    use crate::checkpoint::execution_history::{ExecutionHistory, ExecutionStep, StepOutcome};

    let workspace = MemoryWorkspace::new_test();

    // Test with empty history
    let checkpoint_empty = make_test_checkpoint_for_workspace(PipelinePhase::Planning, 1);
    save_checkpoint_with_workspace(&workspace, &checkpoint_empty).unwrap();
    let empty_json = workspace.read(Path::new(".agent/checkpoint.json")).unwrap();
    let empty_size = empty_json.len();

    let empty_estimate = estimate_checkpoint_size(&checkpoint_empty);
    assert!(
        empty_estimate >= empty_size,
        "estimate should be conservative for empty checkpoints"
    );

    // Base size should be within 50% of actual (conservative estimate)
    assert!(
        empty_size <= 15_000,
        "Empty checkpoint should be < 15KB, got {empty_size}"
    );

    // Test with 100 entries
    let steps: std::collections::VecDeque<ExecutionStep> = (0..100)
        .map(|i| {
            let outcome = StepOutcome::Success {
                output: Some("test output".to_string().into()),
                files_modified: Some(vec!["file.rs".to_string()].into_boxed_slice()),
                exit_code: Some(0),
            };
            ExecutionStep::new("Development", i, "test", outcome)
                .with_agent("agent")
                .with_duration(100)
        })
        .collect();
    let history = ExecutionHistory {
        steps,
        file_snapshots: std::collections::HashMap::new(),
    };
    let checkpoint_100 = PipelineCheckpoint {
        execution_history: Some(history),
        ..make_test_checkpoint_for_workspace(PipelinePhase::Development, 5)
    };

    save_checkpoint_with_workspace(&workspace, &checkpoint_100).unwrap();
    let json_100 = workspace.read(Path::new(".agent/checkpoint.json")).unwrap();
    let size_100 = json_100.len();

    let estimate_100 = estimate_checkpoint_size(&checkpoint_100);
    assert!(
        estimate_100 >= size_100,
        "estimate should be conservative for 100-entry checkpoints"
    );

    assert!(
        estimate_100 <= size_100.saturating_mul(4).saturating_add(10_000),
        "estimate should not over-allocate excessively"
    );

    // Growth should roughly scale with history length.
    assert!(
        size_100 > empty_size,
        "serialized checkpoint should grow with execution history"
    );
}

#[test]
fn test_estimate_checkpoint_size_is_overflow_safe_and_capped() {
    let capped = estimate_checkpoint_size_from_history_len(usize::MAX);
    assert_eq!(capped, MAX_CHECKPOINT_ESTIMATE_BYTES);
}

#[test]
fn test_backward_compatibility_with_pretty_printed_checkpoints() {
    // Verify that we can still load pretty-printed JSON from older versions
    // (even though we now save compact JSON)
    let pretty_json = r#"{
  "version": 3,
  "phase": "Development",
  "iteration": 1,
  "total_iterations": 5,
  "reviewer_pass": 0,
  "total_reviewer_passes": 2,
  "timestamp": "2024-01-01 12:00:00",
  "developer_agent": "claude",
  "reviewer_agent": "codex",
  "cli_args": {
    "developer_iters": 5,
    "reviewer_reviews": 2,
    "commit_msg": null,
    "isolation_mode": true,
    "verbosity": 2,
    "show_streaming_metrics": false,
    "review_depth": null,
    "reviewer_json_parser": null
  },
  "developer_agent_config": {
    "name": "claude",
    "cmd": "cmd",
    "output_flag": "-o",
    "yolo_flag": null,
    "can_commit": true
  },
  "reviewer_agent_config": {
    "name": "codex",
    "cmd": "cmd",
    "output_flag": "-o",
    "yolo_flag": null,
    "can_commit": true
  },
  "rebase_state": "NotStarted",
  "config_path": null,
  "config_checksum": null,
  "working_dir": "/test/repo",
  "prompt_md_checksum": null,
  "git_user_name": null,
  "git_user_email": null,
  "run_id": "test-run-id",
  "parent_run_id": null,
  "resume_count": 0,
  "actual_developer_runs": 1,
  "actual_reviewer_runs": 0
}"#;

    let workspace = MemoryWorkspace::new_test().with_file(".agent/checkpoint.json", pretty_json);

    let loaded = load_checkpoint_with_workspace(&workspace)
        .unwrap()
        .expect("pretty-printed checkpoint should load");

    assert_eq!(loaded.version, 3);
    assert_eq!(loaded.phase, PipelinePhase::Development);
    assert_eq!(loaded.iteration, 1);
    assert_eq!(loaded.developer_agent, "claude");
    assert_eq!(loaded.reviewer_agent, "codex");
}
