use super::super::file_activity::FileActivityTracker;
use crate::workspace::MemoryWorkspace;
use std::time::{Duration, SystemTime};

#[test]
fn test_detects_recent_plan_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Plan content");

    // Recent file should be detected
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "PLAN.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_issues_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/ISSUES.md", "# Issues");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "ISSUES.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_notes_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/NOTES.md", "# Notes");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "NOTES.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_status_md_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/STATUS.md", "# Status");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "STATUS.md modified recently should be detected"
    );
}

#[test]
fn test_detects_recent_commit_message_modification() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/commit-message.txt", "commit msg");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "commit-message.txt modified recently should be detected"
    );
}

#[test]
fn test_detects_xml_artifacts_in_tmp() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/tmp/output.xml", "<xml/>");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "XML files in .agent/tmp/ should be detected"
    );
}

#[test]
fn test_ignores_log_files() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test()
        .with_file(".agent/pipeline.log", "log content")
        .with_file(".agent/debug.log", "debug logs");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Log files should not count as activity"
    );
}

#[test]
fn test_ignores_checkpoint_json() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/checkpoint.json", "{}");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "checkpoint.json should not count as activity"
    );
}

#[test]
fn test_ignores_start_commit() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/start_commit", "abc123");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "start_commit should not count as activity"
    );
}

#[test]
fn test_ignores_review_baseline_txt() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/review_baseline.txt", "baseline");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "review_baseline.txt should not count as activity"
    );
}

#[test]
fn test_ignores_old_modifications() {
    let tracker = FileActivityTracker::new();
    let old_time = SystemTime::now() - Duration::from_secs(400);
    let ws =
        MemoryWorkspace::new_test().with_file_at_time(".agent/PLAN.md", "old content", old_time);

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Files modified >300s ago should not be detected"
    );
}

#[test]
fn test_ignores_editor_temp_files() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test()
        .with_file(".agent/.PLAN.md.swp", "swap")
        .with_file(".agent/PLAN.md.tmp", "temp")
        .with_file(".agent/PLAN.md~", "backup")
        .with_file(".agent/PLAN.md.bak", "backup");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Editor temporary files should not count as activity"
    );
}

#[test]
fn test_handles_missing_agent_directory_gracefully() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test();

    // Should not panic, should return Ok(false)
    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Missing .agent/ directory should return false, not error"
    );
}

#[test]
fn test_tracks_modification_state_across_calls() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "initial");

    // First check should detect the file
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "First check should detect new file"
    );

    // Second check should still detect activity because the file is still
    // within the recency window, even if it has not changed since last check.
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Second check should still detect recent activity"
    );
}

#[test]
fn test_detects_new_modification_after_initial_check() {
    let tracker = FileActivityTracker::new();

    // Create workspace with old file
    let old_time = SystemTime::now() - Duration::from_secs(100);
    let ws =
        MemoryWorkspace::new_test().with_file_at_time(".agent/PLAN.md", "old content", old_time);

    // First check
    tracker
        .check_for_recent_activity(&ws, Duration::from_secs(300))
        .unwrap();

    // Simulate file update by clearing and recreating with new mtime
    ws.clear();
    let new_time = SystemTime::now() - Duration::from_secs(10);
    let ws = ws.with_file_at_time(".agent/PLAN.md", "new content", new_time);

    // Second check should detect the modification
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Should detect file modification with newer mtime"
    );
}

#[test]
fn test_multiple_ai_files_any_can_trigger() {
    let tracker = FileActivityTracker::new();

    // Create old PLAN.md
    let old_time = SystemTime::now() - Duration::from_secs(400);
    let ws = MemoryWorkspace::new_test()
        .with_file_at_time(".agent/PLAN.md", "old", old_time)
        .with_file(".agent/ISSUES.md", "recent"); // This one is recent

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Should detect activity if ANY AI file is recent"
    );
}

#[test]
fn test_xml_files_only_in_tmp_subdirectory() {
    let tracker = FileActivityTracker::new();

    // XML in .agent/ root should not be detected
    let ws = MemoryWorkspace::new_test().with_file(".agent/output.xml", "<xml/>");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "XML files in .agent/ root should not be tracked"
    );
}

#[test]
fn test_non_xml_files_in_tmp_ignored() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/output.txt", "text")
        .with_file(".agent/tmp/data.json", "{}");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "Non-XML files in .agent/tmp/ should not be tracked"
    );
}

#[test]
fn test_custom_timeout_window() {
    let tracker = FileActivityTracker::new();

    // File modified 150 seconds ago
    let time_150s_ago = SystemTime::now() - Duration::from_secs(150);
    let ws =
        MemoryWorkspace::new_test().with_file_at_time(".agent/PLAN.md", "content", time_150s_ago);

    // With 300s timeout, should be detected
    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "File modified 150s ago should be detected with 300s timeout"
    );

    // With 100s timeout, should not be detected
    let tracker2 = FileActivityTracker::new();
    assert!(
        !tracker2
            .check_for_recent_activity(&ws, Duration::from_secs(100))
            .unwrap(),
        "File modified 150s ago should not be detected with 100s timeout"
    );
}

#[test]
fn test_default_trait_implementation() {
    // Both constructors should work without errors
    let _tracker1 = FileActivityTracker::new();
    let _tracker2 = FileActivityTracker::default();
    // If we reach here, both constructors work correctly
}

#[test]
fn test_workspace_source_file_prevents_timeout() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("src/lib.rs", "fn main() {}");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "recently modified src/lib.rs with no .agent/ dir should be detected as activity"
    );
}

#[test]
fn test_old_workspace_source_file_does_not_prevent_timeout() {
    let tracker = FileActivityTracker::new();
    let old_time = SystemTime::now() - Duration::from_secs(400);
    let ws = MemoryWorkspace::new_test().with_file_at_time("src/lib.rs", "fn main() {}", old_time);

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "src/lib.rs modified 400s ago should not be detected with 300s timeout"
    );
}

#[test]
fn test_git_dir_excluded_from_workspace_scan() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file(".git/COMMIT_EDITMSG", "Initial commit");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        ".git/ files should be excluded from workspace scan"
    );
}

#[test]
fn test_target_dir_excluded_from_workspace_scan() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("target/debug/binary", "ELF binary");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "target/ files should be excluded from workspace scan"
    );
}

#[test]
fn test_workspace_log_file_excluded() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("pipeline.log", "2026-03-09 log output");

    assert!(
        !tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "*.log files at workspace root should be excluded from scan"
    );
}

#[test]
fn test_workspace_file_in_subdir_prevents_timeout() {
    let tracker = FileActivityTracker::new();
    let ws = MemoryWorkspace::new_test().with_file("tests/integration.rs", "#[test] fn foo() {}");

    assert!(
        tracker
            .check_for_recent_activity(&ws, Duration::from_secs(300))
            .unwrap(),
        "recently modified tests/integration.rs should be detected as activity"
    );
}
