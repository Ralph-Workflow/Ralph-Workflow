//! System tests for real filesystem PROMPT.md permission toggling and
//! `AgentPhaseGuard` RAII cleanup behaviour.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::executor::process_registry;
use ralph_workflow::git_helpers::GitHelpers;
use ralph_workflow::logger::{Colors, Logger};
use ralph_workflow::pipeline::AgentPhaseGuard;
use ralph_workflow::workspace::{MemoryWorkspace, Workspace, WorkspaceFs, PROMPT_MD};
use serial_test::serial;
use std::path::Path;
use tempfile::TempDir;
use test_helpers::{init_git_repo, with_temp_cwd};

#[test]
#[serial]
fn test_prompt_md_permission_toggle() {
    with_default_timeout(|| {
        let temp_dir = TempDir::new().expect("create temp dir");
        let _repo = init_git_repo(&temp_dir);

        let workspace = WorkspaceFs::new(temp_dir.path().to_path_buf());
        let prompt_path = temp_dir.path().join(PROMPT_MD);

        workspace
            .set_readonly(Path::new(PROMPT_MD))
            .expect("set PROMPT.md read-only");
        assert_prompt_readonly(&prompt_path);

        workspace
            .set_writable(Path::new(PROMPT_MD))
            .expect("set PROMPT.md writable");
        assert_prompt_writable(&prompt_path);
    });
}

fn assert_prompt_readonly(path: &Path) {
    let metadata = std::fs::metadata(path).expect("stat PROMPT.md");

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = metadata.permissions().mode();
        assert_eq!(mode & 0o222, 0, "expected PROMPT.md to be read-only");
    }

    #[cfg(windows)]
    {
        assert!(
            metadata.permissions().readonly(),
            "expected PROMPT.md to be read-only"
        );
    }
}

fn assert_prompt_writable(path: &Path) {
    let metadata = std::fs::metadata(path).expect("stat PROMPT.md");

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = metadata.permissions().mode();
        assert_ne!(mode & 0o200, 0, "expected PROMPT.md to be writable");
    }

    #[cfg(windows)]
    {
        assert!(
            !metadata.permissions().readonly(),
            "expected PROMPT.md to be writable"
        );
    }
}

/// Test that `AgentPhaseGuard::drop()` restores PROMPT.md permissions.
///
/// Verifies that when `AgentPhaseGuard` is dropped without calling `disarm()`,
/// the RAII cleanup executes including PROMPT.md permission restoration.
#[test]
#[serial]
fn test_agent_phase_guard_drop_restores_prompt_md() {
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let workspace = WorkspaceFs::new(dir.path().to_path_buf());
        let prompt_rel = Path::new("PROMPT.md");

        assert!(workspace.exists(prompt_rel), "PROMPT.md should exist");
        workspace
            .set_readonly(prompt_rel)
            .expect("set PROMPT.md read-only");

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(dir.path().join("PROMPT.md"))
                .expect("stat PROMPT.md")
                .permissions()
                .mode();
            assert_eq!(
                mode & 0o200,
                0,
                "PROMPT.md should be non-writable before drop"
            );
        }

        let logger = Logger::new(Colors::new());
        let mut git_helpers = GitHelpers::default();

        {
            let _guard = AgentPhaseGuard::new(&mut git_helpers, &logger, &workspace);
        }

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(dir.path().join("PROMPT.md"))
                .expect("stat PROMPT.md")
                .permissions()
                .mode();
            assert_ne!(mode & 0o200, 0, "PROMPT.md should be writable after drop");
        }
    });
}

/// Test that disarmed guard does NOT run cleanup.
///
/// When `disarm()` is called, the guard should not execute cleanup on drop.
#[test]
#[serial]
fn test_agent_phase_guard_disarm_prevents_cleanup() {
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let workspace =
            MemoryWorkspace::new_test().with_file("PROMPT.md", "# Goal\nTest content\n");
        let logger = Logger::new(Colors::new());
        let mut git_helpers = GitHelpers::default();

        {
            let mut guard = AgentPhaseGuard::new(&mut git_helpers, &logger, &workspace);
            guard.disarm();
        }

        assert!(
            workspace.exists(Path::new("PROMPT.md")),
            "PROMPT.md should exist after disarmed guard drop"
        );
    });
}

#[test]
#[serial]
fn test_agent_phase_guard_disarm_preserves_prompt_md_permissions() {
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let workspace = WorkspaceFs::new(dir.path().to_path_buf());
        let prompt_rel = Path::new("PROMPT.md");
        let prompt_abs = dir.path().join(prompt_rel);

        workspace
            .set_readonly(prompt_rel)
            .expect("set PROMPT.md read-only");
        assert_prompt_readonly(&prompt_abs);

        let logger = Logger::new(Colors::new());
        let mut git_helpers = GitHelpers::default();

        {
            let mut guard = AgentPhaseGuard::new(&mut git_helpers, &logger, &workspace);
            guard.disarm();
        }

        assert_prompt_readonly(&prompt_abs);
    });
}

/// Test that guard cleanup handles missing PROMPT.md gracefully.
///
/// `make_prompt_writable_with_workspace` should not panic if PROMPT.md
/// doesn't exist (edge case during early interrupts).
#[test]
#[serial]
fn test_agent_phase_guard_drop_handles_missing_prompt_md() {
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let workspace = MemoryWorkspace::new_test();
        let logger = Logger::new(Colors::new());
        let mut git_helpers = GitHelpers::default();

        {
            let _guard = AgentPhaseGuard::new(&mut git_helpers, &logger, &workspace);
        }
        // Test passes if no panic occurs
    });
}

/// Test that `AgentPhaseGuard::drop()` calls `kill_all_registered_raw()`
/// to clean up any registered agent processes.
///
/// When the guard is dropped without calling `disarm()`, it should:
/// 1. Call `kill_all_registered_raw()` to kill any registered processes
/// 2. Clear the registry
#[test]
#[serial]
#[cfg(unix)]
fn test_agent_phase_guard_drop_kills_registered_processes() {
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let workspace = MemoryWorkspace::new_test();
        let logger = Logger::new(Colors::new());
        let mut git_helpers = GitHelpers::default();

        // Spawn a real sleep process and register it
        let mut child = std::process::Command::new("sleep")
            .arg("30")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("spawn sleep process");

        let pid = child.id();
        process_registry::register(pid);

        // Verify the process is running
        assert!(
            child.try_wait().expect("try_wait").is_none(),
            "sleep process should be running before guard drop"
        );

        // Drop the guard WITHOUT disarming - this should call kill_all_registered_raw()
        {
            let _guard = AgentPhaseGuard::new(&mut git_helpers, &logger, &workspace);
            // guard is dropped here without calling disarm()
        }

        // Verify the registry was cleared
        assert!(
            process_registry::registered_pids().is_empty(),
            "registry should be empty after guard drop"
        );

        // Verify the process is dead within a bounded time
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut killed = false;
        while std::time::Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(status)) => {
                    use std::os::unix::process::ExitStatusExt;
                    let was_killed = status.signal().is_some();
                    assert!(
                        was_killed,
                        "sleep process should have been killed by signal, got: {status}"
                    );
                    killed = true;
                    break;
                }
                Ok(None) => {
                    std::thread::sleep(std::time::Duration::from_millis(50));
                }
                Err(e) => {
                    panic!("try_wait failed: {e}");
                }
            }
        }

        assert!(
            killed,
            "sleep process should have been killed within 5 seconds"
        );

        // Ensure process is reaped
        let _ = child.wait();

        // Final cleanup - use kill_all_registered_raw to clear any leftover state
        process_registry::kill_all_registered_raw();
    });
}
