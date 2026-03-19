use super::*;

#[test]
fn test_execution_step_new() {
    let outcome = StepOutcome::success(None, vec!["test.txt".to_string()]);
    let step = ExecutionStep::new("Development", 1, "dev_run", outcome);
    assert_eq!(&*step.phase, "Development");
    assert_eq!(step.iteration, 1);
    assert_eq!(&*step.step_type, "dev_run");
    assert!(step.agent.is_none());
    assert!(step.duration_secs.is_none());
    // Verify new fields are None by default
    assert!(step.git_commit_oid.is_none());
    assert!(step.modified_files_detail.is_none());
    assert!(step.prompt_used.is_none());
    assert!(step.issues_summary.is_none());
}

#[test]
fn test_execution_step_with_agent() {
    let outcome = StepOutcome::success(None, vec![]);
    let step = ExecutionStep::new("Development", 1, "dev_run", outcome)
        .with_agent("claude")
        .with_duration(120);
    assert_eq!(step.agent.as_deref(), Some("claude"));
    assert_eq!(step.duration_secs, Some(120));
}

#[test]
fn test_execution_step_new_fields_default() {
    let outcome = StepOutcome::success(None, vec![]);
    let step = ExecutionStep::new("Development", 1, "dev_run", outcome);
    // Verify new fields are None by default
    assert!(step.git_commit_oid.is_none());
    assert!(step.modified_files_detail.is_none());
    assert!(step.prompt_used.is_none());
    assert!(step.issues_summary.is_none());
}

#[test]
fn test_modified_files_detail_default() {
    let detail = ModifiedFilesDetail::default();
    assert!(detail.added.is_none());
    assert!(detail.modified.is_none());
    assert!(detail.deleted.is_none());
}

#[test]
fn test_issues_summary_default() {
    let summary = IssuesSummary::default();
    assert_eq!(summary.found, 0);
    assert_eq!(summary.fixed, 0);
    assert!(summary.description.is_none());
}

#[test]
fn test_file_snapshot() {
    let snapshot = FileSnapshot::new("test.txt", "abc123".to_string(), 100, true);
    assert_eq!(snapshot.path, "test.txt");
    assert_eq!(snapshot.checksum, "abc123");
    assert_eq!(snapshot.size, 100);
    assert!(snapshot.exists);
}

#[test]
fn test_file_snapshot_not_found() {
    let snapshot = FileSnapshot::not_found("missing.txt");
    assert_eq!(snapshot.path, "missing.txt");
    assert!(!snapshot.exists);
    assert_eq!(snapshot.size, 0);
}

#[test]
fn test_decompress_data_rejects_oversized_payload() {
    // Safety invariant: checkpoint resume must not allow decompression bombs.
    // We enforce an upper bound on decompressed payload size.
    let max_bytes = 1024 * 1024;
    let data = "a".repeat(max_bytes + 1);
    let encoded = compression::compress(data.as_bytes()).unwrap();

    let err = compression::decompress(&encoded).expect_err("oversized payload should be rejected");
    assert_eq!(err.kind(), std::io::ErrorKind::InvalidData);
}

#[test]
fn test_execution_history_add_step_bounded() {
    let outcome = StepOutcome::success(None, vec![]);
    let step = ExecutionStep::new("Development", 1, "dev_run", outcome);
    let mut history_base = ExecutionHistory::new();
    let history = history_base.add_step_bounded(step, 1000);
    assert_eq!(history.steps.len(), 1);
    assert_eq!(&*history.steps[0].phase, "Development");
    assert_eq!(history.steps[0].iteration, 1);
}

#[test]
fn test_execution_step_serialization_omits_none_option_fields() {
    let outcome = StepOutcome::success(None, vec![]);
    let step = ExecutionStep::new("Development", 1, "dev_run", outcome);
    let json = serde_json::to_string(&step).unwrap();

    assert!(!json.contains("\"agent\":null"));
    assert!(!json.contains("\"duration_secs\":null"));
    assert!(!json.contains("\"checkpoint_saved_at\":null"));
    assert!(!json.contains("\"git_commit_oid\":null"));
    assert!(!json.contains("\"modified_files_detail\":null"));
    assert!(!json.contains("\"prompt_used\":null"));
    assert!(!json.contains("\"issues_summary\":null"));
}

#[test]
fn test_execution_step_serialization_with_new_fields() {
    // Create a step with new fields via JSON to test backward compatibility
    let json_str = r#"{"phase":"Review","iteration":1,"step_type":"review","timestamp":"2025-01-20 12:00:00","outcome":{"Success":{"output":null,"files_modified":[],"exit_code":0}},"agent":null,"duration_secs":null,"checkpoint_saved_at":null,"git_commit_oid":"abc123","modified_files_detail":{"added":["a.rs"],"modified":[],"deleted":[]},"prompt_used":"Fix issues","issues_summary":{"found":2,"fixed":2,"description":"All fixed"}}"#;
    let deserialized: ExecutionStep = serde_json::from_str(json_str).unwrap();
    assert_eq!(deserialized.git_commit_oid, Some("abc123".to_string()));
    let added = deserialized
        .modified_files_detail
        .as_ref()
        .unwrap()
        .added
        .as_ref()
        .unwrap();
    assert_eq!(added.len(), 1);
    assert_eq!(added[0], "a.rs");

    // Empty arrays in legacy JSON should preserve the None-for-empty canonical form.
    let detail = deserialized.modified_files_detail.as_ref().unwrap();
    assert!(detail.modified.is_none());
    assert!(detail.deleted.is_none());
    assert_eq!(deserialized.prompt_used, Some("Fix issues".to_string()));
    assert_eq!(deserialized.issues_summary.as_ref().unwrap().found, 2);
}

#[test]
fn test_execution_step_with_string_pool() {
    use crate::checkpoint::StringPool;

    let pool = StringPool::new();
    let outcome = StepOutcome::success(None, vec![]);

    let (step1, pool) =
        ExecutionStep::new_with_pool("Development", 1, "dev_run", outcome.clone(), pool);
    let (step1, pool) = step1.with_agent_pooled("claude", pool);
    let (step2, pool) = ExecutionStep::new_with_pool("Development", 2, "dev_run", outcome, pool);
    let (step2, _pool) = step2.with_agent_pooled("claude", pool);

    assert!(Arc::ptr_eq(&step1.phase, &step2.phase));
    assert!(Arc::ptr_eq(
        step1.agent.as_ref().unwrap(),
        step2.agent.as_ref().unwrap()
    ));

    assert_eq!(&*step1.phase, "Development");
    assert_eq!(&*step2.phase, "Development");
    assert_eq!(step1.agent.as_deref(), Some("claude"));
    assert_eq!(step2.agent.as_deref(), Some("claude"));
}

#[test]
fn test_execution_step_memory_optimization() {
    use crate::checkpoint::StringPool;

    let pool = StringPool::new();
    let outcome = StepOutcome::success(None, vec![]);

    let (step, pool) = ExecutionStep::new_with_pool("Development", 1, "dev_run", outcome, pool);
    let (step, _pool) = step.with_agent_pooled("claude", pool);

    let phase_size = step.phase.len();
    let step_type_size = step.step_type.len();
    let agent_size = step.agent.as_ref().map_or(0, |s: &Arc<str>| s.len());

    assert_eq!(phase_size, "Development".len());
    assert_eq!(step_type_size, "dev_run".len());
    assert_eq!(agent_size, "claude".len());

    let optimized_size = phase_size + step_type_size + agent_size;
    assert!(optimized_size < 100);
}

#[test]
fn test_execution_step_serialization_roundtrip() {
    use crate::checkpoint::StringPool;

    let pool = StringPool::new();
    let outcome = StepOutcome::success(Some("output".to_string()), vec!["file.txt".to_string()]);

    let (step, pool) = ExecutionStep::new_with_pool("Development", 1, "dev_run", outcome, pool);
    let (step, pool) = step.with_agent_pooled("claude", pool);
    let (step, _pool) = (step.with_duration(120), pool);

    // Serialize to JSON
    let json = serde_json::to_string(&step).unwrap();

    // Deserialize back
    let deserialized: ExecutionStep = serde_json::from_str(&json).unwrap();

    // Verify all fields match
    assert_eq!(&*step.phase, &*deserialized.phase);
    assert_eq!(step.iteration, deserialized.iteration);
    assert_eq!(&*step.step_type, &*deserialized.step_type);
    assert_eq!(step.agent.as_deref(), deserialized.agent.as_deref());
    assert_eq!(step.duration_secs, deserialized.duration_secs);
    assert_eq!(step.outcome, deserialized.outcome);
}

#[test]
fn test_execution_step_backward_compatible_deserialization() {
    // Old checkpoint format with String fields
    let old_json = r#"{
        "phase": "Development",
        "iteration": 1,
        "step_type": "dev_run",
        "timestamp": "2025-01-20 12:00:00",
        "outcome": {"Success": {"output": null, "files_modified": [], "exit_code": 0}},
        "agent": "claude",
        "duration_secs": 120
    }"#;

    // Should deserialize successfully into new Arc<str> format
    let step: ExecutionStep = serde_json::from_str(old_json).unwrap();

    assert_eq!(&*step.phase, "Development");
    assert_eq!(step.iteration, 1);
    assert_eq!(&*step.step_type, "dev_run");
    assert_eq!(step.agent.as_deref(), Some("claude"));
    assert_eq!(step.duration_secs, Some(120));
}

include!("tests/step_outcome.rs");
