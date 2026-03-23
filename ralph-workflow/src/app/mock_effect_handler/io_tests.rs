//! Unit tests for `MockAppEffectHandler`.
//!
//! These tests verify the mock handler's behavior for different effect types
//! and builder methods.

use super::super::effect::{
    AppEffect, AppEffectHandler, AppEffectResult, CommitResult, RebaseResult,
};
use super::core::MockAppEffectHandler;
use std::path::PathBuf;

#[test]
fn test_mock_captures_effects() {
    let mut handler = MockAppEffectHandler::new();

    handler.execute(AppEffect::GitRequireRepo);
    handler.execute(AppEffect::PathExists {
        path: PathBuf::from("test.txt"),
    });

    let captured = handler.captured();
    assert_eq!(captured.len(), 2);
    assert!(handler.was_executed(&AppEffect::GitRequireRepo));
}

#[test]
fn test_mock_filesystem_write_and_read() {
    let mut handler = MockAppEffectHandler::new();

    let write_result = handler.execute(AppEffect::WriteFile {
        path: PathBuf::from("test.txt"),
        content: "hello world".to_string(),
    });
    assert!(matches!(write_result, AppEffectResult::Ok));

    let read_result = handler.execute(AppEffect::ReadFile {
        path: PathBuf::from("test.txt"),
    });
    assert!(matches!(read_result, AppEffectResult::String(ref s) if s == "hello world"));

    assert!(handler.file_exists(&PathBuf::from("test.txt")));
    assert_eq!(
        handler.get_file(&PathBuf::from("test.txt")),
        Some("hello world".to_string())
    );
}

#[test]
fn test_mock_filesystem_read_not_found() {
    let mut handler = MockAppEffectHandler::new();

    let result = handler.execute(AppEffect::ReadFile {
        path: PathBuf::from("nonexistent.txt"),
    });
    assert!(matches!(result, AppEffectResult::Error(_)));
}

#[test]
fn test_builder_with_file() {
    let handler = MockAppEffectHandler::new()
        .with_file("config.toml", "key = value")
        .with_file(".agent/start_commit", "abc1234");

    assert!(handler.file_exists(&PathBuf::from("config.toml")));
    assert_eq!(
        handler.get_file(&PathBuf::from("config.toml")),
        Some("key = value".to_string())
    );
}

#[test]
fn test_builder_on_main_branch() {
    let mut handler = MockAppEffectHandler::new().on_main_branch();

    let result = handler.execute(AppEffect::GitIsMainBranch);
    assert!(matches!(result, AppEffectResult::Bool(true)));
}

#[test]
fn test_builder_with_head_oid() {
    let mut handler = MockAppEffectHandler::new().with_head_oid("deadbeef");

    let result = handler.execute(AppEffect::GitGetHeadOid);
    assert!(matches!(result, AppEffectResult::String(ref s) if s == "deadbeef"));
}

#[test]
fn test_builder_without_repo() {
    let mut handler = MockAppEffectHandler::new().without_repo();

    let result = handler.execute(AppEffect::GitRequireRepo);
    assert!(matches!(result, AppEffectResult::Error(_)));

    let result = handler.execute(AppEffect::GitGetRepoRoot);
    assert!(matches!(result, AppEffectResult::Error(_)));
}

#[test]
fn test_set_current_dir() {
    let mut handler = MockAppEffectHandler::new();

    handler.execute(AppEffect::SetCurrentDir {
        path: PathBuf::from("/new/path"),
    });

    assert_eq!(handler.get_cwd(), PathBuf::from("/new/path"));
}

#[test]
fn test_git_save_start_commit() {
    let mut handler = MockAppEffectHandler::new().with_head_oid("abc1234");

    let result = handler.execute(AppEffect::GitSaveStartCommit);
    assert!(matches!(result, AppEffectResult::String(ref s) if s == "abc1234"));

    assert_eq!(
        handler.get_file(&PathBuf::from(".agent/start_commit")),
        Some("abc1234".to_string())
    );
}

#[test]
fn test_git_reset_start_commit() {
    let mut handler = MockAppEffectHandler::new().with_head_oid("def5678");

    let result = handler.execute(AppEffect::GitResetStartCommit);
    assert!(matches!(result, AppEffectResult::String(ref s) if s == "def5678"));

    assert_eq!(
        handler.get_file(&PathBuf::from(".agent/start_commit")),
        Some("def5678".to_string())
    );
}

#[test]
fn test_env_var_operations() {
    let mut handler = MockAppEffectHandler::new().with_env_var("PATH", "/usr/bin");

    let result = handler.execute(AppEffect::GetEnvVar {
        name: "PATH".to_string(),
    });
    assert!(matches!(result, AppEffectResult::String(ref s) if s == "/usr/bin"));

    handler.execute(AppEffect::SetEnvVar {
        name: "NEW_VAR".to_string(),
        value: "new_value".to_string(),
    });

    let result = handler.execute(AppEffect::GetEnvVar {
        name: "NEW_VAR".to_string(),
    });
    assert!(matches!(result, AppEffectResult::String(ref s) if s == "new_value"));
}

#[test]
fn test_env_var_not_set() {
    let mut handler = MockAppEffectHandler::new();

    let result = handler.execute(AppEffect::GetEnvVar {
        name: "NONEXISTENT".to_string(),
    });
    assert!(matches!(result, AppEffectResult::Error(_)));
}

#[test]
fn test_logging_effects() {
    let mut handler = MockAppEffectHandler::new();

    handler.execute(AppEffect::LogInfo {
        message: "info message".to_string(),
    });
    handler.execute(AppEffect::LogWarn {
        message: "warning".to_string(),
    });
    handler.execute(AppEffect::LogError {
        message: "error".to_string(),
    });

    let logs = handler.get_log_messages();
    assert_eq!(logs.len(), 3);
    assert_eq!(logs[0], ("info".to_string(), "info message".to_string()));
    assert_eq!(logs[1], ("warn".to_string(), "warning".to_string()));
    assert_eq!(logs[2], ("error".to_string(), "error".to_string()));
}

#[test]
fn test_git_commit_with_changes() {
    let mut handler = MockAppEffectHandler::new()
        .with_head_oid("commit123")
        .with_staged_changes(true);

    let result = handler.execute(AppEffect::GitCommit {
        message: "test commit".to_string(),
        user_name: None,
        user_email: None,
    });

    assert!(matches!(
        result,
        AppEffectResult::Commit(CommitResult::Success(ref oid)) if oid == "commit123"
    ));
}

#[test]
fn test_git_commit_no_changes() {
    let mut handler = MockAppEffectHandler::new().with_staged_changes(false);

    let result = handler.execute(AppEffect::GitCommit {
        message: "test commit".to_string(),
        user_name: None,
        user_email: None,
    });

    assert!(matches!(
        result,
        AppEffectResult::Commit(CommitResult::NoChanges)
    ));
}

#[test]
fn test_rebase_result() {
    let mut handler =
        MockAppEffectHandler::new().with_rebase_result(RebaseResult::Conflicts(vec![
            "file1.rs".to_string(),
            "file2.rs".to_string(),
        ]));

    let result = handler.execute(AppEffect::GitRebaseOnto {
        upstream_branch: "main".to_string(),
    });

    assert!(matches!(
        result,
        AppEffectResult::Rebase(RebaseResult::Conflicts(ref files))
            if files.len() == 2
    ));
}

#[test]
fn test_delete_file() {
    let mut handler = MockAppEffectHandler::new().with_file("to_delete.txt", "content");

    assert!(handler.file_exists(&PathBuf::from("to_delete.txt")));

    let result = handler.execute(AppEffect::DeleteFile {
        path: PathBuf::from("to_delete.txt"),
    });
    assert!(matches!(result, AppEffectResult::Ok));

    assert!(!handler.file_exists(&PathBuf::from("to_delete.txt")));
}

#[test]
fn test_delete_nonexistent_file() {
    let mut handler = MockAppEffectHandler::new();

    let result = handler.execute(AppEffect::DeleteFile {
        path: PathBuf::from("nonexistent.txt"),
    });
    assert!(matches!(result, AppEffectResult::Error(_)));
}

#[test]
fn test_clear_captured() {
    let mut handler = MockAppEffectHandler::new();

    handler.execute(AppEffect::GitRequireRepo);
    assert_eq!(handler.effect_count(), 1);

    handler.clear_captured();
    assert_eq!(handler.effect_count(), 0);
    assert!(handler.captured().is_empty());
}

#[test]
fn test_git_diff_with_configured_output() {
    let diff_content = "diff --git a/file.rs b/file.rs\n+added line";
    let mut handler = MockAppEffectHandler::new().with_diff(diff_content);

    let result = handler.execute(AppEffect::GitDiff);
    assert!(matches!(result, AppEffectResult::String(ref s) if s == diff_content));
}

#[test]
fn test_default_branch() {
    let mut handler = MockAppEffectHandler::new().with_default_branch("develop");

    let result = handler.execute(AppEffect::GitGetDefaultBranch);
    assert!(matches!(result, AppEffectResult::String(ref s) if s == "develop"));
}

#[test]
fn test_conflicted_files() {
    let mut handler = MockAppEffectHandler::new()
        .with_conflicted_files(vec!["conflict1.rs".to_string(), "conflict2.rs".to_string()]);

    let result = handler.execute(AppEffect::GitGetConflictedFiles);
    assert!(matches!(result, AppEffectResult::StringList(ref files) if files.len() == 2));
}

#[test]
fn test_path_exists() {
    let mut handler = MockAppEffectHandler::new().with_file("exists.txt", "content");

    let result = handler.execute(AppEffect::PathExists {
        path: PathBuf::from("exists.txt"),
    });
    assert!(matches!(result, AppEffectResult::Bool(true)));

    let result = handler.execute(AppEffect::PathExists {
        path: PathBuf::from("not_exists.txt"),
    });
    assert!(matches!(result, AppEffectResult::Bool(false)));
}
