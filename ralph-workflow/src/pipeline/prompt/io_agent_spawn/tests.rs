use super::{make_completion_check, spawn_stdout_cancel_watcher};
use crate::workspace::{ArtifactEnvelope, MemoryWorkspace, Workspace};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

#[test]
fn completion_check_treats_commit_json_artifact_as_complete() {
    let workspace = Arc::new(MemoryWorkspace::new_test());
    workspace
        .write_artifact_json(&ArtifactEnvelope::new(
            "commit_message",
            serde_json::json!({
                "type": "commit",
                "subject": "fix: treat artifact as completion"
            }),
            "2026-04-10T00:00:00Z",
        ))
        .expect("commit JSON artifact should be written");

    let workspace_trait: Arc<dyn Workspace> = workspace;
    let completion_check = make_completion_check(
        Some(Path::new(".agent/tmp/commit_message.xml")),
        &workspace_trait,
    )
    .expect("completion check should be created");

    assert!(
        completion_check(),
        "valid commit_message JSON artifact should count as completed output even when XML is absent"
    );
}

#[test]
fn completion_check_treats_plan_json_artifact_as_complete() {
    let workspace = Arc::new(MemoryWorkspace::new_test());
    workspace
        .write_artifact_json(&ArtifactEnvelope::new(
            "plan",
            serde_json::json!({
                "summary": {
                    "context": "Investigate completion detection",
                    "scope_items": [{"text": "Update idle timeout", "count": "1", "category": "bugfix"}]
                },
                "steps": [],
                "critical_files": {"primary_files": []},
                "risks_mitigations": [],
                "verification_strategy": []
            }),
            "2026-04-10T00:00:00Z",
        ))
        .expect("plan JSON artifact should be written");

    let workspace_trait: Arc<dyn Workspace> = workspace;
    let completion_check =
        make_completion_check(Some(Path::new(".agent/tmp/plan.xml")), &workspace_trait)
            .expect("completion check should be created");

    assert!(
        completion_check(),
        "valid plan JSON artifact should count as completed output even when XML is absent"
    );
}

#[test]
fn stdout_cancel_watcher_sets_cancel_flag_promptly_on_user_interrupt() {
    use crate::executor::MockAgentChild;

    let _lock = crate::interrupt::interrupt_test_lock();

    let _ = crate::interrupt::take_user_interrupt_request();
    crate::interrupt::reset_user_interrupted_occurred();

    let interrupt_flag = Arc::new(AtomicBool::new(false));
    let interrupt_flag_for_watcher = Arc::clone(&interrupt_flag);
    let stdout_cancel = Arc::new(AtomicBool::new(false));
    let monitor_should_stop = Arc::new(AtomicBool::new(false));
    let (child, _controller) = MockAgentChild::new_running(0);
    let child_shared: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
        Arc::new(std::sync::Mutex::new(Box::new(child)));

    spawn_stdout_cancel_watcher(
        Arc::clone(&stdout_cancel),
        Arc::clone(&monitor_should_stop),
        Arc::clone(&child_shared),
        crate::pipeline::idle_timeout::new_activity_timestamp(),
        move || interrupt_flag_for_watcher.load(Ordering::Acquire),
    );

    std::thread::sleep(Duration::from_millis(20));
    assert!(
        !stdout_cancel.load(Ordering::Acquire),
        "cancel flag should not be set before interrupt"
    );

    interrupt_flag.store(true, Ordering::Release);

    let deadline = Instant::now() + Duration::from_millis(300);
    while Instant::now() < deadline {
        if stdout_cancel.load(Ordering::Acquire) {
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }

    monitor_should_stop.store(true, Ordering::Release);

    assert!(
        stdout_cancel.load(Ordering::Acquire),
        "stdout_cancel_watcher did not set cancel flag within 300ms of user interrupt"
    );
}

#[test]
fn stdout_cancel_watcher_sets_cancel_flag_when_child_process_exits() {
    use crate::executor::MockAgentChild;

    let (child, controller) = MockAgentChild::new_running(0);
    let child_shared: Arc<std::sync::Mutex<Box<dyn crate::executor::AgentChild>>> =
        Arc::new(std::sync::Mutex::new(Box::new(child)));

    let stdout_cancel = Arc::new(AtomicBool::new(false));
    let monitor_should_stop = Arc::new(AtomicBool::new(false));

    spawn_stdout_cancel_watcher(
        Arc::clone(&stdout_cancel),
        Arc::clone(&monitor_should_stop),
        Arc::clone(&child_shared),
        crate::pipeline::idle_timeout::new_activity_timestamp(),
        || false,
    );

    std::thread::sleep(Duration::from_millis(20));
    controller.store(false, Ordering::Release);

    let deadline = Instant::now() + Duration::from_millis(600);
    while Instant::now() < deadline {
        if stdout_cancel.load(Ordering::Acquire) {
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }

    monitor_should_stop.store(true, Ordering::Release);

    assert!(
        stdout_cancel.load(Ordering::Acquire),
        "stdout_cancel_watcher did not set cancel flag within 300ms of child exit"
    );
}
