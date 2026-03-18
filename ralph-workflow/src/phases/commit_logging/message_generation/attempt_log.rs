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

    /// Set the prompt size.
    pub const fn set_prompt_size(&mut self, size: usize) {
        self.prompt_size_bytes = size;
    }

    /// Set the diff information.
    pub const fn set_diff_info(&mut self, size: usize, was_truncated: bool) {
        self.diff_size_bytes = size;
        self.diff_was_truncated = was_truncated;
    }

    /// Set the raw output from the agent.
    ///
    /// Truncates very large outputs to prevent log file bloat.
    pub fn set_raw_output(&mut self, output: &str) {
        const MAX_OUTPUT_SIZE: usize = 50_000;
        if output.len() > MAX_OUTPUT_SIZE {
            self.raw_output = Some(format!(
                "{}\n\n[... truncated {} bytes ...]\n\n{}",
                &output[..MAX_OUTPUT_SIZE / 2],
                output.len() - MAX_OUTPUT_SIZE,
                &output[output.len() - MAX_OUTPUT_SIZE / 2..]
            ));
        } else {
            self.raw_output = Some(output.to_string());
        }
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
        let mut content = String::new();
        self.write_header_to_string(&mut content);
        self.write_context_to_string(&mut content);
        self.write_raw_output_to_string(&mut content);
        self.write_extraction_attempts_to_string(&mut content);
        self.write_validation_to_string(&mut content);
        self.write_outcome_to_string(&mut content);

        // Write using workspace
        workspace.write(&log_path, &content)?;
        Ok(log_path)
    }

    // String-based write helpers for workspace support
    fn write_header_to_string(&self, s: &mut String) {
        use std::fmt::Write;
        let _ = writeln!(
            s,
            "========================================================================"
        );
        let _ = writeln!(s, "COMMIT GENERATION ATTEMPT LOG");
        let _ = writeln!(
            s,
            "========================================================================"
        );
        let _ = writeln!(s);
        let _ = writeln!(s, "Attempt:   #{}", self.attempt_number);
        let _ = writeln!(s, "Agent:     {}", self.agent);
        let _ = writeln!(s, "Strategy:  {}", self.strategy);
        let _ = writeln!(
            s,
            "Timestamp: {}",
            self.timestamp.format("%Y-%m-%d %H:%M:%S %Z")
        );
        let _ = writeln!(s);
    }

    fn write_context_to_string(&self, s: &mut String) {
        use std::fmt::Write;
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s, "CONTEXT");
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s);
        let _ = writeln!(
            s,
            "Prompt size: {} bytes ({} KB)",
            self.prompt_size_bytes,
            self.prompt_size_bytes / 1024
        );
        let _ = writeln!(
            s,
            "Diff size:   {} bytes ({} KB)",
            self.diff_size_bytes,
            self.diff_size_bytes / 1024
        );
        let _ = writeln!(
            s,
            "Diff truncated: {}",
            if self.diff_was_truncated { "YES" } else { "NO" }
        );
        let _ = writeln!(s);
    }

    fn write_raw_output_to_string(&self, s: &mut String) {
        use std::fmt::Write;
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s, "RAW AGENT OUTPUT");
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s);
        match &self.raw_output {
            Some(output) => {
                let _ = writeln!(s, "{output}");
            }
            None => {
                let _ = writeln!(s, "[No output captured]");
            }
        }
        let _ = writeln!(s);
    }

    fn write_extraction_attempts_to_string(&self, s: &mut String) {
        use std::fmt::Write;
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s, "EXTRACTION ATTEMPTS");
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s);

        if self.extraction_attempts.is_empty() {
            let _ = writeln!(s, "[No extraction attempts recorded]");
        } else {
            let attempt_lines: Vec<String> = self
                .extraction_attempts
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
                .collect();
            for line in attempt_lines {
                let _ = writeln!(s, "{}", line);
            }
        }
        let _ = writeln!(s);
    }

    fn write_validation_to_string(&self, s: &mut String) {
        use std::fmt::Write;
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s, "VALIDATION RESULTS");
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s);

        if self.validation_checks.is_empty() {
            let _ = writeln!(s, "[No validation checks recorded]");
        } else {
            let validation_lines: Vec<String> = self
                .validation_checks
                .iter()
                .map(|check| {
                    let status = if check.passed { "✓ PASS" } else { "✗ FAIL" };
                    if let Some(error) = &check.error {
                        format!("  [{status}] {}: {error}", check.name)
                    } else {
                        format!("  [{status}] {}", check.name)
                    }
                })
                .collect();
            for line in validation_lines {
                let _ = writeln!(s, "{}", line);
            }
        }
        let _ = writeln!(s);
    }

    fn write_outcome_to_string(&self, s: &mut String) {
        use std::fmt::Write;
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s, "OUTCOME");
        let _ = writeln!(
            s,
            "------------------------------------------------------------------------"
        );
        let _ = writeln!(s);
        match &self.outcome {
            Some(outcome) => {
                let _ = writeln!(s, "{outcome}");
            }
            None => {
                let _ = writeln!(s, "[Outcome not recorded]");
            }
        }
        let _ = writeln!(s);
        let _ = writeln!(
            s,
            "========================================================================"
        );
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
