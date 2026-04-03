//! System tests for git helper functions requiring real git repositories.
//!
//! These tests use real `git2::Repository` and filesystem operations.
//! Tests that can use `MemoryWorkspace` should remain in the unit tests.

use ralph_workflow::git_helpers::get_hooks_dir;
use ralph_workflow::git_helpers::runtime::hooks::HOOK_MARKER;
use ralph_workflow::git_helpers::{
    self, capture_head_oid, cleanup_orphaned_marker, detect_unauthorized_commit,
    disable_git_wrapper, end_agent_phase, end_agent_phase_in_repo, ensure_agent_phase_protections,
    git_snapshot, git_snapshot_in_repo, reinstall_hooks_if_tampered, runtime::hooks,
    runtime::hooks::RALPH_HOOK_NAMES, start_agent_phase, start_agent_phase_in_repo,
    try_remove_ralph_dir, uninstall_hooks, uninstall_hooks_in_repo, verify_hooks_removed,
    GitHelpers,
};
use ralph_workflow::logger::Logger;
use ralph_workflow::pipeline::AgentPhaseGuard;
use ralph_workflow::workspace::WorkspaceFs;
use serial_test::serial;
use std::fs::{self, File};
use std::process::Command;
use test_helpers::git_safety::assert_in_isolated_temp_repo;

fn program_exists(name: &str) -> bool {
    Command::new(name).arg("--version").output().is_ok()
}

fn resolve_real_git_from_path() -> std::path::PathBuf {
    std::env::var("PATH")
        .unwrap()
        .split(':')
        .map(std::path::PathBuf::from)
        .map(|entry| entry.join("git"))
        .find(|candidate| {
            candidate.exists()
                && candidate
                    .parent()
                    .and_then(std::path::Path::file_name)
                    .and_then(std::ffi::OsStr::to_str)
                    .is_none_or(|name| !name.starts_with("ralph-git-wrapper-"))
        })
        .unwrap()
}

fn assert_wrapper_blocks(output: &std::process::Output, context: &str) {
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !output.status.success(),
        "{context} should be blocked; output: {combined}"
    );
    assert!(
        combined.to_lowercase().contains("blocked"),
        "{context} should report enforcement; got: {combined}"
    );
}

fn init_repo_with_commit(path: &std::path::Path) -> git2::Repository {
    let repo = init_repo_guarded(path);
    let sig = git2::Signature::now("test", "test@test.com").unwrap();
    fs::write(path.join("tracked.txt"), "tracked\n").unwrap();
    let mut index = repo.index().unwrap();
    index.add_path(std::path::Path::new("tracked.txt")).unwrap();
    index.write().unwrap();
    let tree_oid = index.write_tree().unwrap();
    let tree = repo.find_tree(tree_oid).unwrap();
    repo.commit(Some("HEAD"), &sig, &sig, "initial", &tree, &[])
        .unwrap();
    drop(tree);
    repo
}

fn init_repo_guarded(path: impl AsRef<std::path::Path>) -> git2::Repository {
    let path = path.as_ref();
    assert_in_isolated_temp_repo(path);
    git2::Repository::init(path).unwrap()
}

fn linked_worktree_git_dir(worktree_root: &std::path::Path) -> std::path::PathBuf {
    git2::Repository::open(worktree_root)
        .unwrap()
        .path()
        .to_path_buf()
}

fn assert_ralph_hook_installed(hooks_dir: &std::path::Path) {
    for hook_name in RALPH_HOOK_NAMES {
        let hook_path = hooks_dir.join(hook_name);
        assert!(
            hook_path.exists(),
            "expected Ralph hook at {}",
            hook_path.display()
        );
        let content = fs::read_to_string(&hook_path).unwrap();
        assert!(
            content.contains(HOOK_MARKER),
            "expected Ralph marker in {}",
            hook_path.display()
        );
    }
}

fn assert_no_ralph_hooks(hooks_dir: &std::path::Path) {
    for hook_name in RALPH_HOOK_NAMES {
        let hook_path = hooks_dir.join(hook_name);
        assert!(
            !hook_path.exists(),
            "did not expect Ralph hook at {}",
            hook_path.display()
        );
    }
}

fn worktree_config_file(worktree_root: &std::path::Path) -> std::path::PathBuf {
    git_helpers::resolve_protection_scope_from(worktree_root)
        .unwrap()
        .worktree_config_path
        .expect("expected worktree config path")
}

fn scoped_hooks_dir(worktree_root: &std::path::Path) -> std::path::PathBuf {
    git_helpers::resolve_protection_scope_from(worktree_root)
        .unwrap()
        .hooks_dir
}

fn read_config_value(path: &std::path::Path, key: &str) -> Option<String> {
    if !path.exists() {
        return None;
    }

    let config = git2::Config::open(path).unwrap();
    config.get_string(key).ok()
}

fn create_linked_worktree_fixture() -> (
    tempfile::TempDir,
    std::path::PathBuf,
    std::path::PathBuf,
    std::path::PathBuf,
) {
    let tempdir = tempfile::tempdir().unwrap();
    let root_repo_path = tempdir.path().join("main");
    fs::create_dir_all(&root_repo_path).unwrap();
    let root_repo = init_repo_with_commit(&root_repo_path);

    let worktree_one = tempdir.path().join("wt-one");
    let worktree_two = tempdir.path().join("wt-two");
    let _wt_one = root_repo.worktree("wt-one", &worktree_one, None).unwrap();
    let _wt_two = root_repo.worktree("wt-two", &worktree_two, None).unwrap();

    (tempdir, root_repo_path, worktree_one, worktree_two)
}

#[test]
#[serial]
fn test_agent_phase_cleanup_removes_git_wrapper_track_file() {
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Precondition: wrapper track file exists.
        assert!(
            dir.path().join(".git/ralph/git-wrapper-dir.txt").exists(),
            "expected wrapper track file to exist after start_agent_phase"
        );

        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();

        assert!(
            !dir.path().join(".git/ralph/git-wrapper-dir.txt").exists(),
            "expected wrapper track file to be removed by disable_git_wrapper"
        );
    });
}

#[test]
#[serial]
fn test_linked_worktree_start_agent_phase_keeps_root_and_sibling_unmodified() {
    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, worktree_two) = create_linked_worktree_fixture();

    let root_hooks_dir = root_repo.join(".git/hooks");
    let root_ralph_dir = root_repo.join(".git/ralph");
    let wt_one_hooks_dir = scoped_hooks_dir(&worktree_one);
    let wt_one_git_dir = linked_worktree_git_dir(&worktree_one);
    let wt_two_git_dir = linked_worktree_git_dir(&worktree_two);

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&worktree_one, &mut helpers).unwrap();

    assert_ralph_hook_installed(&wt_one_hooks_dir);
    assert!(wt_one_git_dir.join("ralph/no_agent_commit").exists());
    assert!(wt_one_git_dir.join("ralph/git-wrapper-dir.txt").exists());

    assert_no_ralph_hooks(&root_hooks_dir);
    assert!(
        !root_ralph_dir.exists(),
        "root repo Ralph dir must stay untouched"
    );
    assert_no_ralph_hooks(&wt_two_git_dir.join("hooks"));
    assert!(
        !wt_two_git_dir.join("ralph").exists(),
        "sibling worktree Ralph dir must stay untouched"
    );

    end_agent_phase_in_repo(&worktree_one);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
    assert!(try_remove_ralph_dir(&worktree_one));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_linked_worktree_absolute_git_commit_is_blocked_by_worktree_local_hooks() {
    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let real_git = resolve_real_git_from_path();
    let (_tempdir, _root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&worktree_one, &mut helpers).unwrap();

    let output = Command::new(&real_git)
        .current_dir(&worktree_one)
        .env("GIT_AUTHOR_NAME", "Test User")
        .env("GIT_AUTHOR_EMAIL", "test@example.com")
        .env("GIT_COMMITTER_NAME", "Test User")
        .env("GIT_COMMITTER_EMAIL", "test@example.com")
        .args(["commit", "--allow-empty", "-m", "blocked"])
        .output()
        .unwrap();

    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !output.status.success(),
        "absolute git commit should be blocked in linked worktree; output: {combined}"
    );
    assert!(
        combined.to_lowercase().contains("blocked"),
        "blocked commit should report hook enforcement; got: {combined}"
    );

    end_agent_phase_in_repo(&worktree_one);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
    assert!(try_remove_ralph_dir(&worktree_one));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_linked_worktree_cleanup_only_removes_active_worktree_protection() {
    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, worktree_two) = create_linked_worktree_fixture();

    let root_hooks_dir = root_repo.join(".git/hooks");
    let root_ralph_dir = root_repo.join(".git/ralph");
    fs::create_dir_all(&root_hooks_dir).unwrap();
    fs::create_dir_all(&root_ralph_dir).unwrap();
    let root_hook = root_hooks_dir.join("pre-commit");
    fs::write(
        &root_hook,
        format!("#!/usr/bin/env bash\n# {HOOK_MARKER}\nexit 0\n"),
    )
    .unwrap();
    fs::write(root_ralph_dir.join("no_agent_commit"), "").unwrap();

    let wt_two_git_dir = linked_worktree_git_dir(&worktree_two);
    let wt_two_hooks_dir = wt_two_git_dir.join("hooks");
    let wt_two_ralph_dir = wt_two_git_dir.join("ralph");
    fs::create_dir_all(&wt_two_hooks_dir).unwrap();
    fs::create_dir_all(&wt_two_ralph_dir).unwrap();
    let wt_two_hook = wt_two_hooks_dir.join("pre-commit");
    fs::write(
        &wt_two_hook,
        format!("#!/usr/bin/env bash\n# {HOOK_MARKER}\nexit 0\n"),
    )
    .unwrap();
    fs::write(wt_two_ralph_dir.join("no_agent_commit"), "").unwrap();

    let wt_one_git_dir = linked_worktree_git_dir(&worktree_one);
    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&worktree_one, &mut helpers).unwrap();

    end_agent_phase_in_repo(&worktree_one);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
    assert!(try_remove_ralph_dir(&worktree_one));

    assert!(
        root_hook.exists(),
        "root repo hook must survive linked-worktree cleanup"
    );
    assert!(
        root_ralph_dir.join("no_agent_commit").exists(),
        "root repo marker must survive linked-worktree cleanup"
    );
    assert!(
        wt_two_hook.exists(),
        "sibling worktree hook must survive cleanup"
    );
    assert!(
        wt_two_ralph_dir.join("no_agent_commit").exists(),
        "sibling worktree marker must survive cleanup"
    );
    assert!(
        !wt_one_git_dir.join("ralph/no_agent_commit").exists(),
        "active worktree marker should be removed during cleanup"
    );

    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_root_start_agent_phase_does_not_touch_linked_worktree_protection_paths() {
    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();
    let wt_one_git_dir = linked_worktree_git_dir(&worktree_one);
    let root_scoped_hooks_dir = scoped_hooks_dir(&root_repo);

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    assert_ralph_hook_installed(&root_scoped_hooks_dir);
    assert!(root_repo.join(".git/ralph/no_agent_commit").exists());
    assert_no_ralph_hooks(&wt_one_git_dir.join("hooks"));
    assert!(
        !wt_one_git_dir.join("ralph").exists(),
        "linked worktree Ralph dir must stay untouched during root run"
    );

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    assert!(
        !root_repo.join(".git/ralph").exists(),
        "root cleanup should remove only root Ralph dir"
    );
    assert!(
        !wt_one_git_dir.join("ralph").exists(),
        "linked worktree Ralph dir should remain untouched after root cleanup"
    );
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_root_start_agent_phase_scopes_absolute_git_commit_blocking_to_root_worktree_only() {
    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let real_git = resolve_real_git_from_path();
    let (_tempdir, root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    let output = Command::new(&real_git)
        .current_dir(&worktree_one)
        .env("GIT_AUTHOR_NAME", "Test User")
        .env("GIT_AUTHOR_EMAIL", "test@example.com")
        .env("GIT_COMMITTER_NAME", "Test User")
        .env("GIT_COMMITTER_EMAIL", "test@example.com")
        .args(["commit", "--allow-empty", "-m", "allowed-from-sibling"])
        .output()
        .unwrap();

    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        output.status.success(),
        "root-repo protection must not block sibling linked worktree commits; output: {combined}"
    );

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_root_start_agent_phase_blocks_absolute_git_commit_when_command_targets_root_repo() {
    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let real_git = resolve_real_git_from_path();
    let (_tempdir, root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    let output = Command::new(&real_git)
        .current_dir(&worktree_one)
        .env("GIT_AUTHOR_NAME", "Test User")
        .env("GIT_AUTHOR_EMAIL", "test@example.com")
        .env("GIT_COMMITTER_NAME", "Test User")
        .env("GIT_COMMITTER_EMAIL", "test@example.com")
        .args([
            "-C",
            root_repo.to_str().unwrap(),
            "commit",
            "--allow-empty",
            "-m",
            "blocked-root-target",
        ])
        .output()
        .unwrap();

    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !output.status.success(),
        "root-repo protection must block commands that explicitly target the protected root repo; output: {combined}"
    );
    assert!(
        combined.to_lowercase().contains("blocked"),
        "blocked root-target commit should report enforcement; got: {combined}"
    );

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_root_start_agent_phase_blocks_wrapper_git_tag_with_dash_c_target() {
    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    let output = Command::new("git")
        .current_dir(&worktree_one)
        .args([
            "-C",
            root_repo.to_str().unwrap(),
            "tag",
            "blocked-via-dash-c",
        ])
        .output()
        .unwrap();

    assert_wrapper_blocks(&output, "git -C <protected-root> tag");

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_root_start_agent_phase_blocks_flag_only_mutating_git_branch_forms() {
    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    let output = Command::new("git")
        .current_dir(&worktree_one)
        .args([
            "-C",
            root_repo.to_str().unwrap(),
            "branch",
            "--unset-upstream",
        ])
        .output()
        .unwrap();

    assert_wrapper_blocks(&output, "git branch --unset-upstream");

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[cfg(unix)]
#[test]
#[serial]
fn test_root_start_agent_phase_blocks_wrapper_git_tag_when_repo_is_targeted_via_symlink_alias() {
    use std::os::unix::fs::symlink;

    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();
    let root_alias = worktree_one.join("root-alias");
    symlink(&root_repo, &root_alias).unwrap();

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    let output = Command::new("git")
        .current_dir(&worktree_one)
        .args([
            "-C",
            root_alias.to_str().unwrap(),
            "tag",
            "blocked-via-symlink-alias",
        ])
        .output()
        .unwrap();

    assert_wrapper_blocks(&output, "git -C <protected-root-symlink> tag");

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_root_start_agent_phase_blocks_wrapper_git_tag_with_git_dir_and_work_tree_targets() {
    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    let output = Command::new("git")
        .current_dir(&worktree_one)
        .args([
            "--git-dir",
            root_repo.join(".git").to_str().unwrap(),
            "--work-tree",
            root_repo.to_str().unwrap(),
            "tag",
            "blocked-via-git-dir-work-tree",
        ])
        .output()
        .unwrap();

    assert_wrapper_blocks(
        &output,
        "git --git-dir <protected> --work-tree <protected> tag",
    );

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_linked_worktree_start_agent_phase_blocks_wrapper_git_tag_with_git_dir_only_target() {
    if !program_exists("git") {
        return;
    }

    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, _root_repo, worktree_one, _worktree_two) = create_linked_worktree_fixture();
    let outside_dir = tempfile::tempdir().unwrap();
    let worktree_git_dir = linked_worktree_git_dir(&worktree_one);

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&worktree_one, &mut helpers).unwrap();

    let output = Command::new("git")
        .current_dir(outside_dir.path())
        .args([
            "--git-dir",
            worktree_git_dir.to_str().unwrap(),
            "tag",
            "blocked-via-git-dir-only",
        ])
        .output()
        .unwrap();

    assert_wrapper_blocks(&output, "git --git-dir <protected> tag");

    end_agent_phase_in_repo(&worktree_one);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
    assert!(try_remove_ralph_dir(&worktree_one));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_linked_worktree_repeated_start_cleanup_cycles_leave_no_scoped_state_behind() {
    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, worktree_two) = create_linked_worktree_fixture();
    let worktree_one_config = worktree_config_file(&worktree_one);
    let root_config = worktree_config_file(&root_repo);
    let sibling_config = worktree_config_file(&worktree_two);

    for _ in 0..2 {
        let mut helpers = GitHelpers::default();
        start_agent_phase_in_repo(&worktree_one, &mut helpers).unwrap();

        assert!(
            worktree_one_config.exists(),
            "active worktree config should exist while protection is installed"
        );
        assert!(
            !root_config.exists(),
            "linked worktree run must not create root config.worktree"
        );
        assert!(
            !sibling_config.exists(),
            "linked worktree run must not create sibling config.worktree"
        );

        end_agent_phase_in_repo(&worktree_one);
        disable_git_wrapper(&mut helpers);
        uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
        assert!(try_remove_ralph_dir(&worktree_one));
        ralph_workflow::git_helpers::clear_agent_phase_global_state();

        assert!(
            !worktree_one_config.exists(),
            "cleanup should remove the active worktree config override"
        );
        assert!(
            !root_config.exists(),
            "cleanup must not leave root config.worktree behind"
        );
        assert!(
            !sibling_config.exists(),
            "cleanup must not leave sibling config.worktree behind"
        );
        assert!(
            !scoped_hooks_dir(&worktree_one).exists(),
            "cleanup should remove scoped hooks dir after linked-worktree run"
        );
    }
}

#[test]
#[serial]
fn test_root_start_agent_phase_writes_only_main_worktree_hook_config() {
    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, worktree_two) = create_linked_worktree_fixture();

    let root_config = worktree_config_file(&root_repo);
    let wt_one_config = worktree_config_file(&worktree_one);
    let wt_two_config = worktree_config_file(&worktree_two);
    let common_config = root_repo.join(".git/config");
    let shared_hooks_dir = root_repo.join(".git/hooks");
    let root_scoped_hooks_dir = scoped_hooks_dir(&root_repo);

    let mut helpers = GitHelpers::default();
    start_agent_phase_in_repo(&root_repo, &mut helpers).unwrap();

    assert_no_ralph_hooks(&shared_hooks_dir);
    assert_ralph_hook_installed(&root_scoped_hooks_dir);
    let root_config_contents = fs::read_to_string(&root_config).unwrap();
    assert!(
        root_config_contents.contains("hooksPath"),
        "root worktree config must own the scoped hooksPath override"
    );
    assert!(
        !wt_one_config.exists(),
        "root run must not create sibling worktree config overrides"
    );
    assert!(
        !wt_two_config.exists(),
        "root run must not create second sibling worktree config overrides"
    );
    let common_config_contents = fs::read_to_string(&common_config).unwrap();
    assert!(
        !common_config_contents.contains("hooksPath"),
        "shared common config must not receive scoped hooksPath overrides"
    );

    end_agent_phase_in_repo(&root_repo);
    disable_git_wrapper(&mut helpers);
    uninstall_hooks_in_repo(&root_repo, &logger).unwrap();
    assert!(try_remove_ralph_dir(&root_repo));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();
}

#[test]
#[serial]
fn test_last_active_linked_worktree_cleanup_restores_shared_worktree_extension_state() {
    let _guard = ralph_workflow::git_helpers::agent_phase_test_lock()
        .lock()
        .unwrap();
    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
    let (_tempdir, root_repo, worktree_one, worktree_two) = create_linked_worktree_fixture();
    let common_config = root_repo.join(".git/config");

    assert_eq!(
        read_config_value(&common_config, "extensions.worktreeConfig"),
        None,
        "fixture should start without a shared worktreeConfig extension override"
    );

    let mut helpers_one = GitHelpers::default();
    start_agent_phase_in_repo(&worktree_one, &mut helpers_one).unwrap();
    assert_eq!(
        read_config_value(&common_config, "extensions.worktreeConfig").as_deref(),
        Some("true"),
        "first protected linked worktree should enable shared worktreeConfig support"
    );

    let mut helpers_two = GitHelpers::default();
    start_agent_phase_in_repo(&worktree_two, &mut helpers_two).unwrap();

    end_agent_phase_in_repo(&worktree_one);
    disable_git_wrapper(&mut helpers_one);
    uninstall_hooks_in_repo(&worktree_one, &logger).unwrap();
    assert!(try_remove_ralph_dir(&worktree_one));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();

    assert_eq!(
        read_config_value(&common_config, "extensions.worktreeConfig").as_deref(),
        Some("true"),
        "shared worktreeConfig support must remain enabled while another linked worktree is still protected"
    );

    end_agent_phase_in_repo(&worktree_two);
    disable_git_wrapper(&mut helpers_two);
    uninstall_hooks_in_repo(&worktree_two, &logger).unwrap();
    assert!(try_remove_ralph_dir(&worktree_two));
    ralph_workflow::git_helpers::clear_agent_phase_global_state();

    assert_eq!(
        read_config_value(&common_config, "extensions.worktreeConfig"),
        None,
        "last protected linked worktree cleanup must restore the shared extension to its original missing state"
    );
}

#[test]
#[serial]
fn test_disable_git_wrapper_removes_track_file_even_when_cwd_changes() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_file = dir.path().join(".git/ralph/git-wrapper-dir.txt");
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
fn test_start_agent_phase_in_repo_uses_target_repo_not_cwd_repo() {
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|cwd_repo| {
        init_repo_guarded(".");

        let target_repo = tempfile::tempdir().expect("create target repo tempdir");
        init_repo_guarded(target_repo.path());

        let mut helpers = GitHelpers::default();
        start_agent_phase_in_repo(target_repo.path(), &mut helpers).unwrap();

        assert!(
            target_repo
                .path()
                .join(".git/ralph/no_agent_commit")
                .exists(),
            "target repo should receive the agent marker"
        );
        assert!(
            target_repo
                .path()
                .join(".git/ralph/git-wrapper-dir.txt")
                .exists(),
            "target repo should receive the wrapper track file"
        );
        for hook_name in RALPH_HOOK_NAMES {
            let hook_path = target_repo.path().join(".git/hooks").join(hook_name);
            assert!(
                hook_path.exists(),
                "target repo should receive hook {hook_name}"
            );
            let content = fs::read_to_string(&hook_path).unwrap();
            assert!(
                content.contains(HOOK_MARKER),
                "target repo hook should contain Ralph marker"
            );
        }

        assert!(
            !cwd_repo.path().join(".git/ralph/no_agent_commit").exists(),
            "cwd repo must not receive the marker for another repo"
        );
        assert!(
            !cwd_repo
                .path()
                .join(".git/ralph/git-wrapper-dir.txt")
                .exists(),
            "cwd repo must not receive the wrapper track file for another repo"
        );

        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks_in_repo(target_repo.path(), &logger).unwrap();
    });
}

#[test]
#[serial]
fn test_git_snapshot() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|_dir| {
        init_repo_guarded(".");

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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

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

        init_repo_guarded(dir_path);

        // Create marker.
        let marker_path = dir_path.join(".git/ralph/no_agent_commit");
        fs::create_dir_all(dir_path.join(".git/ralph")).unwrap();
        File::create(&marker_path).unwrap();
        assert!(marker_path.exists());

        cleanup_orphaned_marker(&logger).unwrap();
        assert!(!marker_path.exists());
    });
}

#[test]
#[serial]
fn test_cleanup_orphaned_marker_removes_legacy_root_marker() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        let dir_path = dir.path();

        init_repo_guarded(dir_path);

        let legacy_marker_path = dir_path.join(".no_agent_commit");
        File::create(&legacy_marker_path).unwrap();
        assert!(legacy_marker_path.exists());

        cleanup_orphaned_marker(&logger).unwrap();
        assert!(!legacy_marker_path.exists());
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_start_agent_phase_quarantines_symlinked_ralph_dir() {
    use std::os::unix::fs::symlink;
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let outside = tempfile::tempdir().unwrap();
        let ralph_dir = dir.path().join(".git/ralph");
        symlink(outside.path(), &ralph_dir).unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let ralph_meta = fs::symlink_metadata(&ralph_dir).unwrap();
        assert!(
            ralph_meta.is_dir() && !ralph_meta.file_type().is_symlink(),
            "start_agent_phase should recreate ralph dir as a real directory"
        );
        assert!(
            ralph_dir.join("no_agent_commit").is_file(),
            "marker should be created inside the repo-owned ralph dir"
        );
        assert!(
            !outside.path().join("no_agent_commit").exists(),
            "marker creation must not follow a symlinked ralph dir outside the repo"
        );

        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_install_hooks_in_repo_quarantines_symlinked_ralph_dir() {
    use std::os::unix::fs::symlink;
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let outside = tempfile::tempdir().unwrap();
        let ralph_dir = dir.path().join(".git/ralph");
        symlink(outside.path(), &ralph_dir).unwrap();

        hooks::install_hooks_in_repo(dir.path()).unwrap();

        let ralph_meta = fs::symlink_metadata(&ralph_dir).unwrap();
        assert!(
            ralph_meta.is_dir() && !ralph_meta.file_type().is_symlink(),
            "install_hooks_in_repo should recreate ralph dir as a real directory"
        );
        assert!(
            !outside.path().join("no_agent_commit").exists()
                && !outside.path().join("git-wrapper-dir.txt").exists()
                && !outside.path().join("hooks").exists(),
            "hook setup must not write enforcement artifacts through a symlinked ralph dir"
        );
    });
}

#[test]
#[serial]
fn test_git_add_specific_in_repo_skips_legacy_root_marker() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo_root = dir.path();
        let _repo = init_repo_guarded(repo_root);
        fs::write(repo_root.join(".no_agent_commit"), "legacy").unwrap();

        let staged =
            git_helpers::git_add_specific_in_repo(repo_root, &[".no_agent_commit"]).unwrap();

        assert!(
            !staged,
            "legacy root marker should be ignored instead of staged for commit"
        );
    });
}

#[test]
#[serial]
fn test_git_add_all_in_repo_skips_legacy_root_marker() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo_root = dir.path();
        let repo = init_repo_guarded(repo_root);

        fs::write(repo_root.join("tracked.txt"), "tracked").unwrap();
        fs::write(repo_root.join(".no_agent_commit"), "legacy").unwrap();

        let staged = git_helpers::git_add_all_in_repo(repo_root).unwrap();
        assert!(staged, "tracked content should still be staged");

        let index = repo.index().unwrap();
        assert!(
            index
                .get_path(std::path::Path::new("tracked.txt"), 0)
                .is_some(),
            "tracked file should be staged"
        );
        assert!(
            index
                .get_path(std::path::Path::new(".no_agent_commit"), 0)
                .is_none(),
            "legacy root marker should not be staged"
        );
    });
}

#[test]
#[serial]
fn test_cleanup_agent_phase_silent_uses_stored_ralph_dir() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo_root = dir.path().to_path_buf();
        fs::create_dir_all(repo_root.join(".git")).unwrap();

        let actual_git_dir = tempfile::tempdir().unwrap();
        let stored_ralph_dir = actual_git_dir.path().join("ralph");
        fs::create_dir_all(&stored_ralph_dir).unwrap();

        let marker = stored_ralph_dir.join("no_agent_commit");
        let head_oid = stored_ralph_dir.join("head-oid.txt");
        let track_file = stored_ralph_dir.join("git-wrapper-dir.txt");
        fs::write(&marker, "").unwrap();
        fs::write(&head_oid, "abc123\n").unwrap();

        let wrapper_dir = tempfile::Builder::new()
            .prefix("ralph-git-wrapper-")
            .tempdir()
            .unwrap();
        let wrapper_dir_path = wrapper_dir.keep();
        fs::write(&track_file, format!("{}\n", wrapper_dir_path.display())).unwrap();

        ralph_workflow::git_helpers::set_agent_phase_paths_for_test(
            Some(repo_root.clone()),
            Some(stored_ralph_dir),
            None,
        );

        git_helpers::cleanup_agent_phase_silent();

        assert!(
            !marker.exists(),
            "cleanup must remove the marker from the stored ralph dir"
        );
        assert!(
            !head_oid.exists(),
            "cleanup must remove the head-oid file from the stored ralph dir"
        );
        assert!(
            !track_file.exists(),
            "cleanup must remove the wrapper track file from the stored ralph dir"
        );
        assert!(
            !wrapper_dir_path.exists(),
            "cleanup must remove the wrapper temp dir tracked from the stored ralph dir"
        );
        assert!(
            !repo_root.join(".git/ralph").exists(),
            "cleanup should not recreate a fallback repo_root/.git/ralph directory"
        );

        ralph_workflow::git_helpers::set_agent_phase_paths_for_test(None, None, None);
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
        let repo = init_repo_guarded(".");

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
    let repo = init_repo_guarded(dir.path());

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

#[cfg(unix)]
#[test]
#[serial]
fn test_pre_commit_hook_blocks_when_marker_exists() {
    // Verify the installed pre-commit hook exits non-zero and prints a blocking
    // message when .no_agent_commit is present.
    use test_helpers::with_temp_cwd;

    if !program_exists("bash") {
        return;
    }

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        // Install Ralph-managed hooks.
        hooks::install_hooks().unwrap();

        // Create the marker file that should trigger blocking.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
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

#[cfg(unix)]
#[test]
#[serial]
fn test_pre_commit_hook_passes_when_no_marker() {
    // Verify the installed pre-commit hook exits 0 when .no_agent_commit is absent.
    use test_helpers::with_temp_cwd;

    if !program_exists("bash") {
        return;
    }

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        // Install Ralph-managed hooks.
        hooks::install_hooks().unwrap();

        // Confirm marker is absent.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
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
        init_repo_guarded(".");

        let workspace = WorkspaceFs::new(dir.path().to_path_buf());
        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Preconditions: marker and wrapper track file must exist.
        assert!(
            dir.path().join(".git/ralph/no_agent_commit").exists(),
            "expected .no_agent_commit after start_agent_phase"
        );
        assert!(
            dir.path().join(".git/ralph/git-wrapper-dir.txt").exists(),
            "expected wrapper track file after start_agent_phase"
        );

        // Create guard without calling disarm() — drop must perform cleanup.
        {
            let _guard = AgentPhaseGuard::new(&mut helpers, &logger, &workspace);
            // Drop here without disarm.
        }

        // Marker must be gone.
        assert!(
            !dir.path().join(".git/ralph/no_agent_commit").exists(),
            "expected .no_agent_commit to be removed by AgentPhaseGuard::drop"
        );

        // Wrapper track file must be gone.
        assert!(
            !dir.path().join(".git/ralph/git-wrapper-dir.txt").exists(),
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

        // The .git/ralph/ directory itself must be fully removed, not just emptied.
        assert!(
            !dir.path().join(".git/ralph").exists(),
            "expected .git/ralph/ directory to be fully removed by AgentPhaseGuard::drop"
        );
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
        let repo = init_repo_guarded(".");

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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".git/ralph/no_agent_commit");
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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".git/ralph/no_agent_commit");
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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".git/ralph/no_agent_commit");

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
    init_repo_guarded(dir);

    let mut helpers = GitHelpers::default();
    start_agent_phase(&mut helpers).unwrap();

    // Read the wrapper script path from the track file.
    let track_content = fs::read_to_string(dir.join(".git/ralph/git-wrapper-dir.txt")).unwrap();
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

#[cfg(unix)]
#[test]
#[serial]
fn test_start_agent_phase_creates_marker_in_repo_root_not_cwd() {
    // Regression: start_agent_phase must create the marker in the repo root, not in CWD.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        // Move CWD into a subdirectory within the repo.
        let subdir = dir.path().join("subdir");
        fs::create_dir_all(&subdir).unwrap();
        std::env::set_current_dir(&subdir).unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        assert!(
            dir.path().join(".git/ralph/no_agent_commit").exists(),
            "marker must be created in repo root"
        );
        assert!(
            !subdir.join(".git/ralph/no_agent_commit").exists(),
            "marker must not be created in CWD subdir"
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
fn test_start_agent_phase_repairs_marker_symlink_without_clobbering_target() {
    // Security: start_agent_phase must not follow a marker symlink.
    // It may self-heal by removing the symlink and creating a real marker file.
    use std::os::unix::fs::{symlink, PermissionsExt};
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        // Create a target file inside the repo and point the marker symlink at it.
        let target = dir.path().join("marker-target.txt");
        fs::write(&target, "do-not-touch").unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();

        let marker = dir.path().join(".git/ralph/no_agent_commit");
        fs::create_dir_all(dir.path().join(".git/ralph")).unwrap();
        symlink(&target, &marker).unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let meta = fs::symlink_metadata(&marker).unwrap();
        assert!(
            !meta.file_type().is_symlink(),
            "marker must be recreated as a real file (not a symlink)"
        );
        assert_eq!(
            fs::read_to_string(&target).unwrap(),
            "do-not-touch",
            "marker creation must not clobber symlink target"
        );
        let mode = fs::metadata(&target).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "marker code must not chmod symlink target");

        let marker_mode = fs::metadata(&marker).unwrap().permissions().mode() & 0o777;
        assert_eq!(marker_mode, 0o444, "marker must be 0o444");

        // Best-effort cleanup for partial installs.
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        let _ = uninstall_hooks(&logger);
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_end_agent_phase_does_not_chmod_marker_symlink_target() {
    // Security: end_agent_phase must not chmod through a marker symlink.
    use std::os::unix::fs::{symlink, PermissionsExt};
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let target = dir.path().join("marker-target.txt");
        fs::write(&target, "x").unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o444)).unwrap();

        let marker = dir.path().join(".git/ralph/no_agent_commit");
        fs::create_dir_all(dir.path().join(".git/ralph")).unwrap();
        symlink(&target, &marker).unwrap();

        end_agent_phase();

        assert!(
            !marker.exists(),
            "end_agent_phase should remove the marker directory entry"
        );
        let mode = fs::metadata(&target).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o444,
            "end_agent_phase must not chmod symlink target; mode was {mode:#o}"
        );
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_capture_head_oid_refuses_agent_dir_symlink() {
    // Security: capture_head_oid must not write through a symlinked .agent directory.
    use std::os::unix::fs::symlink;
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo = init_repo_guarded(".");

        // Create an initial commit so HEAD exists.
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write("init.txt", "init").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("init.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
            .unwrap();

        let outside = tempfile::tempdir().unwrap();
        fs::remove_dir_all(dir.path().join(".agent")).ok();
        symlink(outside.path(), dir.path().join(".agent")).unwrap();

        capture_head_oid(dir.path());

        assert!(
            !outside.path().join("head-oid.txt").exists(),
            "capture_head_oid must not write outside repo via .agent symlink"
        );
        assert!(
            !outside.path().join("git-wrapper-dir.txt").exists(),
            "no wrapper track file should be written in this test"
        );
    });
}

#[test]
#[serial]
fn test_detect_unauthorized_commit_uses_repo_root_not_cwd() {
    // Regression: detect_unauthorized_commit must compare HEAD in the provided repo_root,
    // not whatever repository happens to be discoverable from CWD.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo_a = dir.path().join("repo_a");
        let repo_b = dir.path().join("repo_b");
        fs::create_dir_all(&repo_a).unwrap();
        fs::create_dir_all(&repo_b).unwrap();

        let repo = init_repo_guarded(&repo_a);
        init_repo_guarded(&repo_b);

        // Commit in repo_a.
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write(repo_a.join("file1.txt"), "c1").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("file1.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "c1", &tree, &[])
            .unwrap();

        // Capture baseline for repo_a, but set CWD to repo_b.
        std::env::set_current_dir(&repo_b).unwrap();
        capture_head_oid(&repo_a);
        assert!(
            !detect_unauthorized_commit(&repo_a),
            "precondition: no unauthorized commit yet"
        );

        // Create second commit in repo_a.
        fs::write(repo_a.join("file2.txt"), "c2").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("file2.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        let parent = repo.head().unwrap().peel_to_commit().unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "c2", &tree, &[&parent])
            .unwrap();

        assert!(
            detect_unauthorized_commit(&repo_a),
            "must detect HEAD change in repo_root regardless of CWD"
        );
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_merge_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
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
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
        let ctx = setup_wrapper_test(dir.path());

        // Simulate agent deleting the marker.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
        fs::remove_file(&marker).unwrap();
        assert!(!marker.exists(), "precondition: marker must be deleted");

        let (_code, output) = run_wrapper(&ctx.wrapper_path, &["merge", "main"]);
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_rebase_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
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
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
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
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
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
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
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
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["stash", "pop"]);
        assert_eq!(code, 1, "stash pop should be blocked; output: {output}");
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_bare_stash_when_marker_exists() {
    // Regression: `git stash` with no subcommand is equivalent to `git stash push`
    // and mutates the working tree. During agent phase we allow only `git stash list`.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["stash"]);
        assert_eq!(code, 1, "bare stash should be blocked; output: {output}");
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_branch_create_when_marker_exists() {
    // Regression: `git branch <name>` creates a new branch and mutates repo state.
    // During agent phase we allow only list-only forms of `git branch`.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["branch", "new-branch"]);
        assert_eq!(code, 1, "branch create should be blocked; output: {output}");
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_checkout_switch_when_marker_exists() {
    // Regression: `git checkout <ref>` changes HEAD and can mutate working tree.
    // During agent phase we allow only read-only git commands.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
        let ctx = setup_wrapper_test(dir.path());
        let (code, output) = run_wrapper(&ctx.wrapper_path, &["checkout", "HEAD"]);
        assert_eq!(code, 1, "checkout should be blocked; output: {output}");
        assert!(output.to_lowercase().contains("blocked"), "got: {output}");
    });
}

#[test]
#[serial]
fn test_git_wrapper_blocks_add_when_marker_exists() {
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
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
        if !program_exists("git") || !program_exists("sh") {
            return;
        }
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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Simulate tampering: delete marker and all hooks.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".git/ralph/no_agent_commit");
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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Read wrapper path from track file.
        let track_content =
            fs::read_to_string(dir.path().join(".git/ralph/git-wrapper-dir.txt")).unwrap();
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
        init_repo_guarded(".");

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

#[cfg(unix)]
#[test]
#[serial]
fn test_pre_merge_commit_hook_blocks_when_marker_exists() {
    // The pre-merge-commit hook should block when .no_agent_commit is present.
    use test_helpers::with_temp_cwd;

    if !program_exists("bash") {
        return;
    }

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        hooks::install_hooks().unwrap();

        // Create the marker file.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let marker = dir.path().join(".git/ralph/no_agent_commit");
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
        init_repo_guarded(".");

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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_content =
            fs::read_to_string(dir.path().join(".git/ralph/git-wrapper-dir.txt")).unwrap();
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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_content =
            fs::read_to_string(dir.path().join(".git/ralph/git-wrapper-dir.txt")).unwrap();
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

#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_missing_wrapper_track_file() {
    // If an agent deletes the wrapper track file mid-run, ensure_agent_phase_protections
    // must recreate it so the wrapper remains an active agent-phase signal.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_file = dir.path().join(".git/ralph/git-wrapper-dir.txt");
        assert!(track_file.exists(), "precondition: track file must exist");

        // Simulate agent deletion.
        fs::remove_file(&track_file).unwrap();
        assert!(
            !track_file.exists(),
            "precondition: track file must be deleted"
        );

        let result = ensure_agent_phase_protections(&logger);
        assert!(result.tampering_detected, "must report tampering");
        assert!(track_file.exists(), "track file must be recreated");

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_ensure_agent_phase_protections_ignores_tampered_wrapper_track_file_path() {
    // Security regression: ensure_agent_phase_protections must treat the track file as
    // untrusted input and must not write the wrapper script to an attacker-chosen path.
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_file = dir.path().join(".git/ralph/git-wrapper-dir.txt");
        let track_content = fs::read_to_string(&track_file).unwrap();
        let real_wrapper_dir = std::path::PathBuf::from(track_content.trim());
        let real_wrapper_path = real_wrapper_dir.join("git");
        assert!(
            real_wrapper_path.exists(),
            "precondition: wrapper must exist"
        );

        // Tamper the track file to point at an attacker-controlled path.
        let evil_dir = dir.path().join("evil_wrapper_target");
        fs::create_dir_all(&evil_dir).unwrap();
        let mut perms = fs::metadata(&track_file).unwrap().permissions();
        perms.set_mode(0o644);
        fs::set_permissions(&track_file, perms).unwrap();
        fs::write(&track_file, evil_dir.display().to_string()).unwrap();

        // Force wrapper restoration.
        fs::remove_file(&real_wrapper_path).unwrap();
        assert!(
            !real_wrapper_path.exists(),
            "precondition: wrapper must be deleted"
        );

        let result = ensure_agent_phase_protections(&logger);
        assert!(result.tampering_detected, "must report tampering");

        assert!(
            real_wrapper_path.exists(),
            "wrapper must be restored in the real wrapper dir"
        );
        assert!(
            !evil_dir.join("git").exists(),
            "must not write wrapper script to attacker-chosen track file path"
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
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_content =
            fs::read_to_string(dir.path().join(".git/ralph/git-wrapper-dir.txt")).unwrap();
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
        init_repo_guarded(".");

        let marker = std::path::Path::new(".git/ralph/no_agent_commit");
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

// =========================================================================
// Hook dual-check tests (marker + track file)
// =========================================================================

#[cfg(unix)]
#[test]
#[serial]
fn test_hook_blocks_when_only_track_file_exists() {
    // Defense-in-depth: if an agent deletes the marker but the track file remains,
    // the hook must still block commits.
    use test_helpers::with_temp_cwd;

    if !program_exists("bash") {
        return;
    }

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        hooks::install_hooks().unwrap();

        // Create the track file (simulates active agent phase).
        let ralph_dir = dir.path().join(".git/ralph");
        fs::write(ralph_dir.join("git-wrapper-dir.txt"), "/tmp/fake-wrapper\n").unwrap();

        // Ensure NO marker exists.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
        assert!(!marker.exists(), "precondition: marker must not exist");

        // The hook should still block because the track file exists.
        let hooks_dir = get_hooks_dir().unwrap();
        let hook_path = hooks_dir.join("pre-commit");
        let output = Command::new("bash")
            .arg(&hook_path)
            .output()
            .expect("bash must be available");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        let combined = format!("{stdout}{stderr}");

        assert_ne!(
            output.status.code(),
            Some(0),
            "hook should block when only track file exists; output: {combined}"
        );
        assert!(
            combined.to_lowercase().contains("blocked"),
            "output should mention 'blocked'; got: {combined}"
        );
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_hook_passes_when_neither_marker_nor_track_file() {
    // When neither marker nor track file exists, the hook should pass.
    use test_helpers::with_temp_cwd;

    if !program_exists("bash") {
        return;
    }

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        hooks::install_hooks().unwrap();

        // Ensure NO marker and NO track file exist.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
        let track_file = dir.path().join(".git/ralph/git-wrapper-dir.txt");
        assert!(!marker.exists(), "precondition: marker must not exist");
        assert!(
            !track_file.exists(),
            "precondition: track file must not exist"
        );

        let hooks_dir = get_hooks_dir().unwrap();
        let hook_path = hooks_dir.join("pre-commit");
        let output = Command::new("bash")
            .arg(&hook_path)
            .output()
            .expect("bash must be available");

        assert_eq!(
            output.status.code(),
            Some(0),
            "hook should pass when neither marker nor track file exists"
        );
    });
}

// =========================================================================
// HEAD OID comparison system tests
// =========================================================================

#[test]
#[serial]
fn test_start_agent_phase_captures_head_oid() {
    // After start_agent_phase, .agent/head-oid.txt should exist with the current HEAD OID.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo = init_repo_guarded(".");

        // Create an initial commit so HEAD exists.
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write("init.txt", "init").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("init.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
            .unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let head_oid_path = dir.path().join(".git/ralph/head-oid.txt");
        assert!(
            head_oid_path.exists(),
            "head-oid.txt must be created by start_agent_phase"
        );

        let stored_oid = fs::read_to_string(&head_oid_path).unwrap();
        assert!(
            !stored_oid.trim().is_empty(),
            "head-oid.txt must contain a non-empty OID"
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
fn test_detect_unauthorized_commit_detects_head_change() {
    // When HEAD changes after the OID was captured, detect_unauthorized_commit must return true.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo = init_repo_guarded(".");

        // Create an initial commit.
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write("file1.txt", "content1").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("file1.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "commit1", &tree, &[])
            .unwrap();

        // Capture current HEAD OID.
        capture_head_oid(dir.path());

        // No change yet — should not detect unauthorized commit.
        assert!(
            !detect_unauthorized_commit(dir.path()),
            "should not detect unauthorized commit when HEAD hasn't changed"
        );

        // Create a second commit (simulating unauthorized commit by agent).
        fs::write("file2.txt", "content2").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("file2.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        let parent = repo.head().unwrap().peel_to_commit().unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "unauthorized", &tree, &[&parent])
            .unwrap();

        // Now detect_unauthorized_commit should return true.
        assert!(
            detect_unauthorized_commit(dir.path()),
            "must detect unauthorized commit when HEAD changes"
        );
    });
}

#[test]
#[serial]
fn test_end_agent_phase_removes_head_oid_file() {
    // end_agent_phase should clean up .agent/head-oid.txt.
    use test_helpers::with_temp_cwd;

    with_temp_cwd(|dir| {
        let repo = init_repo_guarded(".");

        // Create initial commit.
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write("init.txt", "init").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("init.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
            .unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let head_oid_path = dir.path().join(".git/ralph/head-oid.txt");
        assert!(
            head_oid_path.exists(),
            "precondition: head-oid.txt must exist"
        );

        end_agent_phase();

        assert!(
            !head_oid_path.exists(),
            "head-oid.txt must be removed by end_agent_phase"
        );

        // Cleanup remaining
        disable_git_wrapper(&mut helpers);
        let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));
        uninstall_hooks(&logger).unwrap();
    });
}

// =========================================================================
// Track file permission enforcement tests
// =========================================================================

#[cfg(unix)]
#[test]
#[serial]
fn test_ensure_agent_phase_protections_restores_track_file_permissions() {
    // If an agent loosens track file permissions from 0o444 to 0o644,
    // ensure_agent_phase_protections must restore them to 0o444.
    use std::os::unix::fs::PermissionsExt;
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let track_file = dir.path().join(".git/ralph/git-wrapper-dir.txt");
        assert!(track_file.exists(), "precondition: track file must exist");

        // Verify initial permissions are 0o444.
        let mode = fs::metadata(&track_file).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o444, "precondition: track file should be 0o444");

        // Simulate agent loosening permissions.
        let mut perms = fs::metadata(&track_file).unwrap().permissions();
        perms.set_mode(0o644);
        fs::set_permissions(&track_file, perms).unwrap();

        let mode = fs::metadata(&track_file).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o644,
            "precondition: track file must have loosened perms"
        );

        let result = ensure_agent_phase_protections(&logger);

        let mode = fs::metadata(&track_file).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o444,
            "track file permissions must be restored to 0o444"
        );
        assert!(
            result.tampering_detected,
            "should report tampering when track file permissions loosened"
        );

        // Cleanup
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();
    });
}

// =========================================================================
// Cleanup completeness tests
// =========================================================================

#[test]
#[serial]
fn test_finalize_cleanup_leaves_no_artifacts() {
    // After end_agent_phase + disable_git_wrapper + uninstall_hooks,
    // ALL protection artifacts must be gone.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        let repo = init_repo_guarded(".");

        // Create initial commit so HEAD exists for head-oid capture.
        let sig = git2::Signature::now("test", "test@test.com").unwrap();
        fs::write("init.txt", "init").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(std::path::Path::new("init.txt")).unwrap();
        index.write().unwrap();
        let tree_oid = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_oid).unwrap();
        repo.commit(Some("HEAD"), &sig, &sig, "init", &tree, &[])
            .unwrap();

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        // Verify artifacts exist.
        assert!(dir.path().join(".git/ralph/no_agent_commit").exists());
        assert!(dir.path().join(".git/ralph/git-wrapper-dir.txt").exists());
        assert!(dir.path().join(".git/ralph/head-oid.txt").exists());

        // Perform finalize-style cleanup.
        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();

        // Assert all artifacts are gone.
        assert!(
            !dir.path().join(".git/ralph/no_agent_commit").exists(),
            "marker must be removed"
        );
        assert!(
            !dir.path().join(".git/ralph/git-wrapper-dir.txt").exists(),
            "track file must be removed"
        );
        assert!(
            !dir.path().join(".git/ralph/head-oid.txt").exists(),
            "head-oid.txt must be removed"
        );

        // Hooks must not contain HOOK_MARKER.
        let hooks_dir = get_hooks_dir().unwrap();
        for &hook_name in RALPH_HOOK_NAMES {
            let hook_path = hooks_dir.join(hook_name);
            if hook_path.exists() {
                let content = fs::read_to_string(&hook_path).unwrap();
                assert!(
                    !content.contains(HOOK_MARKER),
                    "{hook_name} must not contain HOOK_MARKER after cleanup"
                );
            }
        }

        // No .ralph.orig files should exist.
        for &hook_name in RALPH_HOOK_NAMES {
            let orig_path = hooks_dir.join(format!("{hook_name}.ralph.orig"));
            assert!(
                !orig_path.exists(),
                "{hook_name}.ralph.orig should not exist after cleanup"
            );
        }

        // verify_hooks_removed should confirm cleanup.
        let remaining = verify_hooks_removed(dir.path()).unwrap();
        assert!(remaining.is_empty(), "got: {remaining:?}");
    });
}

#[cfg(unix)]
#[test]
#[serial]
fn test_commit_msg_hook_installed_and_blocks() {
    // commit-msg hook provides a second blocking layer that fires even if
    // pre-commit is somehow bypassed.
    use test_helpers::with_temp_cwd;

    if !program_exists("bash") {
        return;
    }

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        hooks::install_hooks().unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        let hook_path = hooks_dir.join("commit-msg");
        assert!(hook_path.exists(), "commit-msg hook must be installed");

        let content = fs::read_to_string(&hook_path).unwrap();
        assert!(
            content.contains(HOOK_MARKER),
            "commit-msg hook must contain HOOK_MARKER"
        );

        // Create the marker file to activate blocking.
        let marker = dir.path().join(".git/ralph/no_agent_commit");
        File::create(&marker).unwrap();

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
            "commit-msg hook should exit non-zero when .no_agent_commit is present; output: {combined}"
        );
        assert!(
            combined.to_lowercase().contains("blocked"),
            "commit-msg hook output should mention 'blocked'; got: {combined}"
        );
    });
}

#[test]
#[serial]
fn test_commit_msg_hook_cleaned_up_on_exit() {
    // commit-msg hook must be removed during cleanup, just like other hooks.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|_dir| {
        init_repo_guarded(".");

        let mut helpers = GitHelpers::default();
        start_agent_phase(&mut helpers).unwrap();

        let hooks_dir = get_hooks_dir().unwrap();
        assert!(
            hooks_dir.join("commit-msg").exists(),
            "commit-msg hook must exist after start_agent_phase"
        );

        end_agent_phase();
        disable_git_wrapper(&mut helpers);
        uninstall_hooks(&logger).unwrap();

        assert!(
            !hooks_dir.join("commit-msg").exists(),
            "commit-msg hook must be removed after cleanup"
        );
    });
}

#[test]
#[serial]
fn test_verify_hooks_removed_detects_remaining() {
    // verify_hooks_removed should detect hooks that remain after a partial cleanup.
    use test_helpers::with_temp_cwd;

    let logger = Logger::new(ralph_workflow::logger::Colors::with_enabled(false));

    with_temp_cwd(|dir| {
        init_repo_guarded(".");

        hooks::install_hooks().unwrap();

        // Hooks are installed — verify_hooks_removed should detect them.
        let remaining = verify_hooks_removed(dir.path()).unwrap();
        assert_eq!(
            remaining.len(),
            RALPH_HOOK_NAMES.len(),
            "all hooks should be detected as remaining; got: {remaining:?}"
        );

        // Uninstall hooks.
        uninstall_hooks(&logger).unwrap();

        // Now verify_hooks_removed should return empty.
        let remaining = verify_hooks_removed(dir.path()).unwrap();
        assert!(remaining.is_empty(), "got: {remaining:?}");
    });
}

#[test]
#[serial]
fn test_verify_hooks_removed_errors_when_repo_missing() {
    let dir = tempfile::tempdir().unwrap();
    let err = verify_hooks_removed(dir.path()).unwrap_err();
    assert!(
        matches!(
            err.kind(),
            std::io::ErrorKind::NotFound | std::io::ErrorKind::Other
        ),
        "unexpected error kind: {err:?}"
    );
}
