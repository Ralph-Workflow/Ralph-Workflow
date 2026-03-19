use super::*;
use crate::app::mock_effect_handler::MockAppEffectHandler;

#[test]
fn test_reset_start_commit_emits_correct_effects() {
    let mut handler = MockAppEffectHandler::new();

    let result = handle_reset_start_commit(&mut handler, None);

    assert!(result.is_ok());
    let captured = handler.captured();
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::GitRequireRepo)),
        "should emit GitRequireRepo"
    );
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::GitGetRepoRoot)),
        "should emit GitGetRepoRoot"
    );
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::GitResetStartCommit)),
        "should emit GitResetStartCommit"
    );
}

#[test]
fn test_reset_start_commit_with_working_dir() {
    let mut handler = MockAppEffectHandler::new();
    let dir = PathBuf::from("/test/dir");

    let result = handle_reset_start_commit(&mut handler, Some(&dir));

    assert!(result.is_ok());
    let captured = handler.captured();
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::SetCurrentDir { path } if path == &dir)),
        "should emit SetCurrentDir with the override path"
    );
}

#[test]
fn test_reset_start_commit_fails_without_repo() {
    let mut handler = MockAppEffectHandler::new().without_repo();

    let result = handle_reset_start_commit(&mut handler, None);

    assert!(result.is_err());
    assert!(result.unwrap_err().contains("git repository"));
}

#[test]
fn test_save_start_commit_returns_oid() {
    let expected_oid = "abc123def456";
    let mut handler = MockAppEffectHandler::new().with_head_oid(expected_oid);

    let result = save_start_commit(&mut handler);

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), expected_oid);
}

#[test]
fn test_is_on_main_branch_true() {
    let mut handler = MockAppEffectHandler::new().on_main_branch();

    let result = is_on_main_branch(&mut handler);

    assert!(result.is_ok());
    assert!(result.unwrap());
}

#[test]
fn test_is_on_main_branch_false() {
    let mut handler = MockAppEffectHandler::new(); // default is not on main

    let result = is_on_main_branch(&mut handler);

    assert!(result.is_ok());
    assert!(!result.unwrap());
}

#[test]
fn test_get_head_oid() {
    let expected = "1234567890abcdef1234567890abcdef12345678";
    let mut handler = MockAppEffectHandler::new().with_head_oid(expected);

    let result = get_head_oid(&mut handler);

    assert!(result.is_ok());
    assert_eq!(result.unwrap(), expected);
}

#[test]
fn test_require_repo_success() {
    let mut handler = MockAppEffectHandler::new();

    let result = require_repo(&mut handler);

    assert!(result.is_ok());
}

#[test]
fn test_require_repo_failure() {
    let mut handler = MockAppEffectHandler::new().without_repo();

    let result = require_repo(&mut handler);

    assert!(result.is_err());
}

#[test]
fn test_get_repo_root() {
    let mut handler = MockAppEffectHandler::new();

    let result = get_repo_root(&mut handler);

    assert!(result.is_ok());
    // Default mock CWD is "/"
    assert_eq!(result.unwrap(), PathBuf::from("/"));
}

#[test]
fn test_ensure_files_creates_directories() {
    let mut handler = MockAppEffectHandler::new();

    let result = ensure_files_effectful(&mut handler, true);

    assert!(result.is_ok());

    // Verify directories were created via effects
    let captured = handler.captured();
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::CreateDir { path } if path.ends_with(".agent/logs"))),
        "should create .agent/logs directory"
    );
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::CreateDir { path } if path.ends_with(".agent/tmp"))),
        "should create .agent/tmp directory"
    );
}

#[test]
fn test_ensure_files_writes_xsd_schemas() {
    let mut handler = MockAppEffectHandler::new();

    let result = ensure_files_effectful(&mut handler, true);

    assert!(result.is_ok());

    // Verify XSD schemas were written via effects
    let captured = handler.captured();
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("plan.xsd"))),
        "should write plan.xsd"
    );
    assert!(
        captured.iter().any(
            |e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("issues.xsd"))
        ),
        "should write issues.xsd"
    );
}

#[test]
fn test_ensure_files_non_isolation_creates_context_files() {
    let mut handler = MockAppEffectHandler::new();

    // isolation_mode = false should create STATUS.md, NOTES.md, ISSUES.md
    let result = ensure_files_effectful(&mut handler, false);

    assert!(result.is_ok());

    let captured = handler.captured();
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("STATUS.md"))),
        "should create STATUS.md in non-isolation mode"
    );
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("NOTES.md"))),
        "should create NOTES.md in non-isolation mode"
    );
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("ISSUES.md"))),
        "should create ISSUES.md in non-isolation mode"
    );
}

#[test]
fn test_ensure_files_isolation_skips_context_files() {
    let mut handler = MockAppEffectHandler::new();

    // isolation_mode = true should NOT create STATUS.md, NOTES.md, ISSUES.md
    let result = ensure_files_effectful(&mut handler, true);

    assert!(result.is_ok());

    let captured = handler.captured();
    assert!(
        !captured
            .iter()
            .any(|e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("STATUS.md"))),
        "should NOT create STATUS.md in isolation mode"
    );
    assert!(
        !captured
            .iter()
            .any(|e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("NOTES.md"))),
        "should NOT create NOTES.md in isolation mode"
    );
    assert!(
        !captured
            .iter()
            .any(|e| matches!(e, AppEffect::WriteFile { path, .. } if path.ends_with("ISSUES.md"))),
        "should NOT create ISSUES.md in isolation mode"
    );
}

#[test]
fn test_reset_context_for_isolation_deletes_existing_files() {
    // Files exist - should emit delete effects
    let mut handler = MockAppEffectHandler::new()
        .with_file(".agent/STATUS.md", "old status")
        .with_file(".agent/NOTES.md", "old notes")
        .with_file(".agent/ISSUES.md", "old issues");

    let result = reset_context_for_isolation_effectful(&mut handler);

    assert!(result.is_ok());

    let captured = handler.captured();
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::DeleteFile { path } if path.ends_with("STATUS.md"))),
        "should delete STATUS.md"
    );
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::DeleteFile { path } if path.ends_with("NOTES.md"))),
        "should delete NOTES.md"
    );
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::DeleteFile { path } if path.ends_with("ISSUES.md"))),
        "should delete ISSUES.md"
    );
}

#[test]
fn test_reset_context_for_isolation_skips_nonexistent_files() {
    // No files exist - should check PathExists but not emit DeleteFile
    let mut handler = MockAppEffectHandler::new();

    let result = reset_context_for_isolation_effectful(&mut handler);

    assert!(result.is_ok());

    let captured = handler.captured();
    // Should check if files exist
    assert!(
        captured
            .iter()
            .any(|e| matches!(e, AppEffect::PathExists { path } if path.ends_with("STATUS.md"))),
        "should check if STATUS.md exists"
    );
    // Should NOT try to delete non-existent files
    assert!(
        !captured
            .iter()
            .any(|e| matches!(e, AppEffect::DeleteFile { path } if path.ends_with("STATUS.md"))),
        "should NOT delete non-existent STATUS.md"
    );
}

#[test]
fn test_check_prompt_exists_returns_true_when_file_exists() {
    let mut handler = MockAppEffectHandler::new().with_file("PROMPT.md", "# Goal\nTest");

    let result = check_prompt_exists_effectful(&mut handler);

    assert!(result.is_ok());
    assert!(result.unwrap(), "should return true when PROMPT.md exists");
}

#[test]
fn test_check_prompt_exists_returns_false_when_file_missing() {
    let mut handler = MockAppEffectHandler::new();

    let result = check_prompt_exists_effectful(&mut handler);

    assert!(result.is_ok());
    assert!(
        !result.unwrap(),
        "should return false when PROMPT.md doesn't exist"
    );
}
