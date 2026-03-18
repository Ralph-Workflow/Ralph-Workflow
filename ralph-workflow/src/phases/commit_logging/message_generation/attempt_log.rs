/// Per-attempt log for commit message generation.
///
/// Captures all details about a single attempt to generate a commit message,
/// providing a complete audit trail for debugging.
#[derive(Debug, Clone)]
pub struct CommitAttemptLog {
    /// Attempt number within this session
    pub attempt_number: usize,
    /// Agent being used (e.g., "claude", "glm")
    pub agent: String,
    /// Retry strategy (e.g., "initial", "`strict_json`")
    pub strategy: String,
    /// Timestamp when attempt started
    pub timestamp: DateTime<Local>,
    /// Size of the prompt in bytes
    pub prompt_size_bytes: usize,
    /// Size of the diff in bytes
    pub diff_size_bytes: usize,
    /// Whether the diff was pre-truncated
    pub diff_was_truncated: bool,
    /// Raw output from the agent (truncated if very large)
    pub raw_output: Option<String>,
    /// Extraction attempts with their results
    pub extraction_attempts: Vec<ExtractionAttempt>,
    /// Validation checks that were run
    pub validation_checks: Vec<ValidationCheck>,
    /// Final outcome of this attempt
    pub outcome: Option<AttemptOutcome>,
}

impl CommitAttemptLog {
    /// Create a new attempt log.
    #[must_use]
    pub fn new(attempt_number: usize, agent: &str, strategy: &str) -> Self {
        Self {
            attempt_number,
            agent: agent.to_string(),
            strategy: strategy.to_string(),
            timestamp: Local::now(),
            prompt_size_bytes: 0,
            diff_size_bytes: 0,
            diff_was_truncated: false,
            raw_output: None,
            extraction_attempts: Vec::new(),
            validation_checks: Vec::new(),
            outcome: None,
        }
    }

    /// Create a new attempt log with basic info already set.
    ///
    /// This is the functional equivalent of calling `new()` followed by
    /// `set_prompt_size()` and `set_diff_info()`, avoiding `let mut`.
    #[must_use]
    pub fn with_basics(
        attempt_number: usize,
        agent: &str,
        strategy: &str,
        prompt_size: usize,
        diff_size: usize,
        diff_was_truncated: bool,
    ) -> Self {
        Self {
            attempt_number,
            agent: agent.to_string(),
            strategy: strategy.to_string(),
            timestamp: Local::now(),
            prompt_size_bytes: prompt_size,
            diff_size_bytes: diff_size,
            diff_was_truncated,
            raw_output: None,
            extraction_attempts: Vec::new(),
            validation_checks: Vec::new(),
            outcome: None,
        }
    }

    /// Set the prompt size.
    pub const fn set_prompt_size(&mut self, size: usize) {
        self.prompt_size_bytes = size;
    }

    /// Set the diff information.
    pub const fn set_diff_info(&mut self, size: usize, was_truncated: bool) {
        self.diff_size_bytes = size;
        self.diff_was_truncated = was_truncated;
    }

    /// Set the raw output from the agent (consuming builder).
    ///
    /// Truncates very large outputs to prevent log file bloat.
    #[must_use]
    pub fn with_raw_output(mut self, output: &str) -> Self {
        const MAX_OUTPUT_SIZE: usize = 50_000;
        self.raw_output = if output.len() > MAX_OUTPUT_SIZE {
            Some(format!(
                "{}\n\n[... truncated {} bytes ...]\n\n{}",
                &output[..MAX_OUTPUT_SIZE / 2],
                output.len() - MAX_OUTPUT_SIZE,
                &output[output.len() - MAX_OUTPUT_SIZE / 2..]
            ))
        } else {
            Some(output.to_string())
        };
        self
    }

    /// Record an extraction attempt (consuming builder).
    #[must_use]
    pub fn with_extraction_attempt(mut self, attempt: ExtractionAttempt) -> Self {
        self.extraction_attempts.push(attempt);
        self
    }

    /// Set the final outcome (consuming builder).
    #[must_use]
    pub fn with_outcome(mut self, outcome: AttemptOutcome) -> Self {
        self.outcome = Some(outcome);
        self
    }

    /// Record an extraction attempt.
    pub fn add_extraction_attempt(&mut self, attempt: ExtractionAttempt) {
        self.extraction_attempts.push(attempt);
    }

    /// Record validation check results.
    #[cfg(test)]
    pub fn set_validation_checks(&mut self, checks: Vec<ValidationCheck>) {
        self.validation_checks = checks;
    }

    /// Set the final outcome.
    pub fn set_outcome(&mut self, outcome: AttemptOutcome) {
        self.outcome = Some(outcome);
    }

    /// Write this log to a file using workspace abstraction.
    ///
    /// This is the architecture-conformant version that uses the workspace trait
    /// instead of direct filesystem access.
    ///
    /// # Arguments
    ///
    /// * `log_dir` - Directory to write the log file to (relative to workspace)
    /// * `workspace` - The workspace to use for filesystem operations
    ///
    /// # Returns
    ///
    /// Path to the written log file on success.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn write_to_workspace(
        &self,
        log_dir: &Path,
        workspace: &dyn Workspace,
    ) -> std::io::Result<PathBuf> {
        // Create the log directory if needed
        workspace.create_dir_all(log_dir)?;

        // Generate filename
        let filename = format!(
            "attempt_{:03}_{}_{}_{}.log",
            self.attempt_number,
            sanitize_agent_name(&self.agent),
            self.strategy.replace(' ', "_"),
            self.timestamp.format("%Y%m%dT%H%M%S")
        );
        let log_path = log_dir.join(filename);

        // Build content in memory
        let content: String = [
            self.header_as_string(),
            self.context_as_string(),
            self.raw_output_as_string(),
            self.extraction_attempts_as_string(),
            self.validation_as_string(),
            self.outcome_as_string(),
        ]
        .into_iter()
        .collect();

        // Write using workspace
        workspace.write(&log_path, &content)?;
        Ok(log_path)
    }

    fn header_as_string(&self) -> String {
        format!(
            "========================================================================\n\
             COMMIT GENERATION ATTEMPT LOG\n\
             ========================================================================\n\
             \n\
             Attempt:   #{}\n\
             Agent:     {}\n\
             Strategy:  {}\n\
             Timestamp: {}\n\
             \n",
            self.attempt_number,
            self.agent,
            self.strategy,
            self.timestamp.format("%Y-%m-%d %H:%M:%S %Z")
        )
    }

    fn context_as_string(&self) -> String {
        format!(
            "------------------------------------------------------------------------\n\
             CONTEXT\n\
             ---------------------------------------------------------------------------\n\
             \n\
             Prompt size: {} bytes ({} KB)\n\
             Diff size:   {} bytes ({} KB)\n\
             Diff truncated: {}\n\
             \n",
            self.prompt_size_bytes,
            self.prompt_size_bytes / 1024,
            self.diff_size_bytes,
            self.diff_size_bytes / 1024,
            if self.diff_was_truncated { "YES" } else { "NO" }
        )
    }

    fn raw_output_as_string(&self) -> String {
        let output_section = match &self.raw_output {
            Some(output) => output.as_str(),
            None => "[No output captured]",
        };
        format!(
            "------------------------------------------------------------------------\n\
             RAW AGENT OUTPUT\n\
             ---------------------------------------------------------------------------\n\
             \n\
             {output_section}\n\
             \n"
        )
    }

    fn extraction_attempts_as_string(&self) -> String {
        let attempts_section = if self.extraction_attempts.is_empty() {
            "[No extraction attempts recorded]".to_string()
        } else {
            self.extraction_attempts
                .iter()
                .enumerate()
                .map(|(i, attempt)| {
                    let status = if attempt.success {
                        "✓ SUCCESS"
                    } else {
                        "✗ FAILED"
                    };
                    format!(
                        "{}. {} [{}]\n   Detail: {}\n",
                        i + 1,
                        attempt.method,
                        status,
                        attempt.detail
                    )
                })
                .collect::<Vec<_>>()
                .join("")
        };
        format!(
            "------------------------------------------------------------------------\n\
             EXTRACTION ATTEMPTS\n\
             ---------------------------------------------------------------------------\n\
             \n\
             {attempts_section}\n\
             \n"
        )
    }

    fn validation_as_string(&self) -> String {
        let validation_section = if self.validation_checks.is_empty() {
            "[No validation checks recorded]".to_string()
        } else {
            self.validation_checks
                .iter()
                .map(|check| {
                    let status = if check.passed { "✓ PASS" } else { "✗ FAIL" };
                    if let Some(error) = &check.error {
                        format!("  [{status}] {}: {error}", check.name)
                    } else {
                        format!("  [{status}] {}", check.name)
                    }
                })
                .collect::<Vec<_>>()
                .join("\n")
        };
        format!(
            "------------------------------------------------------------------------\n\
             VALIDATION RESULTS\n\
             ---------------------------------------------------------------------------\n\
             \n\
             {validation_section}\n\
             \n"
        )
    }

    fn outcome_as_string(&self) -> String {
        let outcome_section = match &self.outcome {
            Some(outcome) => outcome.to_string(),
            None => "[Outcome not recorded]".to_string(),
        };
        format!(
            "------------------------------------------------------------------------\n\
             OUTCOME\n\
             ---------------------------------------------------------------------------\n\
             \n\
             {outcome_section}\n\
             \n\
             ========================================================================\n"
        )
    }
}

/// Sanitize agent name for use in filename.
fn sanitize_agent_name(agent: &str) -> String {
    agent
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '_' })
        .collect::<String>()
        .chars()
        .take(MAX_AGENT_NAME_LENGTH)
        .collect()
}
