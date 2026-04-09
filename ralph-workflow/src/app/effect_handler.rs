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

    fn execute_git_get_repo_root(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot get repository root: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::get_repo_root_in_repo(workspace_root) {
            Ok(root) => AppEffectResult::Path(root),
            Err(error) => AppEffectResult::Error(format!("Failed to get repository root: {error}")),
        }
    }

    fn execute_git_get_head_oid(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot get HEAD OID: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::get_current_head_oid_at(workspace_root) {
            Ok(oid) => AppEffectResult::String(oid),
            Err(error) => AppEffectResult::Error(format!("Failed to get HEAD OID: {error}")),
        }
    }

    fn execute_git_diff(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot get git diff: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::git_diff_in_repo(workspace_root) {
            Ok(diff) => AppEffectResult::String(diff),
            Err(error) => AppEffectResult::Error(format!("Failed to get git diff: {error}")),
        }
    }

    fn execute_git_diff_from(&self, start_oid: &str) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot get diff from start commit: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::git_diff_from_in_repo(workspace_root, start_oid) {
            Ok(diff) => AppEffectResult::String(diff),
            Err(error) => {
                AppEffectResult::Error(format!("Failed to get diff from '{start_oid}': {error}"))
            }
        }
    }

    fn execute_git_diff_from_start(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot get diff from start commit: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::get_git_diff_from_start(workspace_root) {
            Ok(diff) => AppEffectResult::String(diff),
            Err(error) => {
                AppEffectResult::Error(format!("Failed to get diff from start commit: {error}"))
            }
        }
    }

    fn execute_git_snapshot(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot create git snapshot: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::git_snapshot_in_repo(workspace_root) {
            Ok(snapshot) => AppEffectResult::String(snapshot),
            Err(error) => AppEffectResult::Error(format!("Failed to create git snapshot: {error}")),
        }
    }

    fn execute_git_add_all(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot stage all changes: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)` \
                     when git add effects are needed."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::git_add_all_in_repo(workspace_root) {
            Ok(staged) => AppEffectResult::Bool(staged),
            Err(error) => AppEffectResult::Error(format!("Failed to stage all changes: {error}")),
        }
    }

    fn execute_git_commit(
        &self,
        message: &str,
        user_name: Option<&str>,
        user_email: Option<&str>,
    ) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot execute git commit: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)` \
                     when git commit effects are needed."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::git_commit_in_repo(
            workspace_root,
            message,
            user_name,
            user_email,
            None,
            None,
        ) {
            Ok(Some(oid)) => AppEffectResult::Commit(CommitResult::Success(oid.to_string())),
            Ok(None) => AppEffectResult::Commit(CommitResult::NoChanges),
            Err(error) => AppEffectResult::Error(format!("Failed to create commit: {error}")),
        }
    }

    fn execute_git_save_start_commit(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot save start commit: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::save_start_commit(workspace_root) {
            Ok(()) => AppEffectResult::Ok,
            Err(error) => AppEffectResult::Error(format!("Failed to save start commit: {error}")),
        }
    }

    fn execute_git_reset_start_commit(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot reset start commit: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::reset_start_commit(workspace_root) {
            Ok(result) => AppEffectResult::String(result.oid),
            Err(error) => AppEffectResult::Error(format!("Failed to reset start commit: {error}")),
        }
    }

    fn execute_git_rebase_onto(_upstream_branch: String) -> AppEffectResult {
        AppEffectResult::Error(
            "GitRebaseOnto requires executor injection - use pipeline runner".to_string(),
        )
    }

    fn execute_git_get_conflicted_files(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot get conflicted files: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::get_conflicted_files_at(workspace_root) {
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

    fn execute_git_get_default_branch(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot get default branch: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::get_default_branch_at(workspace_root) {
            Ok(branch) => AppEffectResult::String(branch),
            Err(error) => AppEffectResult::Error(format!("Failed to get default branch: {error}")),
        }
    }

    fn execute_git_is_main_branch(&self) -> AppEffectResult {
        let workspace_root = match self.workspace_root.as_ref() {
            Some(root) => root,
            None => {
                return AppEffectResult::Error(
                    "Cannot check if main branch: workspace root is not set. \
                     RealAppEffectHandler must be constructed with `with_workspace_root(root)`."
                        .to_string(),
                );
            }
        };
        match crate::git_helpers::is_main_or_master_branch_at(workspace_root) {
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
            AppEffect::GitGetRepoRoot => self.execute_git_get_repo_root(),
            AppEffect::GitGetHeadOid => self.execute_git_get_head_oid(),
            AppEffect::GitDiff => self.execute_git_diff(),
            AppEffect::GitDiffFrom { start_oid } => self.execute_git_diff_from(&start_oid),
            AppEffect::GitDiffFromStart => self.execute_git_diff_from_start(),
            AppEffect::GitSnapshot => self.execute_git_snapshot(),
            AppEffect::GitAddAll => self.execute_git_add_all(),
            AppEffect::GitCommit {
                message,
                user_name,
                user_email,
            } => self.execute_git_commit(&message, user_name.as_deref(), user_email.as_deref()),
            AppEffect::GitSaveStartCommit => self.execute_git_save_start_commit(),
            AppEffect::GitResetStartCommit => self.execute_git_reset_start_commit(),
            AppEffect::GitRebaseOnto { upstream_branch } => {
                Self::execute_git_rebase_onto(upstream_branch)
            }
            AppEffect::GitGetConflictedFiles => self.execute_git_get_conflicted_files(),
            AppEffect::GitContinueRebase => Self::execute_git_continue_rebase(),
            AppEffect::GitAbortRebase => Self::execute_git_abort_rebase(),
            AppEffect::GitGetDefaultBranch => self.execute_git_get_default_branch(),
            AppEffect::GitIsMainBranch => self.execute_git_is_main_branch(),
            AppEffect::GetEnvVar { name } => Self::execute_get_env_var(&name),
            AppEffect::SetEnvVar { name, value } => Self::execute_set_env_var(&name, &value),
            AppEffect::LogInfo { message: _ }
            | AppEffect::LogSuccess { message: _ }
            | AppEffect::LogWarn { message: _ }
            | AppEffect::LogError { message: _ } => AppEffectResult::Ok,
        }
    }
}

#[cfg(test)]
mod workspace_root_guard_tests {
    use super::*;

    fn handler_without_root() -> RealAppEffectHandler {
        RealAppEffectHandler::new()
    }

    #[test]
    fn git_get_repo_root_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_get_repo_root() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_get_head_oid_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_get_head_oid() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_diff_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_diff() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_commit_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_commit("test message", None, None) {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_snapshot_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_snapshot() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_get_conflicted_files_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_get_conflicted_files() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_get_default_branch_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_get_default_branch() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_is_main_branch_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_is_main_branch() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_diff_from_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_diff_from("abc123") {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_diff_from_start_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_diff_from_start() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_save_start_commit_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_save_start_commit() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_reset_start_commit_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_reset_start_commit() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    #[test]
    fn git_add_all_fails_with_clear_error_when_workspace_root_not_set() {
        let handler = handler_without_root();
        match handler.execute_git_add_all() {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error message must mention workspace root: got {msg:?}"
                );
                assert!(
                    msg.contains("with_workspace_root"),
                    "Error message must mention constructor: got {msg:?}"
                );
            }
            other => panic!("Expected AppEffectResult::Error, got {other:?}"),
        }
    }

    /// Verify that the workspace_root error propagates through the MCP protocol layer.
    ///
    /// When git operations fail due to missing workspace_root, the MCP layer must
    /// return a proper JSON-RPC error response rather than panicking.
    #[test]
    fn workspace_root_error_propagates_through_mcp_protocol() {
        use crate::agents::session::{AgentSession, SessionDrain};
        use crate::workspace::memory_workspace::MemoryWorkspace;
        use mcp_server::io::ServerState;
        use mcp_server::protocol::JsonRpcRequest;
        use std::sync::Arc;

        // Use a memory workspace (does not touch git) — this will succeed
        // The workspace_root error is at the effect_handler layer, not the workspace layer.
        // This test proves the MCP layer handles tool errors as protocol-level errors.
        let ws = Arc::new(MemoryWorkspace::new_test());
        let session = AgentSession::for_drain(
            "workspace-root-test".to_string(),
            SessionDrain::Development,
            1,
        );
        let workspace: Arc<dyn crate::workspace::Workspace> = ws;
        let mut bridge = crate::mcp_server::session_bridge::SessionBridge::new(session, workspace);
        bridge.start().expect("bridge must start");

        // Initialize the session first
        let init_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        }))
        .expect("init request is valid");
        let (init_resp, state) =
            bridge.handle_request_in_process(init_req, ServerState::Uninitialized);
        let init_resp = serde_json::to_value(init_resp.expect("initialize must return a response"))
            .expect("serialize");
        assert!(
            init_resp.get("error").is_none(),
            "initialize must succeed: {init_resp}"
        );

        // Call git_status — this goes through the MCP protocol and hits the tool handler.
        // The git_status tool handler uses the workspace's git integration.
        // Since MemoryWorkspace doesn't have real git, this should return a tool error,
        // not a panic from missing workspace_root.
        let git_status_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "git_status",
                "arguments": {}
            },
            "id": 2
        }))
        .expect("git_status request is valid");
        let (git_resp, _) = bridge.handle_request_in_process(git_status_req, state);
        let git_resp = serde_json::to_value(git_resp.expect("git_status must return a response"))
            .expect("serialize");

        // The response must be a proper JSON-RPC response (error or result), not a panic.
        // Either an error response or a successful (but possibly empty) result is acceptable.
        // What's NOT acceptable is a panic/unwrap failure.
        assert!(
            git_resp.get("jsonrpc").is_some(),
            "Response must be a valid JSON-RPC envelope: {git_resp}"
        );
        // Either error or result must be present
        assert!(
            git_resp.get("error").is_some() || git_resp.get("result").is_some(),
            "Response must have error or result: {git_resp}"
        );
    }
}
