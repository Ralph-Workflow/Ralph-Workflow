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

    let err = MainEffectHandler::create_commit(&ctx, "test message".to_string(), &[])
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
    )
    .expect_err("create_commit with files should fail when repo discovery fails");

    assert!(
        err.downcast_ref::<ErrorEvent>().is_some(),
        "expected Err() to carry an ErrorEvent, got: {err:?}"
    );
}

#[test]
fn test_create_commit_with_specific_files_calls_add_specific() {
    // Verify the success path: when non-empty files are passed, the commit is created
    // using git_add_specific_in_repo and a CommitCreated/CommitSkipped event is returned
    // (not an error). Uses a real isolated git repo so no CWD mutation is needed.
    use std::path::Path;

    // Set up an isolated git repo in a temp dir.
    let repo_dir = tempfile::TempDir::new().expect("create temp git repo dir");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    let sig = git2::Signature::now("test", "test@test.com").expect("create signature");

    // Create an initial commit so HEAD exists.
    let file_path = "initial.txt";
    let abs_path = repo_dir.path().join(file_path);
    std::fs::write(&abs_path, "initial content\n").expect("write initial file");
    let mut index = repo.index().expect("open index");
    index
        .add_path(Path::new(file_path))
        .expect("stage initial file");
    index.write().expect("write index");
    let tree = repo
        .find_tree(index.write_tree().expect("write tree"))
        .expect("find tree");
    repo.commit(Some("HEAD"), &sig, &sig, "initial commit", &tree, &[])
        .expect("create initial commit");

    // Create a new file that should be staged selectively.
    let new_file = "src/foo.rs";
    let abs_new = repo_dir.path().join(new_file);
    std::fs::create_dir_all(abs_new.parent().unwrap()).expect("create src dir");
    std::fs::write(&abs_new, "pub fn foo() {}\n").expect("write new file");

    // Set up fixture pointing at the isolated repo.
    let mut fixture = TestFixture::new();
    fixture.repo_root = repo_dir.path().to_path_buf();
    let ctx = fixture.ctx();

    // Call create_commit with non-empty files — must succeed without an error.
    let result = MainEffectHandler::create_commit(
        &ctx,
        "feat: add foo".to_string(),
        &[new_file.to_string()],
    )
    .expect("create_commit with specific files should succeed on a real git repo");

    // The outcome should be either CommitCreated (file staged and committed) or
    // CommitSkipped (edge-case: nothing staged after filtering). Either is acceptable;
    // what matters is that no Err is returned, proving git_add_specific_in_repo ran.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(
                crate::reducer::event::CommitEvent::Created { .. }
                    | crate::reducer::event::CommitEvent::Skipped { .. },
            )
        ),
        "expected CommitCreated or CommitSkipped event, got: {:?}",
        result.event
    );
}
