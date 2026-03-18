use crate::checkpoint::file_capture;
use crate::checkpoint::git_capture;

impl FileSystemState {
    /// Create a new file system state.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Internal implementation for CWD-relative file state capture.
    ///
    /// This is a crate-internal function that uses CWD-relative paths. It exists to support
    /// CLI-layer code that operates before a workspace is available. New pipeline code
    /// should use `capture_with_workspace` instead.
    pub(crate) fn capture_with_optional_executor_impl(
        executor: Option<&dyn ProcessExecutor>,
    ) -> Self {
        executor.map_or_else(
            || {
                let real_executor = RealProcessExecutor::new();
                Self::capture_current_with_executor_impl(&real_executor)
            },
            Self::capture_current_with_executor_impl,
        )
    }

    /// Internal implementation for CWD-relative file state capture with executor.
    ///
    /// This is a crate-internal function that uses CWD-relative paths. It exists to support
    /// CLI-layer code that operates before a workspace is available. New pipeline code
    /// should use `capture_with_workspace` instead.
    fn capture_current_with_executor_impl(executor: &dyn ProcessExecutor) -> Self {
        let files_to_capture = [
            "PROMPT.md",
            ".agent/PLAN.md",
            ".agent/ISSUES.md",
            ".agent/config.toml",
            ".agent/start_commit",
            ".agent/NOTES.md",
            ".agent/status",
        ];

        Self {
            files: files_to_capture
                .iter()
                .map(|path| {
                    let snapshot = snapshot_for_path(path);
                    (path.to_string(), snapshot)
                })
                .collect(),
            ..Self::with_git_state(executor)
        }
    }

    /// Capture the current state of key files using a workspace.
    ///
    /// This includes files that are critical for pipeline execution:
    /// - PROMPT.md: The primary task description
    /// - .agent/PLAN.md: The implementation plan (if exists)
    /// - .agent/ISSUES.md: Review findings (if exists)
    /// - .agent/config.toml: Agent configuration (if exists)
    /// - .`agent/start_commit`: Baseline commit reference (if exists)
    /// - .agent/NOTES.md: Development notes (if exists)
    /// - .agent/status: Pipeline status file (if exists)
    pub fn capture_with_workspace(
        workspace: &dyn Workspace,
        executor: &dyn ProcessExecutor,
    ) -> Self {
        let files_to_capture = [
            "PROMPT.md",
            ".agent/PLAN.md",
            ".agent/ISSUES.md",
            ".agent/config.toml",
            ".agent/start_commit",
            ".agent/NOTES.md",
            ".agent/status",
        ];

        Self {
            files: files_to_capture
                .iter()
                .map(|path| {
                    let snapshot = snapshot_for_path_workspace(workspace, path);
                    (path.to_string(), snapshot)
                })
                .collect(),
            ..Self::with_git_state(executor)
        }
    }
}

fn snapshot_for_path(path: &str) -> FileSnapshot {
    let path_obj = Path::new(path);
    if path_obj.exists() {
        file_capture::read_file_bytes(path_obj).map_or_else(
            || FileSnapshot::not_found(path),
            |content| {
                let checksum = crate::checkpoint::state::calculate_checksum_from_bytes(&content);
                let size = content.len() as u64;
                FileSnapshot::new(path, checksum, size, true)
            },
        )
    } else {
        FileSnapshot::not_found(path)
    }
}

fn snapshot_for_path_workspace(workspace: &dyn Workspace, path: &str) -> FileSnapshot {
    let path_ref = Path::new(path);
    if workspace.exists(path_ref) {
        workspace.read_bytes(path_ref).map_or_else(
            |_| FileSnapshot::not_found(path),
            |content| {
                let checksum = crate::checkpoint::state::calculate_checksum_from_bytes(&content);
                let size = content.len() as u64;
                FileSnapshot::new(path, checksum, size, true)
            },
        )
    } else {
        FileSnapshot::not_found(path)
    }
}

impl FileSystemState {
    /// Build git state fields using an executor, returning a partial struct for struct-update.
    fn with_git_state(executor: &dyn ProcessExecutor) -> Self {
        if crate::interrupt::user_interrupted_occurred() {
            return Self::default();
        }

        Self {
            files: HashMap::new(),
            git_head_oid: git_capture::git_head_oid(executor),
            git_branch: git_capture::git_branch_name(executor),
            git_status: git_capture::git_status(executor),
            git_modified_files: git_capture::git_modified_files(executor),
        }
    }
}
