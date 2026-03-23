// phases/commit_logging/io.rs — boundary module for mutable session state.
// File stem is `io` — recognized as boundary module by functional lints.
// Mutable-receiver methods on CommitLogSession live here to comply with
// forbid_mutating_receiver_methods in non-boundary code.

/// Session tracker for commit generation logging.
///
/// Manages a unique run directory for a commit generation session,
/// ensuring log files are organized and don't overwrite each other.
#[derive(Debug)]
pub struct CommitLogSession {
    /// Base log directory
    run_dir: PathBuf,
    /// Current attempt counter
    attempt_counter: usize,
}

impl CommitLogSession {
    /// Create a new logging session using workspace abstraction.
    ///
    /// Creates a unique run directory under the base log path.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn new(base_log_dir: &str, workspace: &dyn Workspace) -> std::io::Result<Self> {
        let timestamp = Local::now().format("%Y%m%d_%H%M%S");
        let run_dir = PathBuf::from(base_log_dir).join(format!("run_{timestamp}"));
        workspace.create_dir_all(&run_dir)?;

        Ok(Self {
            run_dir,
            attempt_counter: 0,
        })
    }

    /// Create a no-op logging session that discards all writes.
    #[must_use]
    pub fn noop() -> Self {
        Self {
            run_dir: PathBuf::from("/dev/null/ralph-noop-session"),
            attempt_counter: 0,
        }
    }

    /// Check if this is a no-op session.
    #[must_use]
    pub fn is_noop(&self) -> bool {
        self.run_dir.starts_with("/dev/null")
    }

    /// Get the path to the run directory.
    #[must_use]
    pub fn run_dir(&self) -> &Path {
        &self.run_dir
    }

    /// Get the next attempt number and increment the counter.
    pub fn next_attempt_number(&mut self) -> usize {
        self.attempt_counter = self.attempt_counter.saturating_add(1);
        self.attempt_counter
    }

    /// Create a new attempt log for this session.
    pub fn new_attempt(&mut self, agent: &str, strategy: &str) -> CommitAttemptLog {
        let attempt_number = self.next_attempt_number();
        CommitAttemptLog::new(attempt_number, agent, strategy)
    }

    /// Write summary file at end of session.
    ///
    /// For noop sessions, this silently succeeds without writing anything.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn write_summary(
        &self,
        total_attempts: usize,
        final_outcome: &str,
        workspace: &dyn Workspace,
    ) -> std::io::Result<()> {
        if self.is_noop() {
            return Ok(());
        }

        let summary_path = self.run_dir.join("SUMMARY.txt");

        let content = format!(
            "COMMIT GENERATION SESSION SUMMARY\n\
             =================================\n\
             \n\
             Run directory: {}\n\
             Total attempts: {}\n\
             Final outcome: {}\n\
             \n\
             Individual attempt logs are in this directory.\n",
            self.run_dir.display(),
            total_attempts,
            final_outcome
        );

        workspace.write(&summary_path, &content)?;
        Ok(())
    }
}
