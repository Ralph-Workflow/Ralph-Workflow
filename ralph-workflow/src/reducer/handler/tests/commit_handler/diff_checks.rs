use super::super::common::TestFixture;
use crate::reducer::event::PipelineEvent;
use crate::reducer::handler::MainEffectHandler;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::PathBuf;

#[test]
fn test_check_commit_diff_emits_prepared_event() {
    use crate::reducer::prompt_inputs::sha256_hex_str;

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let result = MainEffectHandler::check_commit_diff_with_content(&ctx, "")
        .expect("check_commit_diff_with_content should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::DiffPrepared {
            empty: true,
            content_id_sha256,
        }) if content_id_sha256 == sha256_hex_str("")
    ));
}

#[test]
fn test_check_commit_diff_emits_failed_event_on_error() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let result =
        MainEffectHandler::check_commit_diff_with_result(&ctx, Err(anyhow::anyhow!("diff failed")))
            .expect("check_commit_diff_with_result should succeed");

    // New behavior: diff failure uses fallback instructions instead of DiffFailed event
    // The event should be DiffPrepared with fallback content
    assert!(matches!(
        result.event,
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::DiffPrepared { .. })
    ));
}

#[test]
fn test_check_commit_diff_discovers_repo_from_ctx_repo_root_not_process_cwd() {
    use std::path::Path;

    struct RestoreCwd {
        original: PathBuf,
    }
    impl Drop for RestoreCwd {
        fn drop(&mut self) {
            let _ = std::env::set_current_dir(&self.original);
        }
    }

    let mut fixture = TestFixture::new();
    fixture.repo_root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());

    let _restore = RestoreCwd {
        original: std::env::current_dir().unwrap(),
    };
    std::env::set_current_dir(std::env::temp_dir()).unwrap();

    let ctx = fixture.ctx();

    let _result = MainEffectHandler::check_commit_diff(&ctx)
        .expect("check_commit_diff should succeed when repo_root is set");

    let diff = fixture
        .workspace
        .read(Path::new(".agent/tmp/commit_diff.txt"))
        .expect("expected commit diff file to be written");
    assert!(
        !diff.starts_with("## DIFF UNAVAILABLE - INVESTIGATION REQUIRED"),
        "Diff should be computed from ctx.repo_root even when process CWD is elsewhere"
    );
}

#[test]
fn test_check_commit_diff_uses_head_baseline_not_start_commit() {
    // TDD regression: check_commit_diff must generate its diff from HEAD (working-tree
    // vs last commit), NOT from .agent/start_commit (pipeline-start baseline).
    //
    // Proof strategy:
    //   Commit A: initial commit (baseline)
    //   Commit B: committed change (already in history — must NOT appear in diff)
    //   Change C: uncommitted modification with a unique marker (MUST appear in diff)
    //
    // If HEAD baseline is used: diff shows only C.
    // If start_commit baseline is used: diff shows both B and C.
    //
    // IMPORTANT: Use an isolated tempdir repo; never mutate process CWD (test parallelism).
    use std::path::Path;

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    let sig = git2::Signature::now("test", "test@test.com").expect("signature");

    // Commit A: initial state — create two separate tracked files.
    // file_committed will hold the "already committed" change (commit B).
    // file_working will hold the uncommitted working-tree change (C).
    let file_committed = "committed_change_file.txt";
    let file_working = "working_change_file.txt";
    let abs_committed = repo_dir.path().join(file_committed);
    let abs_working = repo_dir.path().join(file_working);
    std::fs::write(&abs_committed, "base content\n").expect("write committed file A");
    std::fs::write(&abs_working, "base content\n").expect("write working file A");
    let mut index = repo.index().expect("open index");
    index
        .add_path(Path::new(file_committed))
        .expect("stage committed file A");
    index
        .add_path(Path::new(file_working))
        .expect("stage working file A");
    index.write().expect("write index A");
    let tree_a = repo
        .find_tree(index.write_tree().expect("write tree A"))
        .expect("find tree A");
    repo.commit(Some("HEAD"), &sig, &sig, "commit A: initial", &tree_a, &[])
        .expect("create commit A");

    // Commit B: modify file_committed only — this becomes part of history.
    // With HEAD baseline, file_committed has NO working-tree changes (HEAD == workdir).
    // With start_commit baseline, file_committed would show committed_marker as added.
    let committed_marker = "COMMITTED_CHANGE_MUST_NOT_APPEAR_IN_DIFF";
    std::fs::write(
        &abs_committed,
        format!("base content\n{committed_marker}\n"),
    )
    .expect("write committed file for commit B");
    let mut index = repo.index().expect("open index for B");
    index
        .add_path(Path::new(file_committed))
        .expect("stage committed file B");
    index.write().expect("write index B");
    let tree_b = repo
        .find_tree(index.write_tree().expect("write tree B"))
        .expect("find tree B");
    let parent_a = repo
        .head()
        .expect("head after A")
        .peel_to_commit()
        .expect("commit A");
    repo.commit(
        Some("HEAD"),
        &sig,
        &sig,
        "commit B: committed change",
        &tree_b,
        &[&parent_a],
    )
    .expect("create commit B");

    // Change C: modify file_working without staging (MUST appear in HEAD diff).
    // file_working is tracked (committed in A) but not changed in B, so HEAD still has base content.
    let uncommitted_marker = "UNCOMMITTED_CHANGE_MUST_APPEAR_IN_DIFF";
    std::fs::write(
        &abs_working,
        format!("base content\n{uncommitted_marker}\n"),
    )
    .expect("write uncommitted change to working file");

    // Set up fixture with isolated repo
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.repo_root = repo_dir.path().to_path_buf();
    let ctx = fixture.ctx();

    MainEffectHandler::check_commit_diff(&ctx)
        .expect("check_commit_diff should succeed with isolated repo");

    let diff = fixture
        .workspace
        .read(Path::new(".agent/tmp/commit_diff.txt"))
        .expect("commit diff file must be written");

    // C (uncommitted) must appear — proves HEAD diff captures working tree changes.
    assert!(
        diff.contains(uncommitted_marker),
        "expected uncommitted change marker in commit diff; got: {diff}"
    );

    // B (committed) must NOT appear — proves HEAD baseline is used, not start_commit.
    assert!(
        !diff.contains(committed_marker),
        "expected already-committed change to be ABSENT from commit diff (HEAD baseline); got: {diff}"
    );
}

#[test]
fn test_fresh_commit_context_after_previous_commit() {
    // Behavioral regression: in a multi-iteration scenario, the second commit context
    // must contain ONLY second-iteration changes, never first-iteration changes.
    //
    // This proves that check_commit_diff uses HEAD as baseline on each call, so a
    // previously committed change (iteration 1) is invisible to the second diff check.
    //
    // IMPORTANT: Uses an isolated tempdir repo; never mutates process CWD (parallel-safe).
    use std::path::Path;

    const ITERATION1_UNIQUE_MARKER: &str =
        "ITERATION1_UNIQUE_MARKER_MUST_NOT_APPEAR_IN_SECOND_DIFF";
    const ITERATION2_UNIQUE_MARKER: &str = "ITERATION2_UNIQUE_MARKER_MUST_APPEAR_IN_SECOND_DIFF";

    let repo_dir = tempfile::TempDir::new().expect("create temp git repo");
    let repo = git2::Repository::init(repo_dir.path()).expect("init git repo");
    let sig = git2::Signature::now("test", "test@test.com").expect("signature");

    // Initial commit: create both tracked files with base content so HEAD exists.
    // Both files must be tracked (committed) so that working-tree modifications appear in the diff.
    let file1 = "iteration1_file.txt";
    let file2 = "iteration2_file.txt";
    let abs_file1 = repo_dir.path().join(file1);
    let abs_file2 = repo_dir.path().join(file2);
    std::fs::write(&abs_file1, "base content\n").expect("write file1 base");
    std::fs::write(&abs_file2, "base content\n").expect("write file2 base");
    let mut index = repo.index().expect("open index");
    index.add_path(Path::new(file1)).expect("stage file1 base");
    index.add_path(Path::new(file2)).expect("stage file2 base");
    index.write().expect("write index");
    let tree_init = repo
        .find_tree(index.write_tree().expect("write tree init"))
        .expect("find tree init");
    repo.commit(Some("HEAD"), &sig, &sig, "initial commit", &tree_init, &[])
        .expect("create initial commit");

    // Iteration 1: modify file1 with a unique marker (working tree change of tracked file).
    std::fs::write(
        &abs_file1,
        format!("base content\n{ITERATION1_UNIQUE_MARKER}\n"),
    )
    .expect("write file1 iter1 change");

    // Set up fixture for first check_commit_diff call.
    let workspace1 = crate::workspace::MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture1 = TestFixture::with_workspace(workspace1);
    fixture1.repo_root = repo_dir.path().to_path_buf();
    {
        let ctx = fixture1.ctx();
        MainEffectHandler::check_commit_diff(&ctx).expect("first check_commit_diff should succeed");
    }

    // Verify iteration 1 diff contains ITERATION1_UNIQUE_MARKER (proves file1 is captured).
    let diff1 = fixture1
        .workspace
        .read(Path::new(".agent/tmp/commit_diff.txt"))
        .expect("first diff file must be written");
    assert!(
        diff1.contains(ITERATION1_UNIQUE_MARKER),
        "first diff must contain ITERATION1 marker; got: {diff1}"
    );

    // Now commit file1 into history (simulating end of iteration 1).
    let mut index = repo.index().expect("open index for commit");
    index
        .add_path(Path::new(file1))
        .expect("stage file1 for commit");
    index.write().expect("write index for commit");
    let tree_iter1 = repo
        .find_tree(index.write_tree().expect("write tree iter1"))
        .expect("find tree iter1");
    let parent = repo
        .head()
        .expect("head after initial")
        .peel_to_commit()
        .expect("initial commit object");
    repo.commit(
        Some("HEAD"),
        &sig,
        &sig,
        "iteration 1 commit",
        &tree_iter1,
        &[&parent],
    )
    .expect("create iteration 1 commit");

    // Iteration 2: modify file2 (already tracked) with a different unique marker.
    std::fs::write(
        &abs_file2,
        format!("base content\n{ITERATION2_UNIQUE_MARKER}\n"),
    )
    .expect("write file2 iter2 change");

    // Set up fixture for second check_commit_diff call (fresh workspace).
    let workspace2 = crate::workspace::MemoryWorkspace::new_test().with_dir(".agent/tmp");
    let mut fixture2 = TestFixture::with_workspace(workspace2);
    fixture2.repo_root = repo_dir.path().to_path_buf();
    {
        let ctx = fixture2.ctx();
        MainEffectHandler::check_commit_diff(&ctx)
            .expect("second check_commit_diff should succeed");
    }

    let diff2 = fixture2
        .workspace
        .read(Path::new(".agent/tmp/commit_diff.txt"))
        .expect("second diff file must be written");

    // The second diff must contain the second iteration's change.
    assert!(
        diff2.contains(ITERATION2_UNIQUE_MARKER),
        "second diff must contain ITERATION2 marker to prove fresh context; got: {diff2}"
    );

    // The second diff must NOT contain the first iteration's change (already committed into HEAD).
    assert!(
        !diff2.contains(ITERATION1_UNIQUE_MARKER),
        "second diff must NOT contain ITERATION1 marker (already committed); got: {diff2}"
    );
}
