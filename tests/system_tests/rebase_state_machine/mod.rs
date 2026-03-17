//! System tests for rebase state machine operations requiring real filesystem.
//!
//! These tests exercise CWD-relative lock and checkpoint file I/O in the rebase
//! state machine. They must be serial because `with_temp_cwd` mutates the
//! process-global CWD.

use ralph_workflow::git_helpers::rebase_checkpoint::{
    save_rebase_checkpoint, RebaseCheckpoint, RebasePhase,
};
use ralph_workflow::git_helpers::{
    acquire_rebase_lock, release_rebase_lock, RebaseLock, RebaseStateMachine,
};
use serial_test::serial;
use std::fs;
use std::path::Path;
use test_helpers::with_temp_cwd;

const REBASE_LOCK_PATH: &str = ".agent/rebase.lock";

#[test]
#[serial]
fn test_state_machine_transition() {
    with_temp_cwd(|_dir| {
        let mut machine = RebaseStateMachine::new("main".to_string());
        machine
            .transition_to(RebasePhase::RebaseInProgress)
            .unwrap();
        assert_eq!(machine.phase(), &RebasePhase::RebaseInProgress);
    });
}

#[test]
#[serial]
fn test_state_machine_save_load() {
    with_temp_cwd(|_dir| {
        let mut machine1 = RebaseStateMachine::new("feature-branch".to_string());
        machine1
            .transition_to(RebasePhase::ConflictDetected)
            .unwrap();

        let checkpoint = RebaseCheckpoint::new("feature-branch".to_string())
            .with_phase(RebasePhase::ConflictDetected)
            .with_conflicted_file("test.rs".to_string());
        save_rebase_checkpoint(&checkpoint).unwrap();

        let machine2 = RebaseStateMachine::load_or_create("main".to_string()).unwrap();
        assert_eq!(machine2.phase(), &RebasePhase::ConflictDetected);
        assert_eq!(machine2.upstream_branch(), "feature-branch");
        assert_eq!(machine2.unresolved_conflict_count(), 1);
    });
}

#[test]
#[serial]
fn test_state_machine_clear_checkpoint() {
    with_temp_cwd(|_dir| {
        let mut machine = RebaseStateMachine::new("main".to_string());
        machine
            .transition_to(RebasePhase::RebaseInProgress)
            .unwrap();

        machine.clear_checkpoint().unwrap();
        assert!(!Path::new(".agent/rebase_checkpoint.json").exists());
    });
}

#[test]
#[serial]
fn test_state_machine_abort() {
    with_temp_cwd(|_dir| {
        let mut machine = RebaseStateMachine::new("main".to_string());
        machine
            .transition_to(RebasePhase::ConflictDetected)
            .unwrap();
        machine.abort().unwrap();

        let loaded = RebaseStateMachine::load_or_create("main".to_string()).unwrap();
        assert_eq!(loaded.phase(), &RebasePhase::RebaseAborted);
    });
}

#[test]
#[serial]
fn test_acquire_and_release_rebase_lock() {
    with_temp_cwd(|_dir| {
        acquire_rebase_lock().unwrap();
        assert!(Path::new(REBASE_LOCK_PATH).exists());
        release_rebase_lock().unwrap();
        assert!(!Path::new(REBASE_LOCK_PATH).exists());
    });
}

#[test]
#[serial]
fn test_rebase_lock_prevents_duplicate() {
    with_temp_cwd(|_dir| {
        acquire_rebase_lock().unwrap();

        let result = acquire_rebase_lock();
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("already in progress"));

        release_rebase_lock().unwrap();
    });
}

#[test]
#[serial]
fn test_rebase_lock_guard_auto_releases() {
    with_temp_cwd(|_dir| {
        {
            let _lock = RebaseLock::new().unwrap();
            assert!(Path::new(REBASE_LOCK_PATH).exists());
        }
        assert!(!Path::new(REBASE_LOCK_PATH).exists());
    });
}

#[test]
#[serial]
fn test_rebase_lock_guard_leak() {
    with_temp_cwd(|_dir| {
        {
            let lock = RebaseLock::new().unwrap();
            assert!(Path::new(REBASE_LOCK_PATH).exists());
            let _ = lock.leak();
        }
        assert!(Path::new(REBASE_LOCK_PATH).exists());
        let _ = release_rebase_lock();
    });
}

#[test]
#[serial]
fn test_stale_lock_is_replaced() {
    with_temp_cwd(|_dir| {
        // Create a lock file with an old timestamp (well past the 1800s timeout)
        let old_timestamp = "2020-01-01T00:00:00+00:00";
        let lock_content = format!("pid=12345\ntimestamp={old_timestamp}\n");
        fs::create_dir_all(".agent").unwrap();
        fs::write(REBASE_LOCK_PATH, lock_content).unwrap();

        acquire_rebase_lock().unwrap();
        assert!(Path::new(REBASE_LOCK_PATH).exists());
        release_rebase_lock().unwrap();
    });
}
