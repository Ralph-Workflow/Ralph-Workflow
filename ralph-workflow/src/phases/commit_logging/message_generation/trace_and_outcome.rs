// Commit message generation logic - formatting commit messages, extracting summaries.
// This file is included via include!() macro from the parent commit_logging.rs module.

/// Maximum length for agent name in filenames (to avoid path length issues).
const MAX_AGENT_NAME_LENGTH: usize = 20;

/// Represents a single step in the parsing trace log.
#[derive(Debug, Clone)]
pub struct ParsingTraceStep {
    /// Step number in the trace
    pub step_number: usize,
    /// Description of what was attempted
    pub description: String,
    /// Input content for this step
    pub input: Option<String>,
    /// Result/output of this step
    pub result: Option<String>,
    /// Whether this step succeeded
    pub success: bool,
    /// Additional details or error message
    pub details: String,
}

impl ParsingTraceStep {
    /// Create a new parsing trace step.
    #[must_use] 
    pub fn new(step_number: usize, description: &str) -> Self {
        Self {
            step_number,
            description: description.to_string(),
            input: None,
            result: None,
            success: false,
            details: String::new(),
        }
    }

    /// Set the input for this step.
    #[must_use] 
    pub fn with_input(mut self, input: &str) -> Self {
        // Truncate input if too large
        const MAX_INPUT_SIZE: usize = 10_000;
        self.input = if input.len() > MAX_INPUT_SIZE {
            Some(format!(
                "{}\n\n[... input truncated {} bytes ...]",
                &input[..MAX_INPUT_SIZE / 2],
                input.len() - MAX_INPUT_SIZE
            ))
        } else {
            Some(input.to_string())
        };
        self
    }

    /// Set the result for this step.
    #[must_use] 
    pub fn with_result(mut self, result: &str) -> Self {
        // Truncate result if too large
        const MAX_RESULT_SIZE: usize = 10_000;
        self.result = if result.len() > MAX_RESULT_SIZE {
            Some(format!(
                "{}\n\n[... result truncated {} bytes ...]",
                &result[..MAX_RESULT_SIZE / 2],
                result.len() - MAX_RESULT_SIZE
            ))
        } else {
            Some(result.to_string())
        };
        self
    }

    /// Set whether this step succeeded.
    #[must_use] 
    pub const fn with_success(mut self, success: bool) -> Self {
        self.success = success;
        self
    }

    /// Set additional details.
    #[must_use] 
    pub fn with_details(mut self, details: &str) -> Self {
        self.details = details.to_string();
        self
    }
}

/// Detailed parsing trace log for commit message extraction.
///
/// This log captures each step of the extraction process, showing:
/// - What extraction method was tried (XML, JSON, pattern-based)
/// - The exact content being processed at each step
/// - Validation results and why they passed/failed
/// - The final extracted message
///
/// This is separate from the attempt log and written to `parsing_trace.log`.
#[derive(Debug, Clone)]
pub struct ParsingTraceLog {
    /// Attempt number this trace belongs to
    pub attempt_number: usize,
    /// Agent that generated the output
    pub agent: String,
    /// Strategy used
    pub strategy: String,
    /// Raw output from the agent
    pub raw_output: Option<String>,
    /// Individual parsing steps
    pub steps: Vec<ParsingTraceStep>,
    /// Final extracted message (if any)
    pub final_message: Option<String>,
    /// Timestamp when trace started
    pub timestamp: DateTime<Local>,
}

impl ParsingTraceLog {
    /// Create a new parsing trace log.
    #[must_use] 
    pub fn new(attempt_number: usize, agent: &str, strategy: &str) -> Self {
        Self {
            attempt_number,
            agent: agent.to_string(),
            strategy: strategy.to_string(),
            raw_output: None,
            steps: Vec::new(),
            final_message: None,
            timestamp: Local::now(),
        }
    }

    /// Set the raw output from the agent.
    pub fn set_raw_output(&mut self, output: &str) {
        const MAX_OUTPUT_SIZE: usize = 50_000;
        self.raw_output = if output.len() > MAX_OUTPUT_SIZE {
            Some(format!(
                "{}\n\n[... raw output truncated {} bytes ...]\n\n{}",
                &output[..MAX_OUTPUT_SIZE / 2],
                output.len() - MAX_OUTPUT_SIZE,
                &output[output.len() - MAX_OUTPUT_SIZE / 2..]
            ))
        } else {
            Some(output.to_string())
        };
    }

    /// Add a parsing step to the trace.
    #[must_use]
    pub fn add_step(mut self, step: ParsingTraceStep) -> Self {
        self.steps = self.steps.into_iter().chain([step]).collect();
        self
    }

    /// Set the final extracted message.
    pub fn set_final_message(&mut self, message: &str) {
        self.final_message = Some(message.to_string());
    }

    /// Write this trace to a file using workspace abstraction.
    ///
    /// This is the architecture-conformant version that uses the workspace trait
    /// instead of direct filesystem access.
    ///
    /// # Arguments
    ///
    /// * `log_dir` - Directory to write the trace file to (relative to workspace)
    /// * `workspace` - The workspace to use for filesystem operations
    ///
    /// # Returns
    ///
    /// Path to the written trace file on success.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn write_to_workspace(
        &self,
        log_dir: &Path,
        workspace: &dyn Workspace,
    ) -> std::io::Result<PathBuf> {
        let trace_path = log_dir.join(format!(
            "attempt_{:03}_parsing_trace.log",
            self.attempt_number
        ));

        // Build the content using string concatenation
        let content = Self::build_trace_content(self);

        // Write using workspace
        workspace.create_dir_all(log_dir)?;
        workspace.write(&trace_path, &content)?;

        Ok(trace_path)
    }

    /// Build the complete trace content as a string.
    #[must_use]
    fn build_trace_content(trace: &Self) -> String {
        [
            Self::header_to_string(trace).as_str(),
            &Self::raw_output_to_string(trace),
            &Self::parsing_steps_to_string(trace),
            &Self::final_message_to_string(trace),
            Self::footer_to_string().as_str(),
        ]
        .concat()
    }

    /// Convert header to string.
    #[must_use]
    fn header_to_string(trace: &Self) -> String {
        format!(
            "\
================================================================================
PARSING TRACE LOG - Attempt #{:03}
================================================================================

Agent:     {}
Strategy:  {}
Timestamp: {}

",
            trace.attempt_number,
            trace.agent,
            trace.strategy,
            trace.timestamp.format("%Y-%m-%d %H:%M:%S %Z")
        )
    }

    /// Convert raw output to string.
    #[must_use]
    fn raw_output_to_string(trace: &Self) -> String {
        format!(
            "\
--------------------------------------------------------------------------------
RAW AGENT OUTPUT
--------------------------------------------------------------------------------

{}

",
            trace.raw_output.as_deref().unwrap_or("[No raw output captured]")
        )
    }

    /// Convert parsing steps to string.
    #[must_use]
    fn parsing_steps_to_string(trace: &Self) -> String {
        if trace.steps.is_empty() {
            return "\
--------------------------------------------------------------------------------
PARSING STEPS
--------------------------------------------------------------------------------

[No parsing steps recorded]

"
            .to_string();
        }

        let steps_content: String = trace
            .steps
            .iter()
            .map(|step| {
                let status = if step.success { "✓ SUCCESS" } else { "✗ FAILED" };
                let step_str = format!("{}. {} [{}]\n", step.step_number, step.description, status);

                let input_str = step
                    .input
                    .as_ref()
                    .map(|input| {
                        input
                            .lines()
                            .map(|line| format!("   INPUT:\n   {}\n", line))
                            .collect::<String>()
                    })
                    .unwrap_or_default();

                let result_str = step
                    .result
                    .as_ref()
                    .map(|result| {
                        result
                            .lines()
                            .map(|line| format!("   RESULT:\n   {}\n", line))
                            .collect::<String>()
                    })
                    .unwrap_or_default();

                let details_str = if !step.details.is_empty() {
                    format!("   DETAILS: {}\n", step.details)
                } else {
                    String::new()
                };

                format!("{}{}{}{}", step_str, input_str, result_str, details_str)
            })
            .collect();

        format!(
            "\
--------------------------------------------------------------------------------
PARSING STEPS
--------------------------------------------------------------------------------

{}
",
            steps_content
        )
    }

    /// Convert final message to string.
    #[must_use]
    fn final_message_to_string(trace: &Self) -> String {
        format!(
            "\
--------------------------------------------------------------------------------
FINAL EXTRACTED MESSAGE
--------------------------------------------------------------------------------

{}

",
            trace.final_message.as_deref().unwrap_or("[No message extracted]")
        )
    }

    /// Convert footer to string.
    #[must_use]
    fn footer_to_string() -> String {
        "\
================================================================================
END OF TRACE LOG
================================================================================
"
        .to_string()
    }
}

/// Represents an extraction attempt with its method and outcome.
#[derive(Debug, Clone)]
pub struct ExtractionAttempt {
    /// Name of the extraction method (e.g., "XML", "JSON", "Salvage")
    pub method: &'static str,
    /// Whether this method succeeded
    pub success: bool,
    /// Detailed reason/description of what happened
    pub detail: String,
}

impl ExtractionAttempt {
    /// Create a successful extraction attempt.
    #[must_use] 
    pub const fn success(method: &'static str, detail: String) -> Self {
        Self {
            method,
            success: true,
            detail,
        }
    }

    /// Create a failed extraction attempt.
    #[must_use] 
    pub const fn failure(method: &'static str, detail: String) -> Self {
        Self {
            method,
            success: false,
            detail,
        }
    }
}

/// Represents a single validation check result.
#[derive(Debug, Clone)]
pub struct ValidationCheck {
    /// Name of the validation check
    pub name: &'static str,
    /// Whether this check passed
    pub passed: bool,
    /// Error message if check failed
    pub error: Option<String>,
}

impl ValidationCheck {
    /// Create a passing validation check.
    #[cfg(test)]
    #[must_use] 
    pub const fn pass(name: &'static str) -> Self {
        Self {
            name,
            passed: true,
            error: None,
        }
    }

    /// Create a failing validation check.
    #[cfg(test)]
    #[must_use] 
    pub const fn fail(name: &'static str, error: String) -> Self {
        Self {
            name,
            passed: false,
            error: Some(error),
        }
    }
}

/// Outcome of a commit generation attempt.
#[derive(Debug, Clone)]
pub enum AttemptOutcome {
    /// Successfully extracted a valid commit message
    Success(String),
    /// XSD validation failed with specific error message
    XsdValidationFailed(String),
    /// Extraction failed entirely
    ExtractionFailed(String),
}

impl std::fmt::Display for AttemptOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Success(msg) => write!(f, "SUCCESS: {}", preview_message(msg)),
            Self::XsdValidationFailed(err) => write!(f, "XSD_VALIDATION_FAILED: {err}"),
            Self::ExtractionFailed(err) => write!(f, "EXTRACTION_FAILED: {err}"),
        }
    }
}

/// Preview a message, truncating if too long.
///
/// Uses character-based truncation to avoid panics on UTF-8 multi-byte characters.
fn preview_message(msg: &str) -> String {
    let first_line = msg.lines().next().unwrap_or(msg);
    // truncate_text handles the ellipsis, so we use 63 to get ~60 chars + "..."
    truncate_text(first_line, 63)
}
