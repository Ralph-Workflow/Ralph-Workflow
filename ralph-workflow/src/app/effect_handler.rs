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
    /// Create a new handler with the current working directory as workspace root.
    ///
    /// This is the correct default because `ralph-workflow` is always invoked from
    /// the command line in a directory that IS the workspace (or a subdirectory of it).
    ///
    /// Use `with_workspace_root()` to create a handler with an explicit workspace root.
    #[must_use]
    pub fn new() -> Self {
        let workspace_root = crate::app::io::effect_io::current_working_dir();
        if let Err(ref e) = workspace_root {
            eprintln!(
                "WARNING: RealAppEffectHandler::new() could not determine cwd: {}. \
                 Git operations will fail with 'workspace root is not set'. \
                 Use RealAppEffectHandler::with_workspace_root() for explicit override.",
                e
            );
        }
        Self {
            workspace_root: workspace_root.ok(),
        }
    }

    #[must_use]
    pub const fn with_workspace_root(root: PathBuf) -> Self {
        Self {
            workspace_root: Some(root),
        }
    }

    /// Returns true if this handler has a workspace root set.
    ///
    /// When `true`, operations that require a workspace (git operations, path resolution)
    /// will use the configured workspace root. When `false`, these operations will fail
    /// with "workspace root is not set" error.
    ///
    /// `new()` defaults to the current working directory, so this should normally return
    /// `true` for production code. Use `with_workspace_root()` for explicit override.
    #[must_use]
    pub fn has_workspace_root(&self) -> bool {
        self.workspace_root.is_some()
    }

    /// Assert that this handler has a workspace root set.
    ///
    /// # Panics
    ///
    /// Panics with a clear error message if `workspace_root` is `None`.
    /// This catches programming errors where `RealAppEffectHandler` is constructed
    /// without a workspace root (e.g., via `new()` in older code).
    #[track_caller]
    pub fn assert_has_workspace_root(&self) {
        if self.workspace_root.is_none() {
            panic!(
                "WORKSPACE ROOT NOT SET: RealAppEffectHandler was constructed without a workspace root. \
                 This is a programming error. Use RealAppEffectHandler::with_workspace_root() or \
                 ensure RealAppEffectHandler::new() is used (which defaults to cwd).",
            );
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

    /// Verify that `new()` creates a handler with the current working directory as workspace root.
    #[test]
    fn new_has_workspace_root_from_cwd() {
        let handler = RealAppEffectHandler::new();
        let cwd = std::env::current_dir().expect("cwd must be available");
        assert!(
            handler.workspace_root.is_some(),
            "new() must create handler with workspace_root set to cwd"
        );
        assert_eq!(
            handler.workspace_root.as_deref(),
            Some(cwd.as_path()),
            "new() must use current working directory as workspace root"
        );
    }

    /// Verify that `default()` also creates a handler with the current working directory as workspace root.
    #[test]
    fn default_has_workspace_root_from_cwd() {
        let handler = RealAppEffectHandler::default();
        let cwd = std::env::current_dir().expect("cwd must be available");
        assert!(
            handler.workspace_root.is_some(),
            "default() must create handler with workspace_root set to cwd"
        );
        assert_eq!(
            handler.workspace_root.as_deref(),
            Some(cwd.as_path()),
            "default() must use current working directory as workspace root"
        );
    }

    /// Verify that `with_workspace_root` stores the provided root correctly.
    #[test]
    fn with_workspace_root_stores_provided_root() {
        let root = PathBuf::from("/tmp/test-workspace");
        let handler = RealAppEffectHandler::with_workspace_root(root.clone());
        assert_eq!(
            handler.workspace_root,
            Some(root),
            "with_workspace_root must store the provided root"
        );
    }

    /// **Diagnosis evidence (Step 1):** Root cause of MCP "tool does not exist" error.
    ///
    /// The original bug manifested as agents reporting `ralph_submit_artifact` does
    /// not exist. The root cause was a cascading failure chain:
    ///
    /// 1. `RealAppEffectHandler::new()` (pre-fix) created a handler with
    ///    `workspace_root: None` because it did not default to `current_dir()`.
    /// 2. When the handler executed `GitGetRepoRoot`, it returned the error:
    ///    "Cannot get repository root: workspace root is not set."
    /// 3. This error propagated to `discover_repo_root()`, which failed.
    /// 4. Without a repo root, `SessionBridge` could not construct a valid
    ///    `McpServerConfig`, so the MCP server either didn't start or started
    ///    with an invalid configuration.
    /// 5. The agent process received `RALPH_MCP_ENDPOINT` pointing to a
    ///    non-functional or never-started server, causing all tool calls to fail.
    ///
    /// **Fix:** `RealAppEffectHandler::new()` now defaults `workspace_root` to
    /// `std::env::current_dir()`, and all call sites pass explicit roots.
    ///
    /// This test reproduces the exact failure point: calling `GitGetRepoRoot` on
    /// a handler without workspace root, and verifies the specific error message.
    #[test]
    fn diagnosis_workspace_root_none_causes_repo_root_error() {
        // Simulate the pre-fix state: handler with no workspace root.
        let mut handler = RealAppEffectHandler {
            workspace_root: None,
        };
        let result = handler.execute(AppEffect::GitGetRepoRoot);
        match result {
            AppEffectResult::Error(msg) => {
                assert!(
                    msg.contains("workspace root is not set"),
                    "Error must mention 'workspace root is not set', got: {msg}"
                );
            }
            other => {
                panic!("GitGetRepoRoot with workspace_root: None must return Error, got: {other:?}")
            }
        }
    }

    /// Verify that the MCP protocol layer handles git operations correctly
    /// when workspace is properly initialized. This test uses SessionBridge
    /// with a MemoryWorkspace to avoid real git operations.
    #[test]
    fn mcp_git_status_succeeds_with_proper_workspace() {
        use crate::agents::session::{AgentSession, SessionDrain};
        use crate::workspace::memory_workspace::MemoryWorkspace;
        use mcp_server::io::ServerState;
        use mcp_server::protocol::JsonRpcRequest;
        use std::sync::Arc;

        let ws = Arc::new(MemoryWorkspace::new_test());
        let session = AgentSession::for_drain(
            "workspace-root-test".to_string(),
            SessionDrain::Development,
            1,
        );
        let workspace: Arc<dyn crate::workspace::Workspace> = ws;
        let mut bridge = crate::mcp_server::session_bridge::SessionBridge::new(session, workspace);
        bridge.start().expect("bridge must start");

        // Initialize the session
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

        // Call git_status via MCP
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

        // Must be a valid JSON-RPC response (not a panic)
        assert!(
            git_resp.get("jsonrpc").is_some(),
            "Response must be a valid JSON-RPC envelope: {git_resp}"
        );
    }

    /// Verify that `assert_has_workspace_root()` panics when `workspace_root` is `None`.
    ///
    /// This is the regression test for the workspace root detection requirement:
    /// the fail-fast guard must panic immediately with a clear message rather than
    /// letting the `None` propagate to a git operation deep in the stack.
    #[test]
    #[should_panic(expected = "WORKSPACE ROOT NOT SET")]
    fn assert_has_workspace_root_panics_when_none() {
        let handler = RealAppEffectHandler {
            workspace_root: None,
        };
        handler.assert_has_workspace_root();
    }

    /// Verify that `assert_has_workspace_root()` does NOT panic when root is set.
    #[test]
    fn assert_has_workspace_root_passes_when_set() {
        let handler = RealAppEffectHandler::with_workspace_root(PathBuf::from("/tmp/test-ws"));
        // Must not panic
        handler.assert_has_workspace_root();
    }

    /// Verify that all workspace-root-dependent git effects return an error containing
    /// "workspace root is not set" when `workspace_root` is `None`.
    ///
    /// This test pins the exact error message contract so that callers that match on
    /// the error string can reliably detect the unconfigured-workspace condition.
    ///
    /// The following variants do NOT check workspace_root and are intentionally excluded:
    /// - `GitRequireRepo` — calls a global git helper unrelated to root discovery
    /// - `GitContinueRebase` — deferred to executor injection
    /// - `GitAbortRebase` — deferred to executor injection
    /// - `GitRebaseOnto` — deferred to executor injection
    #[test]
    fn workspace_root_dependent_git_effects_fail_with_workspace_not_set_message_when_root_is_none()
    {
        let mut handler = RealAppEffectHandler {
            workspace_root: None,
        };

        let root_dependent_effects = [
            AppEffect::GitGetRepoRoot,
            AppEffect::GitGetHeadOid,
            AppEffect::GitDiff,
            AppEffect::GitDiffFrom {
                start_oid: "abc123".to_string(),
            },
            AppEffect::GitDiffFromStart,
            AppEffect::GitSnapshot,
            AppEffect::GitAddAll,
            AppEffect::GitCommit {
                message: "test".to_string(),
                user_name: None,
                user_email: None,
            },
            AppEffect::GitSaveStartCommit,
            AppEffect::GitResetStartCommit,
            AppEffect::GitGetConflictedFiles,
            AppEffect::GitGetDefaultBranch,
            AppEffect::GitIsMainBranch,
        ];

        for effect in root_dependent_effects {
            let effect_name = format!("{effect:?}");
            let result = handler.execute(effect);
            match result {
                AppEffectResult::Error(msg) => {
                    assert!(
                        msg.contains("workspace root is not set"),
                        "Effect {effect_name} must include 'workspace root is not set' in error, got: {msg}"
                    );
                }
                other => {
                    panic!(
                        "Effect {effect_name} with workspace_root: None must return Error, got: {other:?}"
                    );
                }
            }
        }
    }
}
