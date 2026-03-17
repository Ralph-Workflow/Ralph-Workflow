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

        let mut state = files_to_capture
            .iter()
            .fold(Self::new(), |mut state, path| {
                state.capture_file_impl(path);
                state
            });

        state.capture_git_state(executor);
        state
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

        let mut state = files_to_capture
            .iter()
            .fold(Self::new(), |mut state, path| {
                state.capture_file_with_workspace(workspace, path);
                state
            });

        state.capture_git_state(executor);
        state
    }

    /// Capture a single file's state using a workspace.
    pub fn capture_file_with_workspace(&mut self, workspace: &dyn Workspace, path: &str) {
        let path_ref = Path::new(path);
        let snapshot = if workspace.exists(path_ref) {
            workspace.read_bytes(path_ref).map_or_else(
                |_| FileSnapshot::not_found(path),
                |content| {
                    let checksum =
                        crate::checkpoint::state::calculate_checksum_from_bytes(&content);
                    let size = content.len() as u64;
                    FileSnapshot::new(path, checksum, size, true)
                },
            )
        } else {
            FileSnapshot::not_found(path)
        };

        self.files.insert(path.to_string(), snapshot);
    }

    /// Internal implementation of file capture using CWD-relative paths.
    ///
    /// This is the core logic used by `capture_current_with_executor_impl`.
    fn capture_file_impl(&mut self, path: &str) {
        let path_obj = Path::new(path);
        let snapshot = if path_obj.exists() {
            std::fs::read(path_obj).map_or_else(
                |_| FileSnapshot::not_found(path),
                |content| {
                    let checksum =
                        crate::checkpoint::state::calculate_checksum_from_bytes(&content);
                    let size = content.len() as u64;
                    FileSnapshot::new(path, checksum, size, true)
                },
            )
        } else {
            FileSnapshot::not_found(path)
        };

        self.files.insert(path.to_string(), snapshot);
    }

    /// Capture git HEAD state and working tree status.
    ///
    /// Skips all git commands when a user interrupt is pending. Blocking on
    /// `executor.execute("git", ...)` after an interrupt-triggered agent kill can hang
    /// indefinitely because orphaned processes may hold pipe write ends open, or because
    /// git cannot acquire lock files left by the killed agent. Skipping is safe: the
    /// checkpoint will simply have no git state, which is acceptable on interrupt.
    fn capture_git_state(&mut self, executor: &dyn ProcessExecutor) {
        if crate::interrupt::user_interrupted_occurred() {
            return;
        }

        // Try to get HEAD OID
        if let Ok(output) = executor.execute("git", &["rev-parse", "HEAD"], &[], None) {
            if output.status.success() {
                let oid = output.stdout.trim().to_string();
                self.git_head_oid = Some(oid);
            }
        }

        // Try to get branch name
        if let Ok(output) =
            executor.execute("git", &["rev-parse", "--abbrev-ref", "HEAD"], &[], None)
        {
            if output.status.success() {
                let branch = output.stdout.trim().to_string();
                if !branch.is_empty() && branch != "HEAD" {
                    self.git_branch = Some(branch);
                }
            }
        }

        // Capture git status --porcelain for tracking staged/unstaged changes
        if let Ok(output) = executor.execute("git", &["status", "--porcelain"], &[], None) {
            if output.status.success() {
                let status = output.stdout.trim().to_string();
                if !status.is_empty() {
                    self.git_status = Some(status);
                }
            }
        }

        // Capture list of modified files from git diff
        if let Ok(output) = executor.execute("git", &["diff", "--name-only"], &[], None) {
            if output.status.success() {
                let diff_output = &output.stdout;
                let modified_files: Vec<String> = diff_output
                    .lines()
                    .map(|line| line.trim().to_string())
                    .filter(|line| !line.is_empty())
                    .collect();
                if !modified_files.is_empty() {
                    self.git_modified_files = Some(modified_files);
                }
            }
        }
    }
}
