//! Real implementation of `AppEffectHandler`.
//!
//! This handler executes actual side effects for production use.
//! It provides concrete implementations for all [`AppEffect`] variants
//! by delegating to the appropriate system calls or internal modules.

use crate::app::effect::{AppEffect, AppEffectHandler, AppEffectResult, CommitResult};
use std::path::{Path, PathBuf};

pub struct RealAppEffectHandler {
    workspace_root: Option<PathBuf>,
}

impl RealAppEffectHandler {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            workspace_root: None,
        }
    }

    #[must_use]
    pub const fn with_workspace_root(root: PathBuf) -> Self {
        Self {
            workspace_root: Some(root),
        }
    }

    fn resolve_path(&self, path: &Path) -> PathBuf {
        crate::app::io::effect_io::resolve_path(&self.workspace_root, path)
    }

    fn execute_set_current_dir(&self, path: &Path) -> AppEffectResult {
        let resolved = self.resolve_path(path);
        match crate::app::io::effect_io::set_current_dir(&resolved) {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!(
                "Failed to set current directory to '{}': {}",
                resolved.display(),
                error
            )),
        }
    }

    fn execute_write_file(&self, path: &Path, content: String) -> AppEffectResult {
        let resolved = self.resolve_path(path);
        match crate::app::io::effect_io::write_file(&resolved, content) {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!(
                "Failed to write file '{}': {}",
                resolved.display(),
                error
            )),
        }
    }

    fn execute_read_file(&self, path: &Path) -> AppEffectResult {
        let resolved = self.resolve_path(path);
        match crate::app::io::effect_io::read_file(&resolved) {
            Ok(content) => AppEffectResult::String(content),
            Err(error) => AppEffectResult::Error(format!(
                "Failed to read file '{}': {}",
                resolved.display(),
                error
            )),
        }
    }

    fn execute_delete_file(&self, path: &Path) -> AppEffectResult {
        let resolved = self.resolve_path(path);
        match crate::app::io::effect_io::delete_file(&resolved) {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!(
                "Failed to delete file '{}': {}",
                resolved.display(),
                error
            )),
        }
    }

    fn execute_create_dir(&self, path: &Path) -> AppEffectResult {
        let resolved = self.resolve_path(path);
        match crate::app::io::effect_io::create_dir(&resolved) {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!(
                "Failed to create directory '{}': {}",
                resolved.display(),
                error
            )),
        }
    }

    fn execute_path_exists(&self, path: &Path) -> AppEffectResult {
        let resolved = self.resolve_path(path);
        AppEffectResult::Bool(crate::app::io::effect_io::path_exists(&resolved))
    }

    fn execute_set_read_only(&self, path: &Path, readonly: bool) -> AppEffectResult {
        let resolved = self.resolve_path(path);
        match crate::app::io::effect_io::set_read_only(&resolved, readonly) {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!(
                "Failed to set permissions on '{}': {}",
                resolved.display(),
                error
            )),
        }
    }

    fn execute_git_require_repo() -> AppEffectResult {
        match crate::git_helpers::require_git_repo() {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!("Not in a git repository: {error}")),
        }
    }

    fn execute_git_get_repo_root() -> AppEffectResult {
        match crate::git_helpers::get_repo_root() {
            Ok(root) => AppEffectResult::Path(root),
            Err(error) => AppEffectResult::Error(format!("Failed to get repository root: {error}")),
        }
    }

    fn execute_git_get_head_oid() -> AppEffectResult {
        match crate::git_helpers::get_current_head_oid() {
            Ok(oid) => AppEffectResult::String(oid),
            Err(error) => AppEffectResult::Error(format!("Failed to get HEAD OID: {error}")),
        }
    }

    fn execute_git_diff() -> AppEffectResult {
        match crate::git_helpers::git_diff() {
            Ok(diff) => AppEffectResult::String(diff),
            Err(error) => AppEffectResult::Error(format!("Failed to get git diff: {error}")),
        }
    }

    fn execute_git_diff_from(start_oid: &str) -> AppEffectResult {
        match crate::git_helpers::git_diff_from(start_oid) {
            Ok(diff) => AppEffectResult::String(diff),
            Err(error) => {
                AppEffectResult::Error(format!("Failed to get diff from '{start_oid}': {error}"))
            }
        }
    }

    fn execute_git_diff_from_start() -> AppEffectResult {
        match crate::git_helpers::get_git_diff_from_start() {
            Ok(diff) => AppEffectResult::String(diff),
            Err(error) => {
                AppEffectResult::Error(format!("Failed to get diff from start commit: {error}"))
            }
        }
    }

    fn execute_git_snapshot() -> AppEffectResult {
        match crate::git_helpers::git_snapshot() {
            Ok(snapshot) => AppEffectResult::String(snapshot),
            Err(error) => AppEffectResult::Error(format!("Failed to create git snapshot: {error}")),
        }
    }

    fn execute_git_add_all() -> AppEffectResult {
        match crate::git_helpers::git_add_all() {
            Ok(staged) => AppEffectResult::Bool(staged),
            Err(error) => AppEffectResult::Error(format!("Failed to stage all changes: {error}")),
        }
    }

    fn execute_git_commit(
        message: &str,
        user_name: Option<&str>,
        user_email: Option<&str>,
    ) -> AppEffectResult {
        match crate::git_helpers::git_commit(message, user_name, user_email, None, None) {
            Ok(Some(oid)) => AppEffectResult::Commit(CommitResult::Success(oid.to_string())),
            Ok(None) => AppEffectResult::Commit(CommitResult::NoChanges),
            Err(error) => AppEffectResult::Error(format!("Failed to create commit: {error}")),
        }
    }

    fn execute_git_save_start_commit() -> AppEffectResult {
        match crate::git_helpers::save_start_commit() {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!("Failed to save start commit: {error}")),
        }
    }

    fn execute_git_reset_start_commit() -> AppEffectResult {
        match crate::git_helpers::reset_start_commit() {
            Ok(result) => AppEffectResult::String(result.oid),
            Err(error) => AppEffectResult::Error(format!("Failed to reset start commit: {error}")),
        }
    }

    fn execute_git_rebase_onto(_upstream_branch: String) -> AppEffectResult {
        AppEffectResult::Error(
            "GitRebaseOnto requires executor injection - use pipeline runner".to_string(),
        )
    }

    fn execute_git_get_conflicted_files() -> AppEffectResult {
        match crate::git_helpers::get_conflicted_files() {
            Ok(files) => AppEffectResult::StringList(files),
            Err(error) => {
                AppEffectResult::Error(format!("Failed to get conflicted files: {error}"))
            }
        }
    }

    fn execute_git_continue_rebase() -> AppEffectResult {
        AppEffectResult::Error(
            "GitContinueRebase requires executor injection - use pipeline runner".to_string(),
        )
    }

    fn execute_git_abort_rebase() -> AppEffectResult {
        AppEffectResult::Error(
            "GitAbortRebase requires executor injection - use pipeline runner".to_string(),
        )
    }

    fn execute_git_get_default_branch() -> AppEffectResult {
        match crate::git_helpers::get_default_branch() {
            Ok(branch) => AppEffectResult::String(branch),
            Err(error) => AppEffectResult::Error(format!("Failed to get default branch: {error}")),
        }
    }

    fn execute_git_is_main_branch() -> AppEffectResult {
        match crate::git_helpers::is_main_or_master_branch() {
            Ok(is_main) => AppEffectResult::Bool(is_main),
            Err(error) => AppEffectResult::Error(format!("Failed to check branch: {error}")),
        }
    }

    fn execute_get_env_var(name: &str) -> AppEffectResult {
        match crate::app::io::effect_io::get_env_var(name) {
            Ok(value) => AppEffectResult::String(value),
            Err(std::env::VarError::NotPresent) => {
                AppEffectResult::Error(format!("Environment variable '{name}' not set"))
            }
            Err(std::env::VarError::NotUnicode(_)) => AppEffectResult::Error(format!(
                "Environment variable '{name}' contains invalid Unicode"
            )),
        }
    }

    fn execute_set_env_var(name: &str, value: &str) -> AppEffectResult {
        crate::app::io::effect_io::set_env_var(name, value);
        AppEffectResult::Ok
    }
}

impl Default for RealAppEffectHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl AppEffectHandler for RealAppEffectHandler {
    fn execute(&mut self, effect: AppEffect) -> AppEffectResult {
        match effect {
            AppEffect::SetCurrentDir { path } => self.execute_set_current_dir(&path),
            AppEffect::WriteFile { path, content } => self.execute_write_file(&path, content),
            AppEffect::ReadFile { path } => self.execute_read_file(&path),
            AppEffect::DeleteFile { path } => self.execute_delete_file(&path),
            AppEffect::CreateDir { path } => self.execute_create_dir(&path),
            AppEffect::PathExists { path } => self.execute_path_exists(&path),
            AppEffect::SetReadOnly { path, readonly } => {
                self.execute_set_read_only(&path, readonly)
            }
            AppEffect::GitRequireRepo => Self::execute_git_require_repo(),
            AppEffect::GitGetRepoRoot => Self::execute_git_get_repo_root(),
            AppEffect::GitGetHeadOid => Self::execute_git_get_head_oid(),
            AppEffect::GitDiff => Self::execute_git_diff(),
            AppEffect::GitDiffFrom { start_oid } => Self::execute_git_diff_from(&start_oid),
            AppEffect::GitDiffFromStart => Self::execute_git_diff_from_start(),
            AppEffect::GitSnapshot => Self::execute_git_snapshot(),
            AppEffect::GitAddAll => Self::execute_git_add_all(),
            AppEffect::GitCommit {
                message,
                user_name,
                user_email,
            } => Self::execute_git_commit(&message, user_name.as_deref(), user_email.as_deref()),
            AppEffect::GitSaveStartCommit => Self::execute_git_save_start_commit(),
            AppEffect::GitResetStartCommit => Self::execute_git_reset_start_commit(),
            AppEffect::GitRebaseOnto { upstream_branch } => {
                Self::execute_git_rebase_onto(upstream_branch)
            }
            AppEffect::GitGetConflictedFiles => Self::execute_git_get_conflicted_files(),
            AppEffect::GitContinueRebase => Self::execute_git_continue_rebase(),
            AppEffect::GitAbortRebase => Self::execute_git_abort_rebase(),
            AppEffect::GitGetDefaultBranch => Self::execute_git_get_default_branch(),
            AppEffect::GitIsMainBranch => Self::execute_git_is_main_branch(),
            AppEffect::GetEnvVar { name } => Self::execute_get_env_var(&name),
            AppEffect::SetEnvVar { name, value } => Self::execute_set_env_var(&name, &value),
            AppEffect::LogInfo { message: _ }
            | AppEffect::LogSuccess { message: _ }
            | AppEffect::LogWarn { message: _ }
            | AppEffect::LogError { message: _ } => AppEffectResult::Ok,
        }
    }
}
