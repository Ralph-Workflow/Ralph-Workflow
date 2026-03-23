//! Tests for `RunLogContext` path resolution and collision handling.

use super::*;
use crate::workspace::WorkspaceFs;

#[test]
fn test_run_log_context_creation() {
    let tempdir = tempfile::tempdir().unwrap();
    let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

    let ctx = RunLogContext::new(&workspace).unwrap();

    // Verify run directory exists
    assert!(workspace.exists(ctx.run_dir()));

    // Verify subdirectories exist
    assert!(workspace.exists(&ctx.run_dir().join("agents")));
    assert!(workspace.exists(&ctx.run_dir().join("provider")));
    assert!(workspace.exists(&ctx.run_dir().join("debug")));
}

#[test]
fn test_run_log_context_path_resolution() {
    let tempdir = tempfile::tempdir().unwrap();
    let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

    let ctx = RunLogContext::new(&workspace).unwrap();

    // Test pipeline log path
    let pipeline_log = ctx.pipeline_log();
    assert!(pipeline_log.ends_with("pipeline.log"));

    // Test event loop log path
    let event_loop_log = ctx.event_loop_log();
    assert!(event_loop_log.ends_with("event_loop.log"));

    // Test agent log path (no attempt)
    let agent_log = ctx.agent_log("planning", 1, None);
    assert!(agent_log.ends_with("agents/planning_1.log"));

    // Test agent log path (with attempt)
    let agent_log_retry = ctx.agent_log("dev", 2, Some(3));
    assert!(agent_log_retry.ends_with("agents/dev_2_a3.log"));

    // Test provider log path
    let provider_log = ctx.provider_log("claude-stream.jsonl");
    assert!(provider_log.ends_with("provider/claude-stream.jsonl"));
}

#[test]
fn test_run_log_context_from_checkpoint() {
    let tempdir = tempfile::tempdir().unwrap();
    let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

    let original_id = "2026-02-06_14-03-27.123Z";
    let ctx = RunLogContext::from_checkpoint(original_id, &workspace).unwrap();

    assert_eq!(ctx.run_id().as_str(), original_id);
    assert!(workspace.exists(ctx.run_dir()));
}

#[test]
fn test_run_metadata_serialization() {
    let metadata = RunMetadata {
        run_id: "2026-02-06_14-03-27.123Z".to_string(),
        started_at_utc: "2026-02-06T14:03:27.123Z".to_string(),
        command: "ralph".to_string(),
        resume: false,
        repo_root: "/tmp/test".to_string(),
        ralph_version: "0.6.3".to_string(),
        pid: Some(12345),
        config_summary: Some(ConfigSummary {
            developer_agent: Some("claude".to_string()),
            reviewer_agent: Some("claude".to_string()),
            total_iterations: Some(3),
            total_reviewer_passes: Some(1),
        }),
    };

    let json = serde_json::to_string_pretty(&metadata).unwrap();
    assert!(json.contains("run_id"));
    assert!(json.contains("2026-02-06_14-03-27.123Z"));
    assert!(json.contains("ralph"));
}

#[test]
fn test_write_run_metadata() {
    let tempdir = tempfile::tempdir().unwrap();
    let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

    let ctx = RunLogContext::new(&workspace).unwrap();

    let metadata = RunMetadata {
        run_id: ctx.run_id().to_string(),
        started_at_utc: "2026-02-06T14:03:27.123Z".to_string(),
        command: "ralph".to_string(),
        resume: false,
        repo_root: tempdir.path().display().to_string(),
        ralph_version: "0.6.3".to_string(),
        pid: Some(12345),
        config_summary: None,
    };

    ctx.write_run_metadata(&workspace, &metadata).unwrap();

    // Verify file was written
    let json_path = ctx.run_metadata();
    assert!(workspace.exists(&json_path));

    // Verify content
    let content = workspace.read(&json_path).unwrap();
    assert!(content.contains(&ctx.run_id().to_string()));
}

#[test]
fn test_collision_handling() {
    let tempdir = tempfile::tempdir().unwrap();
    let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

    // Create a fixed run_id that we can use to simulate collision
    let fixed_id = RunId::for_test("2026-02-06_14-03-27.123Z");

    // Create the base directory with agents subdirectory to simulate a complete collision
    let base_dir = PathBuf::from(format!(".agent/logs-{fixed_id}"));
    workspace
        .create_dir_all(&base_dir.join("agents"))
        .expect("Failed to create base directory for collision test");

    // Also create collision variants 1-5 with agents subdirectory
    (1..=5).for_each(|i| {
        let collision_dir = PathBuf::from(format!(".agent/logs-{fixed_id}-{i:02}"));
        workspace
            .create_dir_all(&collision_dir.join("agents"))
            .expect("Failed to create collision directory");
    });

    // Now create a RunLogContext with the fixed base run_id
    // It should skip base and collisions 1-5 and create collision variant 06
    let ctx = RunLogContext::for_testing(&fixed_id, &workspace).unwrap();

    // Verify the run_id has a collision suffix -06
    let run_id_str = ctx.run_id().as_str();
    assert!(
        run_id_str.ends_with("-06"),
        "Run ID should have collision suffix -06, got: {run_id_str}"
    );

    // Verify the directory exists
    assert!(workspace.exists(ctx.run_dir()));

    // Verify the directory name matches
    let expected_dir = PathBuf::from(format!(".agent/logs-{run_id_str}"));
    assert_eq!(
        ctx.run_dir(),
        &expected_dir,
        "Run directory should match the collision suffix path"
    );
}

#[test]
fn test_collision_exhaustion() {
    let tempdir = tempfile::tempdir().unwrap();
    let workspace = WorkspaceFs::new(tempdir.path().to_path_buf());

    // Create a fixed run_id
    let fixed_id = RunId::for_test("2026-02-06_14-03-27.123Z");

    // Create the base directory and all 99 collision variants with agents subdirectory
    workspace
        .create_dir_all(&PathBuf::from(format!(".agent/logs-{fixed_id}")).join("agents"))
        .unwrap();
    (1..=99).for_each(|i| {
        workspace
            .create_dir_all(&PathBuf::from(format!(".agent/logs-{fixed_id}-{i:02}")).join("agents"))
            .unwrap();
    });

    // Now try to create a RunLogContext with the fixed base run_id - it should fail
    let result = RunLogContext::for_testing(&fixed_id, &workspace);
    assert!(
        result.is_err(),
        "Should fail when all collision variants are exhausted"
    );

    let err_msg = match result {
        Err(e) => e.to_string(),
        Ok(_) => panic!("Expected error but got success"),
    };
    assert!(
        err_msg.contains("Too many collisions") || err_msg.contains("collisions"),
        "Error message should mention collisions: {err_msg}"
    );
}
