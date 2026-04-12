//! System tests for rebase git operations requiring real git repositories.
//!
//! These tests use `init_git_repo` (libgit2) and therefore belong in the
//! serial system-test binary. They verify that the rebase wrapper functions
//! return correct Result types when interacting with real git repos.

use ralph_workflow::git_helpers::{
    cleanup_stale_rebase_state, get_conflicted_files, is_dirty_tree_cli, rebase_in_progress_cli,
    rebase_onto,
};
use serial_test::serial;
use std::sync::Arc;
use test_helpers::{
    assert_project_head_unchanged, capture_project_head_oid, commit_all, init_git_repo,
    with_temp_cwd, write_file,
};

#[test]
#[serial]
fn test_rebase_onto_returns_result() {
    use ralph_workflow::executor::MockProcessExecutor;

    let head_before = capture_project_head_oid();
    with_temp_cwd(|dir| {
        let repo = init_git_repo(dir);
        write_file(dir.path().join("initial.txt"), "initial content");
        let _ = commit_all(&repo, "initial commit");

        let executor = Arc::new(MockProcessExecutor::new())
            as Arc<dyn ralph_workflow::executor::ProcessExecutor>;
        let result = rebase_onto("nonexistent_branch_that_does_not_exist", executor.as_ref());
        assert!(result.is_ok());
        assert_project_head_unchanged(&head_before);
    });
}

#[test]
#[serial]
fn test_get_conflicted_files_returns_result() {
    let head_before = capture_project_head_oid();
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let result = get_conflicted_files();
        assert!(result.is_ok());
        assert_project_head_unchanged(&head_before);
    });
}

#[test]
#[serial]
fn test_rebase_in_progress_cli_returns_result() {
    use ralph_workflow::executor::MockProcessExecutor;

    let head_before = capture_project_head_oid();
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let executor = Arc::new(MockProcessExecutor::new())
            as Arc<dyn ralph_workflow::executor::ProcessExecutor>;
        let result = rebase_in_progress_cli(executor.as_ref());
        assert!(result.is_ok());
        assert_project_head_unchanged(&head_before);
    });
}

#[test]
#[serial]
fn test_is_dirty_tree_cli_returns_result() {
    use ralph_workflow::executor::MockProcessExecutor;

    let head_before = capture_project_head_oid();
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let executor = Arc::new(MockProcessExecutor::new())
            as Arc<dyn ralph_workflow::executor::ProcessExecutor>;
        let result = is_dirty_tree_cli(executor.as_ref());
        assert!(result.is_ok());
        assert_project_head_unchanged(&head_before);
    });
}

#[test]
#[serial]
fn test_cleanup_stale_rebase_state_returns_result() {
    let head_before = capture_project_head_oid();
    with_temp_cwd(|dir| {
        let _repo = init_git_repo(dir);

        let result = cleanup_stale_rebase_state();
        assert!(result.is_ok());
        assert_project_head_unchanged(&head_before);
    });
}
