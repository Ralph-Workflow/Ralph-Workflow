use super::super::common::TestFixture;
use crate::reducer::event::{ErrorEvent, PipelineEvent};
use crate::reducer::handler::MainEffectHandler;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn test_create_commit_returns_typed_error_event_when_git_add_all_fails() {
    let mut fixture = TestFixture::new();
    // Use a unique, non-existent repo root so git discovery fails deterministically.
    // This avoids mutating process-wide CWD (which would be flaky under parallel test execution).
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    fixture.repo_root = std::env::temp_dir().join(format!("ralph-nonexistent-repo-{unique}"));

    let ctx = fixture.ctx();

    let err = MainEffectHandler::create_commit(&ctx, "test message".to_string(), &[], &[])
        .expect_err("create_commit should fail when repo discovery fails");

    assert!(
        err.downcast_ref::<ErrorEvent>().is_some(),
        "expected Err() to carry an ErrorEvent, got: {err:?}"
    );
}

#[test]
fn test_create_commit_with_files_returns_typed_error_when_git_add_specific_fails() {
    let mut fixture = TestFixture::new();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    fixture.repo_root = std::env::temp_dir().join(format!("ralph-nonexistent-repo-{unique}"));

    let ctx = fixture.ctx();

    let err = MainEffectHandler::create_commit(
        &ctx,
        "test message".to_string(),
        &["src/lib.rs".to_string()],
        &[],
    )
    .expect_err("create_commit with files should fail when repo discovery fails");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("expected Err() to carry an ErrorEvent");
    assert!(
        matches!(error_event, ErrorEvent::GitAddSpecificFailed { .. }),
        "expected GitAddSpecificFailed, got: {error_event:?}"
    );
}

#[test]
fn test_create_commit_with_specific_files_creates_commit_with_only_selected_files() {
    // Verify strict selective staging: passing a file list must commit ONLY those files,
    // even if other changes were already staged.
    use std::path::Path;

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo dir");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    let sig = git2::Signature::now("test", "test@test.com").expect("create signature");

    // Initial commit so HEAD exists.
    let initial_path = "initial.txt";
    std::fs::write(repo_dir.path().join(initial_path), "initial content\n")
        .expect("write initial file");
    let mut index = repo.index().expect("open index");
    index
        .add_path(Path::new(initial_path))
        .expect("stage initial file");
    index.write().expect("write index");
    let tree = repo
        .find_tree(index.write_tree().expect("write tree"))
        .expect("find tree");
    repo.commit(Some("HEAD"), &sig, &sig, "initial commit", &tree, &[])
        .expect("create initial commit");

    // Prepare two new files; pre-stage one of them (simulates pre-existing staged work).
    let selected = "src/foo.rs";
    let pre_staged = "src/bar.rs";
    let abs_selected = repo_dir.path().join(selected);
    let abs_pre_staged = repo_dir.path().join(pre_staged);
    std::fs::create_dir_all(abs_selected.parent().unwrap()).expect("create src dir");
    std::fs::write(&abs_selected, "pub fn foo() {}\n").expect("write selected file");
    std::fs::write(&abs_pre_staged, "pub fn bar() {}\n").expect("write pre-staged file");

    let mut index = repo.index().expect("open index");
    index
        .add_path(Path::new(pre_staged))
        .expect("stage pre-staged file");
    index.write().expect("write index");

    // Run create_commit with ONLY the selected file.
    let mut fixture = TestFixture::new();
    fixture.repo_root = repo_dir.path().to_path_buf();
    let ctx = fixture.ctx();

    let result = MainEffectHandler::create_commit(
        &ctx,
        "feat: add foo".to_string(),
        &[selected.to_string()],
        &[],
    )
    .expect("create_commit with specific files should succeed");

    let created_hash = match result.event {
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::Created { hash, .. }) => hash,
        other => panic!("expected CommitEvent::Created, got: {other:?}"),
    };

    // Verify the commit contains the selected path and excludes the pre-staged path.
    let commit_oid = git2::Oid::from_str(&created_hash).expect("parse created commit hash");
    let commit = repo.find_commit(commit_oid).expect("find created commit");
    let tree = commit.tree().expect("get commit tree");
    assert!(
        tree.get_path(Path::new(selected)).is_ok(),
        "expected selected file to be present in commit"
    );
    assert!(
        tree.get_path(Path::new(pre_staged)).is_err(),
        "expected non-selected (pre-staged) file to be absent from commit"
    );
}

#[test]
fn test_create_commit_with_specific_files_fails_on_invalid_path() {
    // If the agent provides a file path that cannot be staged as add or remove, staging must fail.
    use std::path::Path;

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo dir");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    let sig = git2::Signature::now("test", "test@test.com").expect("create signature");

    // Initial commit so HEAD exists.
    let initial_path = "initial.txt";
    std::fs::write(repo_dir.path().join(initial_path), "initial content\n")
        .expect("write initial file");
    let mut index = repo.index().expect("open index");
    index
        .add_path(Path::new(initial_path))
        .expect("stage initial file");
    index.write().expect("write index");
    let tree = repo
        .find_tree(index.write_tree().expect("write tree"))
        .expect("find tree");
    repo.commit(Some("HEAD"), &sig, &sig, "initial commit", &tree, &[])
        .expect("create initial commit");

    let mut fixture = TestFixture::new();
    fixture.repo_root = repo_dir.path().to_path_buf();
    let ctx = fixture.ctx();

    let err = MainEffectHandler::create_commit(
        &ctx,
        "feat: add foo".to_string(),
        &["does/not/exist.txt".to_string()],
        &[],
    )
    .expect_err("expected create_commit to fail when staging an invalid path");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("expected Err() to carry an ErrorEvent");
    assert!(
        matches!(error_event, ErrorEvent::GitAddSpecificFailed { .. }),
        "expected GitAddSpecificFailed, got: {error_event:?}"
    );
}
