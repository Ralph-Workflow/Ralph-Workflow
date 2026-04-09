use super::super::common::TestFixture;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{CommitEvent, PipelineEvent};
use git2::Repository;
use std::fs;
use tempfile::tempdir;

#[test]
fn check_residual_files_reports_clean_working_tree() {
    // GUARD: capture project HEAD OID before any git operations
    let project_head_before = test_helpers::capture_project_head_oid();

    let repo_dir = tempdir().expect("create repo tempdir");
    let repo = Repository::init(repo_dir.path()).expect("init repo");
    test_helpers::assert_repo_is_isolated(&repo);

    let mut fixture = TestFixture::new();
    fixture.repo_root = repo_dir.path().to_path_buf();
    let ctx = fixture.ctx();

    let result = MainEffectHandler::check_residual_files(&ctx, 1)
        .expect("check residual files should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(CommitEvent::ResidualFilesNone)
    ));
    assert!(
        result.additional_events.is_empty(),
        "no extra events expected"
    );

    // GUARD: verify project git state unchanged
    test_helpers::assert_project_head_unchanged(&project_head_before);
}

#[test]
fn check_residual_files_detects_untracked_files() {
    // GUARD: capture project HEAD OID before any git operations
    let project_head_before = test_helpers::capture_project_head_oid();

    let repo_dir = tempdir().expect("create repo tempdir");
    let repo = Repository::init(repo_dir.path()).expect("init repo");
    test_helpers::assert_repo_is_isolated(&repo);
    let leftover = repo_dir.path().join("leftover.rs");
    fs::write(&leftover, "todo").expect("write leftover file");

    let mut fixture = TestFixture::new();
    fixture.repo_root = repo_dir.path().to_path_buf();
    let ctx = fixture.ctx();

    let result = MainEffectHandler::check_residual_files(&ctx, 2)
        .expect("check residual files should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(CommitEvent::ResidualFilesFound { files, pass })
            if pass == 2 && files == vec!["leftover.rs".to_string()]
    ));

    // GUARD: verify project git state unchanged
    test_helpers::assert_project_head_unchanged(&project_head_before);
}
