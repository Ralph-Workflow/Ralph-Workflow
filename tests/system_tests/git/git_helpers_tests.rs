//! System tests for git helper functions requiring real git repositories.
//!
//! These tests use real `git2::Repository` and filesystem operations.
//! Tests that can use `MemoryWorkspace` should remain in the unit tests.

use ralph_workflow::git_helpers::get_hooks_dir;
use ralph_workflow::git_helpers::hooks::HOOK_MARKER;
use ralph_workflow::git_helpers::{
    self, cleanup_orphaned_marker, disable_git_wrapper, end_agent_phase,
    ensure_agent_phase_protections, git_snapshot, git_snapshot_in_repo, hooks,
    hooks::RALPH_HOOK_NAMES, reinstall_hooks_if_tampered, start_agent_phase, uninstall_hooks,
    GitHelpers,
};
use ralph_workflow::logger::Logger;
use ralph_workflow::pipeline::AgentPhaseGuard;
use ralph_workflow::workspace::WorkspaceFs;
use serial_test::serial;
use std::fs::{self, File};
use std::process::Command;

#[test]
#[serial]
fn test_agent_phase_cleanup_removes_git_wrapper_track_file() {
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Precondition: wrapper track file exists.
        assert!(
            dir.path().join(".agent/git-wrapper-dir.txt").exists(),
            "expected wrapper track file to exist after start_agent_phase"
        );

        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();

        assert!(
            !dir.path().join(".agent/git-wrapper-dir.txt").exists(),
            "expected wrapper track file to be removed by disable_git_wrapper"
        );
    });
}

#[test]
#[serial]
fn test_disable_git_wrapper_removes_track_file_even_when_cwd_changes() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_file = dir.path().join(".agent/git-wrapper-dir.txt");
        assert!(track_file.exists(), "precondition: track file must exist");

        let other_dir = tempfile::tempdir().expect("create other tempdir");
        std::env::set_current_dir(other_dir.path()).expect("set cwd away from repo");

        // Regression: disable_git_wrapper should remove the track file in the repo root,
        // not relative to the current working directory.
        disable_git_wrapper(&mut helpers);

        assert!(
            !track_file.exists(),
            "expected wrapper track file to be removed even when cwd is not repo root"
        );
    });
}

#[test]
#[serial]
fn test_git_snapshot() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        // Create an untracked file.
        fs::write("testfile.txt", "test").unwrap();

        let snapshot = git_snapshot().unwrap();
        assert!(snapshot.contains("?? testfile.txt"));
    });
}

#[test]
#[serial]
fn test_install_hook() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        fs::create_dir_all(&hooks_dir).unwrap();

        let hook_path = hooks_dir.join("pre-commit");
        hooks::install_hook("Commit", &hook_path).unwrap();

        assert!(hook_path.exists());
        let content = fs::read_to_string(&hook_path).unwrap();
        assert!(content.contains(HOOK_MARKER));
    });
}

#[test]
#[serial]
fn test_install_hook_creates_missing_hooks_dir() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        // Simulate a broken repo state where hooks dir is missing.
        // Regression: install_hook must create the directory before canonicalize().
        let _ = fs::remove_dir_all(&hooks_dir);

        let hook_path = hooks_dir.join("pre-commit");
        hooks::install_hook("Commit", &hook_path).expect("install hook should create hooks dir");

        assert!(hook_path.exists());
        let content = fs::read_to_string(&hook_path).unwrap();
        assert!(content.contains(HOOK_MARKER));
    });
}

#[test]
#[serial]
fn test_uninstall_hooks_in_repo_does_not_depend_on_cwd() {
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        fs::create_dir_all(&hooks_dir).unwrap();

        let precommit_path = hooks_dir.join("pre-commit");
        let original_hook = "#!/bin/bash\necho 'Original hook'\n";
        fs::write(&precommit_path, original_hook).unwrap();

        // Install Ralph hook (backs up original).
        hooks::install_hook("Commit", &precommit_path).unwrap();
        let content = fs::read_to_string(&precommit_path).unwrap();
        assert!(content.contains(HOOK_MARKER));

        // Change CWD away from the repo root.
        let other_dir = tempfile::tempdir().expect("create other tempdir");
        std::env::set_current_dir(other_dir.path()).expect("set cwd to other tempdir");

        // Regression: uninstalling startup hooks must target the explicit repo root,
        // not the process CWD.
        hooks::uninstall_hooks_in_repo(dir.path(), &logger).unwrap();

        let restored = fs::read_to_string(&precommit_path).unwrap();
        assert_eq!(restored, original_hook);
        assert!(!restored.contains(HOOK_MARKER));
    });
}

#[test]
#[serial]
fn test_uninstall_hook_restores_original() {
    use test_helpers::with_temp_cwd;
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        fs::create_dir_all(&hooks_dir).unwrap();

        // Create an original hook.
        let hook_path = hooks_dir.join("pre-commit");
        fs::write(&hook_path, "#!/bin/bash\necho 'Original hook'").unwrap();

        // Install Ralph hook (backs up original).
        hooks::install_hook("Commit", &hook_path).unwrap();

        // Verify Ralph hook is installed.
        let content = fs::read_to_string(&hook_path).unwrap();
        assert!(content.contains(HOOK_MARKER));

        // Uninstall hook restores original.
        let restored = hooks::uninstall_hook(&hook_path, &logger).unwrap();
        assert!(restored);

        let content = fs::read_to_string(&hook_path).unwrap();
        assert!(content.contains("Original hook"));
        assert!(!content.contains(HOOK_MARKER));
    });
}

#[test]
#[serial]
fn test_install_hook_uses_absolute_path() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        fs::create_dir_all(&hooks_dir).unwrap();

        // Create an existing hook.
        let hook_path = hooks_dir.join("pre-commit");
        fs::write(&hook_path, "#!/bin/bash\nexit 0").unwrap();

        // Install Ralph hook.
        hooks::install_hook("TestHook", &hook_path).unwrap();

        // Read the installed hook content.
        let content = fs::read_to_string(&hook_path).unwrap();

        // The orig= line should contain an absolute path (starts with /).
        // The hook script now uses bash-safe single-quoted literals.
        assert!(content.contains("orig='/"));
    });
}

#[test]
#[serial]
fn test_cleanup_orphaned_marker() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        let dir_path = dir.path();

        git2::Repository::init(dir_path).unwrap();

        // Create marker.
        let marker_path = dir_path.join(".no_agent_commit");
        File::create(&marker_path).unwrap();
        assert!(marker_path.exists());

        cleanup_orphaned_marker(&logger).unwrap();
        assert!(!marker_path.exists());
    });
}

#[test]
#[serial]
fn test_git2_to_io_error_preserves_not_found_kind_for_missing_repo() {
    let missing =
        std::env::temp_dir().join(format!("ralph-nonexistent-repo-{}", std::process::id()));
    let Err(err) = git2::Repository::discover(&missing) else {
        panic!("expected repo discovery to fail for missing path")
    };

    let io_err = git_helpers::git2_to_io_error(&err);
    assert_eq!(
        io_err.kind(),
        std::io::ErrorKind::NotFound,
        "expected NotFound kind for missing repo discovery error"
    );
}

#[test]
#[serial]
fn test_get_git_diff_from_start_with_workspace_returns_diff_from_start_commit() {
    // TDD regression for get_git_diff_from_start_with_workspace:
    // when the workspace has a real .git on disk, the function must generate a diff
    // from the start_commit baseline (not HEAD-based), and include working-tree changes.
    use ralph_workflow::git_helpers::get_git_diff_from_start_with_workspace;
    use ralph_workflow::workspace::WorkspaceFs;
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        // Arrange: real git repo with an initial commit.
        let repo = git2::Repository::init(".").expect("init git repo");

        let tracked_file = "ralph_test_workspace_diff_marker.txt";
        std::fs::write(tracked_file, "initial\n").expect("write initial file");

        let mut index = repo.index().expect("open index");
        index
            .add_path(std::path::Path::new(tracked_file))
            .expect("add file to index");
        index.write().expect("write index");
        let tree_oid = index.write_tree().expect("write tree");
        let tree = repo.find_tree(tree_oid).expect("find tree");
        let sig = git2::Signature::now("test", "test@test.com").expect("signature");
        repo.commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
            .expect("create initial commit");

        // The workspace points to the same directory as the real repo.
        let workspace = WorkspaceFs::new(dir.path().to_path_buf());

        // Modify the tracked file to produce a deterministic diff.
        let unique_marker = "UNIQUE_WORKSPACE_DIFF_MARKER";
        std::fs::write(
            tracked_file,
            format!("initial\nmodified\n{unique_marker}\n"),
        )
        .expect("modify tracked file");

        // Act: get diff from start_commit baseline (start_commit is auto-saved on first call).
        let result = get_git_diff_from_start_with_workspace(&workspace);

        // Assert: diff is returned and contains the unique modification.
        assert!(
            result.is_ok(),
            "expected Ok diff from workspace with real git repo: {result:?}"
        );
        let diff = result.unwrap();
        assert!(
            diff.contains("diff --git"),
            "expected standard git diff format; got: {diff}"
        );
        assert!(
            diff.contains(unique_marker),
            "expected diff to include unique marker from working-tree change; got: {diff}"
        );
    });
}

#[test]
#[serial]
fn test_git_snapshot_excludes_gitignored_files() {
    let dir = tempfile::tempdir().unwrap();
    let repo = git2::Repository::init(dir.path()).unwrap();

    // Configure git user for commits.
    let mut cfg = repo.config().unwrap();
    cfg.set_str("user.name", "test").unwrap();
    cfg.set_str("user.email", "test@test.com").unwrap();

    // Create .gitignore excluding .agent/ directory.
    fs::write(dir.path().join(".gitignore"), ".agent/\n").unwrap();

    // Stage and commit .gitignore so it takes effect.
    let mut index = repo.index().unwrap();
    index.add_path(std::path::Path::new(".gitignore")).unwrap();
    index.write().unwrap();
    let tree_oid = index.write_tree().unwrap();
    let tree = repo.find_tree(tree_oid).unwrap();
    let sig = git2::Signature::now("test", "test@test.com").unwrap();
    repo.commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
        .unwrap();

    // Create an ignored file (simulates .agent/tmp/plan.xml from pipeline).
    fs::create_dir_all(dir.path().join(".agent/tmp")).unwrap();
    fs::write(dir.path().join(".agent/tmp/plan.xml"), "content").unwrap();

    // git_snapshot_in_repo should NOT report gitignored files.
    let snapshot = git_snapshot_in_repo(dir.path()).unwrap();
    assert!(
        snapshot.trim().is_empty(),
        "git_snapshot should not include gitignored files, got: {snapshot}"
    );
}

#[test]
#[serial]
fn test_pre_commit_hook_blocks_when_marker_exists() {
    // Verify the installed pre-commit hook exits non-zero and prints a blocking
    // message when .no_agent_commit is present.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        // Install Ralph-managed hooks.
        hooks::install_hooks().unwrap();

        // Create the marker file that should trigger blocking.
        let marker = dir.path().join(".no_agent_commit");
        File::create(&marker).unwrap();
        assert!(marker.exists(), "precondition: marker must exist");

        // Locate and verify the hook.
        let hooks_dir = get_hooks_dir().unwrap();
        let hook_path = hooks_dir.join("pre-commit");
        assert!(hook_path.exists(), "pre-commit hook must be installed");
        let content = fs::read_to_string(&hook_path).unwrap();
        assert!(
            content.contains(HOOK_MARKER),
            "hook must contain HOOK_MARKER"
        );

        // Run the hook script directly via bash.
        let output = Command::new("bash")
            .arg(&hook_path)
            .output()
            .expect("bash must be available to run hook script");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        let combined = format!("{stdout}{stderr}");

        assert_ne!(
            output.status.code(),
            Some(0),
            "hook should exit non-zero when .no_agent_commit is present; output: {combined}"
        );
        assert!(
            combined.to_lowercase().contains("blocked"),
            "hook output should mention 'blocked'; got: {combined}"
        );
    });
}

#[test]
#[serial]
fn test_pre_commit_hook_passes_when_no_marker() {
    // Verify the installed pre-commit hook exits 0 when .no_agent_commit is absent.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        // Install Ralph-managed hooks.
        hooks::install_hooks().unwrap();

        // Confirm marker is absent.
        let marker = dir.path().join(".no_agent_commit");
        assert!(!marker.exists(), "precondition: marker must be absent");

        let hooks_dir = get_hooks_dir().unwrap();
        let hook_path = hooks_dir.join("pre-commit");
        assert!(hook_path.exists(), "pre-commit hook must be installed");

        // Run the hook script directly via bash.
        let output = Command::new("bash")
            .arg(&hook_path)
            .output()
            .expect("bash must be available to run hook script");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert_eq!(
            output.status.code(),
            Some(0),
            "hook should exit 0 when .no_agent_commit is absent; stdout={stdout} stderr={stderr}"
        );
    });
}

#[test]
#[serial]
fn test_agent_phase_guard_drop_cleans_up_hooks() {
    // Verify AgentPhaseGuard::drop (without disarm) removes the marker file,
    // the git-wrapper track file, and Ralph-managed hooks.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let workspace = WorkspaceFs::new(dir.path().to_path_buf());
        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Preconditions: marker and wrapper track file must exist.
        assert!(
            dir.path().join(".no_agent_commit").exists(),
            "expected .no_agent_commit after start_agent_phase"
        );
        assert!(
            dir.path().join(".agent/git-wrapper-dir.txt").exists(),
            "expected wrapper track file after start_agent_phase"
        );

        // Create guard without calling disarm() — drop must perform cleanup.
        {
            let _guard = AgentPhaseGuard::new(&mut helpers, &logger, &workspace);
            // Drop here without disarm.
        }

        // Marker must be gone.
        assert!(
            !dir.path().join(".no_agent_commit").exists(),
            "expected .no_agent_commit to be removed by AgentPhaseGuard::drop"
        );

        // Wrapper track file must be gone.
        assert!(
            !dir.path().join(".agent/git-wrapper-dir.txt").exists(),
            "expected wrapper track file to be removed by AgentPhaseGuard::drop"
        );

        // Hooks must be removed or not contain HOOK_MARKER.
        let hooks_dir = get_hooks_dir().unwrap();
        for &hook_name in RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            if hook_path.exists() {
                let content = fs::read_to_string(&hook_path).unwrap();
                assert!(
                    !content.contains(HOOK_MARKER),
                    "hook {hook_name} must not contain HOOK_MARKER after AgentPhaseGuard::drop"
                );
            }
        }
    });
}

#[test]
#[serial]
fn test_git_diff_in_repo_is_head_based_after_multiple_commits() {
    // Regression: git_diff_in_repo must always diff against the current HEAD, not against a
    // stale baseline from an earlier commit. This mirrors the production bug where the commit
    // phase reused a stale diff because commit_diff_prepared was not reset after each commit.
    use ralph_workflow::git_helpers::git_diff_in_repo;
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo = git2::Repository::init(".").unwrap();

        // Configure git identity required for commits.
        {
            let mut config = repo.config().unwrap();
            config.set_str("user.name", "Test User").unwrap();
            config.set_str("user.email", "test@example.com").unwrap();
        }

        let sig = git2::Signature::now("Test User", "test@example.com").unwrap();

        // Commit A: add and commit file1.txt.
        fs::write("file1.txt", "content1\n").unwrap();
        let mut index = repo.index().unwrap();
        index
            .add_all(std::iter::once(&"*"), git2::IndexAddOption::DEFAULT, None)
            .unwrap();
        index.write().unwrap();
        let tree_id = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_id).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "commit A", &tree, &[])
            .unwrap();

        // Arrange: stage file2.txt after commit A (not yet committed).
        // Use staged changes because diff_tree_to_workdir_with_index captures index vs HEAD.
        fs::write("file2.txt", "content2\n").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("file2.txt")).unwrap();
        index.write().unwrap();

        // First diff: should show staged file2.txt, not committed file1.txt.
        let diff1 = git_diff_in_repo(dir.path()).expect("git_diff_in_repo must succeed");

        assert!(
            diff1.contains("file2.txt"),
            "diff after commit A must show staged file2.txt; got: {diff1}"
        );
        assert!(
            !diff1.contains("file1.txt"),
            "diff after commit A must NOT show committed file1.txt (HEAD-based); got: {diff1}"
        );

        // Commit B: commit the staged file2.txt.
        let mut index = repo.index().unwrap();
        index
            .add_all(std::iter::once(&"*"), git2::IndexAddOption::DEFAULT, None)
            .unwrap();
        index.write().unwrap();
        let tree_id = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_id).unwrap();
        let parent = repo.head().unwrap().peel_to_commit().unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "commit B", &tree, &[&parent])
            .unwrap();

        // Arrange: stage file3.txt after commit B.
        fs::write("file3.txt", "content3\n").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("file3.txt")).unwrap();
        index.write().unwrap();

        // Second diff: should show staged file3.txt only, not file1.txt or file2.txt.
        // Regression: a stale diff (start-commit-based) would reinclude file2.txt here.
        let diff2 = git_diff_in_repo(dir.path()).expect("git_diff_in_repo must succeed");

        assert!(
            diff2.contains("file3.txt"),
            "diff after commit B must show staged file3.txt; got: {diff2}"
        );
        assert!(
            !diff2.contains("file1.txt"),
            "diff after commit B must NOT show file1.txt (committed in A); got: {diff2}"
        );
        assert!(
            !diff2.contains("file2.txt"),
            "diff after commit B must NOT show file2.txt (committed in B) — \
             regression: stale diff would reinclude file2.txt; got: {diff2}"
        );
    });
}

// =========================================================================
// File permission tests (hooks and marker)
// =========================================================================

#[cfg(unix)]
#[test]
#[serial]
fn test_installed_hooks_are_read_only_executable() {
    // After install_hooks(), hooks should have mode 0o555 (r-xr-xr-x)
    // to deter agent overwriting.
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        hooks::install_hooks().unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        for &hook_name in RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            assert!(hook_path.exists(), "{hook_name} must exist");
            let mode = fs::metadata(&hook_path).unwrap().permissions().mode() & 0o777;
            assert_eq!(
                mode, 0o555,
                "{hook_name} should have mode 0o555 (read-only executable), got {mode:#o}"
            );
        }
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_marker_file_is_read_only() {
    // After start_agent_phase(), .no_agent_commit should have mode 0o444 (r--r--r--)
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".no_agent_commit");
        assert!(marker.exists(), "marker must exist");
        let mode = fs::metadata(&marker).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o444,
            "marker should have mode 0o444 (read-only), got {mode:#o}"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        uninstall_hooks(&logger).unwrap();
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_uninstall_hooks_handles_read_only() {
    // Hooks set to 0o555 must still be removable by uninstall_hooks().
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        hooks::install_hooks().unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        let pre_commit = hooks_dir.join("pre-commit");
        assert!(pre_commit.exists(), "precondition: hook must exist");

        uninstall_hooks(&logger).unwrap();

        assert!(
            !pre_commit.exists(),
            "hook must be removed even when read-only"
        );
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_end_agent_phase_handles_read_only_marker() {
    // Marker set to 0o444 must still be removable by end_agent_phase().
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".no_agent_commit");
        assert!(marker.exists(), "precondition: marker must exist");

        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        uninstall_hooks(&logger).unwrap();

        assert!(
            !marker.exists(),
            "marker must be removed even when read-only"
        );
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_hook_permissions() {
    // If an agent loosens hook permissions from 0o555 to 0o755,
    // ensure_agent_phase_protections must restore them to 0o555.
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        let pre_commit = hooks_dir.join("pre-commit");

        // Simulate agent loosening permissions.
        let mut perms = fs::metadata(&pre_commit).unwrap().permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&pre_commit, perms).unwrap();

        let mode = fs::metadata(&pre_commit).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o755, "precondition: hook must have loosened perms");

        let _ = ensure_agent_phase_protections(&logger);

        let mode = fs::metadata(&pre_commit).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o555,
            "hook permissions must be restored to 0o555 by ensure_agent_phase_protections"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_marker_permissions() {
    // If an agent loosens marker permissions from 0o444 to 0o644,
    // ensure_agent_phase_protections must restore them to 0o444.
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".no_agent_commit");

        // Simulate agent loosening permissions.
        let mut perms = fs::metadata(&marker).unwrap().permissions();
        perms.set_mode(0o644);
        fs::set_permissions(&marker, perms).unwrap();

        let mode = fs::metadata(&marker).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o644, "precondition: marker must have loosened perms");

        let _ = ensure_agent_phase_protections(&logger);

        let mode = fs::metadata(&marker).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o444,
            "marker permissions must be restored to 0o444 by ensure_agent_phase_protections"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

// =========================================================================
// Wrapper script end-to-end tests
// =========================================================================

/// Helper: create a real git repo with a wrapper installed.
///
/// Returns a context that keeps `GitHelpers` alive for the duration of the test and
/// cleans up on Drop, avoiding tempdir leaks.
fn setup_wrapper_test(dir: &std::path::Path) -> WrapperTestContext {
    git2::Repository::init(dir).unwrap();

    let mut helpers = GitHelpers::default();
    start_agent_phase(&mut helpers).unwrap();

    // Read the wrapper script path from the track file.
    let track_content = fs::read_to_string(dir.join(".agent/git-wrapper-dir.txt")).unwrap();
    let wrapper_dir = std::path::PathBuf::from(track_content.trim());
    let wrapper_path = wrapper_dir.join("git");
    assert!(wrapper_path.exists(), "wrapper script must exist");

    // Keep helpers alive for the duration of the test by returning a guard.
    // The guard performs cleanup on Drop, avoiding tempdir leaks across runs.
    WrapperTestContext {
        wrapper_path,
        helpers,
        logger: Logger::new(ralph_workflow::logger::Colors::with_enabled(false)),
    }
}

struct WrapperTestContext {
    wrapper_path: std::path::PathBuf,
    helpers: GitHelpers,
    logger: Logger,
}

impl Drop for WrapperTestContext {
    fn drop(&mut self) {
        // Best-effort cleanup; avoid panics in Drop.
        end_agent_phase();
        disable_git_wrapper(&mut self.helpers);
        let _ = uninstall_hooks(&self.logger);
    }
}

/// Run the wrapper with given args, returning exit code and combined output.
fn run_wrapper(wrapper_path: &std::path::Path, args: &[&str]) -> (i32, String) {
    let output = Command::new("sh")
        .arg(wrapper_path)
        .args(args)
        .output()
        .expect("sh must be available to run wrapper script");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{stdout}{stderr}");
    let code = output.status.code().unwrap_or(-1);
    (code, combined)
}

#[test]
#[serial]
fn test_git_wrapper_blocks_merge_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["merge", "main"]);
        assert_eq!(code, 1, "merge should be blocked; output: {output}");
        assert!(
            output.to_lowercase().contains("blocked"),
            "output should mention 'blocked'; got: {output}"
        );
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_merge_when_marker_missing_but_wrapper_active() {
    // If an agent deletes .no_agent_commit mid-run, the wrapper should still block
    // destructive subcommands as long as the wrapper is installed (track file present).
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());

        // Simulate agent deleting the marker.
        let marker = dir.path().join(".no_agent_commit");
        fs::remove_file(&marker).unwrap();
        assert!(!marker.exists(), "precondition: marker must be deleted");

        let (_code, output) = run_wrapper(&ctx.wrapper_path, &["merge", "main"]);
        assert!(
            output.contains("Blocked:"),
            "wrapper should block even when marker is missing; got: {output}"
        );
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_rebase_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["rebase", "main"]);
        assert_eq!(code, 1, "rebase should be blocked; output: {output}");
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_reset_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["reset", "--hard"]);
        assert_eq!(code, 1, "reset should be blocked; output: {output}");
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_allows_status_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());
        let (code, _output) = run_wrapper(&ctx.wrapper_path, &["status"]);
        assert_eq!(code, 0, "status should be allowed");
    });
}

#[test]
#[serial]
fn test_git_wrapper_allows_stash_list_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());
        let (code, _output) = run_wrapper(&ctx.wrapper_path, &["stash", "list"]);
        assert_eq!(code, 0, "stash list should be allowed");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_stash_pop_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["stash", "pop"]);
        assert_eq!(code, 1, "stash pop should be blocked; output: {output}");
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_add_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());

        // Create a test file to add.
        fs::write(dir.path().join("testfile.txt"), "content").unwrap();

        let (code, output) = run_wrapper(&ctx.wrapper_path, &["add", "testfile.txt"]);
        assert_eq!(code, 1, "git add should be blocked; output: {output}");
        assert!(
            output.to_lowercase().contains("blocked"),
            "output should mention 'blocked'; got: {output}"
        );
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_init_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["init"]);
        assert_eq!(code, 1, "git init should be blocked; output: {output}");
        assert!(
            output.to_lowercase().contains("blocked"),
            "output should mention 'blocked'; got: {output}"
        );
    });
}

// =========================================================================
// Hook integrity enforcement tests
// =========================================================================

#[test]
#[serial]
fn test_reinstall_hooks_if_tampered_when_missing() {
    // When hooks are completely missing, reinstall_hooks_if_tampered must reinstall them.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let hooks_dir = get_hooks_dir().unwrap();

        // Precondition: no hooks exist.
        assert!(
            !hooks_dir.join("pre-commit").exists(),
            "precondition: pre-commit must not exist"
        );

        reinstall_hooks_if_tampered(&logger).unwrap();

        // All hooks must now exist with the marker.
        for &hook_name in RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            assert!(hook_path.exists(), "{hook_name} must be reinstalled");
            let content = fs::read_to_string(&hook_path).unwrap();
            assert!(
                content.contains(HOOK_MARKER),
                "reinstalled {hook_name} must contain HOOK_MARKER"
            );
        }
    });
}

#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_when_marker_and_hooks_deleted() {
    // If an agent deletes BOTH the marker and all Ralph hooks, ensure_agent_phase_protections
    // must self-heal (run_with_prompt calls this before every agent spawn).
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Simulate tampering: delete marker and all hooks.
        let marker = dir.path().join(".no_agent_commit");
        fs::remove_file(&marker).unwrap();
        for &hook_name in RALPH_HOOK_NAMES {
            let hook_path = get_hooks_dir().unwrap().join(hook_name);
            if hook_path.exists() {
                let _ = fs::remove_file(&hook_path);
            }
        }

        assert!(!marker.exists(), "precondition: marker must be deleted");
        for &hook_name in RALPH_HOOK_NAMES {
            assert!(
                !get_hooks_dir().unwrap().join(hook_name).exists(),
                "precondition: {hook_name} must be deleted"
            );
        }

        let result = ensure_agent_phase_protections(&logger);
        assert!(result.tampering_detected, "must report tampering");

        assert!(marker.exists(), "marker must be recreated");
        for &hook_name in RALPH_HOOK_NAMES {
            let hook_path = get_hooks_dir().unwrap().join(hook_name);
            assert!(hook_path.exists(), "{hook_name} must be reinstalled");
            let content = fs::read_to_string(&hook_path).unwrap();
            assert!(
                content.contains(HOOK_MARKER),
                "reinstalled {hook_name} must contain HOOK_MARKER"
            );
        }

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[test]
#[serial]
fn test_reinstall_hooks_if_tampered_when_marker_stripped() {
    // When a hook exists but the HOOK_MARKER has been stripped (tampered),
    // reinstall_hooks_if_tampered must reinstall it.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        // Install hooks normally first.
        hooks::install_hooks().unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        let pre_commit = hooks_dir.join("pre-commit");

        // Tamper: make writable first (hooks are now 0o555), then overwrite.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&pre_commit).unwrap().permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&pre_commit, perms).unwrap();
        }
        fs::write(&pre_commit, "#!/bin/bash\necho tampered\nexit 0\n").unwrap();

        let content = fs::read_to_string(&pre_commit).unwrap();
        assert!(
            !content.contains(HOOK_MARKER),
            "precondition: tampered hook must not contain HOOK_MARKER"
        );

        reinstall_hooks_if_tampered(&logger).unwrap();

        // Hook must now contain the marker again.
        let restored = fs::read_to_string(&pre_commit).unwrap();
        assert!(
            restored.contains(HOOK_MARKER),
            "reinstalled hook must contain HOOK_MARKER after tamper detection"
        );
    });
}

#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_marker() {
    // When the marker is deleted mid-agent-phase but hooks still exist,
    // ensure_agent_phase_protections must recreate the marker.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".no_agent_commit");
        assert!(marker.exists(), "precondition: marker must exist");

        // Simulate agent deleting the marker.
        fs::remove_file(&marker).unwrap();
        assert!(!marker.exists(), "precondition: marker must be deleted");

        let _ = ensure_agent_phase_protections(&logger);

        assert!(
            marker.exists(),
            ".no_agent_commit must be recreated by ensure_agent_phase_protections"
        );

        // Cleanup.
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_hooks() {
    // When hooks are deleted mid-agent-phase but the marker still exists,
    // ensure_agent_phase_protections must reinstall hooks.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let hooks_dir = get_hooks_dir().unwrap();

        // Precondition: hooks exist.
        assert!(
            hooks_dir.join("pre-commit").exists(),
            "precondition: pre-commit must exist"
        );

        // Simulate agent deleting hooks.
        fs::remove_file(hooks_dir.join("pre-commit")).unwrap();
        fs::remove_file(hooks_dir.join("pre-push")).unwrap();

        let _ = ensure_agent_phase_protections(&logger);

        // Hooks must be reinstalled.
        assert!(
            hooks_dir.join("pre-commit").exists(),
            "pre-commit must be reinstalled by ensure_agent_phase_protections"
        );
        assert!(
            hooks_dir.join("pre-push").exists(),
            "pre-push must be reinstalled by ensure_agent_phase_protections"
        );
        let content = fs::read_to_string(hooks_dir.join("pre-commit")).unwrap();
        assert!(
            content.contains(HOOK_MARKER),
            "reinstalled hook must contain HOOK_MARKER"
        );

        // Cleanup.
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

// =========================================================================
// New hardening tests: wrapper permissions, pre-merge-commit, tamper auditing
// =========================================================================

#[cfg(unix)]
#[test]
#[serial]
fn test_git_wrapper_script_is_read_only() {
    // After start_agent_phase(), the wrapper script should have mode 0o555
    // (read-only executable) to deter agent overwriting.
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Read wrapper path from track file.
        let track_content =
            fs::read_to_string(dir.path().join(".agent/git-wrapper-dir.txt")).unwrap();
        let wrapper_path = std::path::PathBuf::from(track_content.trim()).join("git");
        assert!(wrapper_path.exists(), "wrapper script must exist");

        let mode = fs::metadata(&wrapper_path).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o555,
            "wrapper script should have mode 0o555 (read-only executable), got {mode:#o}"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        uninstall_hooks(&logger).unwrap();
    });
}

#[test]
#[serial]
fn test_pre_merge_commit_hook_installed() {
    // install_hooks() should create a pre-merge-commit hook alongside pre-commit and pre-push.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        hooks::install_hooks().unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        for hook_name in RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            assert!(hook_path.exists(), "{hook_name} must be installed");
            let content = fs::read_to_string(&hook_path).unwrap();
            assert!(
                content.contains(HOOK_MARKER),
                "{hook_name} must contain HOOK_MARKER"
            );
        }
    });
}

#[test]
#[serial]
fn test_pre_merge_commit_hook_blocks_when_marker_exists() {
    // The pre-merge-commit hook should block when .no_agent_commit is present.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        hooks::install_hooks().unwrap();

        // Create the marker file.
        let marker = dir.path().join(".no_agent_commit");
        File::create(&marker).unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        let hook_path = hooks_dir.join("pre-merge-commit");
        assert!(
            hook_path.exists(),
            "pre-merge-commit hook must be installed"
        );

        let output = Command::new("bash")
            .arg(&hook_path)
            .output()
            .expect("bash must be available to run hook script");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        let combined = format!("{stdout}{stderr}");

        assert_ne!(
            output.status.code(),
            Some(0),
            "hook should exit non-zero when .no_agent_commit is present; output: {combined}"
        );
        assert!(
            combined.to_lowercase().contains("blocked"),
            "hook output should mention 'blocked'; got: {combined}"
        );
    });
}

#[test]
#[serial]
fn test_ensure_agent_phase_protections_returns_tampering_when_marker_deleted() {
    // When the marker is deleted, ensure_agent_phase_protections should return
    // a result indicating tampering was detected.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".no_agent_commit");
        // Delete the marker to simulate tampering.
        fs::remove_file(&marker).unwrap();

        let result = ensure_agent_phase_protections(&logger);
        assert!(
            result.tampering_detected,
            "should report tampering when marker is deleted"
        );
        assert!(
            !result.details.is_empty(),
            "should have details about what was self-healed"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[test]
#[serial]
fn test_ensure_agent_phase_protections_returns_tampering_when_hooks_deleted() {
    // When hooks are deleted, ensure_agent_phase_protections should return
    // a result indicating tampering was detected.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        // Delete all hooks to simulate tampering.
        for hook_name in RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            if hook_path.exists() {
                let _ = fs::remove_file(&hook_path);
            }
        }

        let result = ensure_agent_phase_protections(&logger);
        assert!(
            result.tampering_detected,
            "should report tampering when hooks are deleted"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[test]
#[serial]
fn test_wrapper_unsets_git_env_vars() {
    // The wrapper script content should include env var unsetting for
    // GIT_DIR, GIT_WORK_TREE, and GIT_EXEC_PATH.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_content =
            fs::read_to_string(dir.path().join(".agent/git-wrapper-dir.txt")).unwrap();
        let wrapper_path = std::path::PathBuf::from(track_content.trim()).join("git");
        let content = fs::read_to_string(&wrapper_path).unwrap();

        for var in &["GIT_DIR", "GIT_WORK_TREE", "GIT_EXEC_PATH"] {
            assert!(
                content.contains(&format!("unset {var}")),
                "wrapper must unset {var}; got:\n{content}"
            );
        }

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        uninstall_hooks(&logger).unwrap();
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_missing_wrapper_script() {
    // If an agent deletes the wrapper script file mid-run, ensure_agent_phase_protections
    // should restore it (wrapper + hooks is defense-in-depth).
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_content =
            fs::read_to_string(dir.path().join(".agent/git-wrapper-dir.txt")).unwrap();
        let wrapper_path = std::path::PathBuf::from(track_content.trim()).join("git");
        assert!(wrapper_path.exists(), "precondition: wrapper must exist");

        // Simulate agent deleting the wrapper script.
        fs::remove_file(&wrapper_path).unwrap();
        assert!(
            !wrapper_path.exists(),
            "precondition: wrapper must be deleted"
        );

        let result = ensure_agent_phase_protections(&logger);
        assert!(result.tampering_detected, "must report tampering");

        assert!(wrapper_path.exists(), "wrapper script must be restored");
        let mode = fs::metadata(&wrapper_path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o555, "wrapper script must be 0o555, got {mode:#o}");
        let content = fs::read_to_string(&wrapper_path).unwrap();
        assert!(
            content.contains("Blocked:"),
            "restored wrapper must contain blocking message; got:\n{content}"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_disable_git_wrapper_handles_read_only_wrapper() {
    // Cleanup must work even though the wrapper script is now read-only (0o555).
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        git2::Repository::init(".").unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_content =
            fs::read_to_string(dir.path().join(".agent/git-wrapper-dir.txt")).unwrap();
        let wrapper_dir = std::path::PathBuf::from(track_content.trim());
        assert!(wrapper_dir.exists(), "wrapper dir must exist");

        // disable_git_wrapper should handle the read-only wrapper gracefully.
        disable_git_wrapper(&mut helpers);

        assert!(
            !wrapper_dir.exists(),
            "wrapper dir must be cleaned up even when wrapper is read-only"
        );

        // Cleanup remaining
        end_agent_phase();
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        uninstall_hooks(&logger).unwrap();
    });
}

#[test]
#[serial]
fn test_ensure_agent_phase_protections_noop_when_phase_ended() {
    // When called (run_with_prompt calls this before every agent spawn),
    // ensure_agent_phase_protections must enforce protections even if they are missing.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        git2::Repository::init(".").unwrap();

        let marker = std::path::Path::new(".no_agent_commit");
        assert!(!marker.exists(), "precondition: marker must not exist");

        let result = ensure_agent_phase_protections(&logger);

        assert!(marker.exists(), "marker must be created when missing");
        assert!(
            result.tampering_detected,
            "missing protections should be treated as tampering"
        );
        let hooks_dir = get_hooks_dir().unwrap();
        for &hook_name in RALPH_HOOK_NAMES {
            assert!(
                hooks_dir.join(hook_name).exists(),
                "{hook_name} must be created"
            );
        }
    });
}
