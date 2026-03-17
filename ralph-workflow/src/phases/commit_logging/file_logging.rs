// File-based logging operations - writing to log files, file management.
// This file is included via include!() macro from the parent commit_logging.rs module.

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
    /// # Arguments
    ///
    /// * `base_log_dir` - Base directory for commit logs (e.g., `.agent/logs/commit_generation`)
    /// * `workspace` - The workspace to use for filesystem operations
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
    ///
    /// This is used as a fallback when all log directories fail to be created.
    /// The session will still track attempt numbers and provide a dummy `run_dir`,
    /// but writes will silently succeed without actually writing anything.
    ///
    /// # Returns
    ///
    /// A `CommitLogSession` that uses `/dev/null` equivalent as its run directory.
    #[must_use] 
    pub fn noop() -> Self {
        // Use a path that indicates this is a noop session
        // The path won't be created or written to by noop session
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
    pub const fn next_attempt_number(&mut self) -> usize {
        self.attempt_counter = self.attempt_counter.saturating_add(1);
        self.attempt_counter
    }

    /// Create a new attempt log for this session.
    ///
    /// # Arguments
    ///
    /// * `agent` - The agent being used
    /// * `strategy` - The retry strategy being used
    pub fn new_attempt(&mut self, agent: &str, strategy: &str) -> CommitAttemptLog {
        let attempt_number = self.next_attempt_number();
        CommitAttemptLog::new(attempt_number, agent, strategy)
    }

    /// Write summary file at end of session.
    ///
    /// For noop sessions, this silently succeeds without writing anything.
    ///
    /// # Arguments
    ///
    /// * `total_attempts` - Total number of attempts made
    /// * `final_outcome` - Description of the final outcome
    /// * `workspace` - The workspace to use for filesystem operations
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
        // Skip writing for noop sessions
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
