//! Review phase validation checks.
//!
//! This module contains pre-flight and post-flight validation logic for the review phase.
//! These checks verify that the environment is suitable for running the review agent
//! and help diagnose issues early.

use crate::agents::contains_glm_model;
use crate::common::domain_types::{AgentDirectoryEntryCount, AgentName, IssueFileSize, ModelName};
use crate::review_metrics::ReviewMetrics;
use crate::workspace::Workspace;
use std::path::Path;

/// Maximum number of files in .agent directory before warning about cleanup.
const MAX_AGENT_DIR_ENTRY_COUNT: usize = 1000;

/// Result of pre-flight validation
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PreflightResult {
    /// All checks passed
    Ok,
    /// Warning issued but can proceed
    Warning(String),
    /// Critical error that should halt execution
    Error(String),
}

/// Diagnostics emitted by [`pre_flight_review_check`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PreflightDiagnostic {
    ProblematicReviewer {
        agent: AgentName,
        model: Option<ModelName>,
    },
    GlmAgentDetected {
        agent: AgentName,
    },
    ExistingIssuesFile {
        size_bytes: IssueFileSize,
    },
    EmptyIssuesFile,
    IssuesFileReadFailure {
        error: String,
    },
    AgentDirectoryTooLarge {
        entry_count: AgentDirectoryEntryCount,
    },
    AgentDirectoryCreationFailed {
        error: String,
    },
    AgentDirectoryNotWritable {
        error: String,
    },
}

/// Wrapper that pairs a domain value with diagnostics that explain non-fatal decisions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WithDiagnostics<T, D> {
    pub value: T,
    pub diagnostics: Vec<D>,
}

impl<T, D> WithDiagnostics<T, D> {
    pub fn new(value: T) -> Self {
        Self {
            value,
            diagnostics: Vec::new(),
        }
    }
}

/// Result of post-flight validation
#[derive(Debug)]
pub enum PostflightResult {
    /// ISSUES.md found and valid
    Valid,
    /// ISSUES.md missing or empty
    Missing(String),
    /// ISSUES.md has unexpected format
    Malformed(String),
}

/// Run pre-flight validation checks before starting a review pass.
///
/// These checks verify that the environment is suitable for running
/// the review agent and return diagnostics that explain warnings or adjustments
/// instead of logging directly.
///
/// Uses workspace abstraction for file operations, enabling testing with
/// `MemoryWorkspace`.
pub fn pre_flight_review_check(
    workspace: &dyn Workspace,
    cycle: u32,
    reviewer_agent: &str,
    reviewer_model: Option<&str>,
) -> WithDiagnostics<PreflightResult, PreflightDiagnostic> {
    let agent_dir = Path::new(".agent");
    let issues_path = Path::new(".agent/ISSUES.md");

    // Each check returns Ok(non_fatal_diagnostics) to continue, or
    // Err(terminal_result_with_diagnostics) to halt.
    // We thread these checks, accumulating diagnostics along the way.

    // Check 0: Agent compatibility warning (non-blocking)
    let agent_diagnostics: Vec<PreflightDiagnostic> =
        check_agent_compatibility(reviewer_agent, reviewer_model);

    // Check 0.5: Existing ISSUES.md diagnostic (non-blocking)
    let issues_diagnostics: Vec<PreflightDiagnostic> =
        check_existing_issues_file(workspace, issues_path);

    // Accumulated non-fatal diagnostics so far.
    let preamble_diagnostics: Vec<PreflightDiagnostic> = agent_diagnostics
        .into_iter()
        .chain(issues_diagnostics)
        .collect();

    // Check 1: .agent directory must exist and be writable (may halt).
    check_agent_dir_writable(workspace, agent_dir, cycle, preamble_diagnostics)
        .and_then(|diagnostics_after_write| {
            // Check 2: .agent directory must not be too large (may halt).
            check_agent_dir_size(workspace, agent_dir, diagnostics_after_write)
        })
        .unwrap_or_else(|terminal| terminal)
}

/// Check 0 + 0.1: agent/model compatibility warnings — always non-fatal.
fn check_agent_compatibility(
    reviewer_agent: &str,
    reviewer_model: Option<&str>,
) -> Vec<PreflightDiagnostic> {
    let problematic: Option<PreflightDiagnostic> =
        is_problematic_prompt_target(reviewer_agent, reviewer_model).then(|| {
            PreflightDiagnostic::ProblematicReviewer {
                agent: AgentName::from(reviewer_agent),
                model: reviewer_model.map(ModelName::from),
            }
        });

    let glm: Option<PreflightDiagnostic> =
        contains_glm_model(reviewer_agent).then(|| PreflightDiagnostic::GlmAgentDetected {
            agent: AgentName::from(reviewer_agent),
        });

    problematic.into_iter().chain(glm).collect()
}

/// Check 0.5: existing ISSUES.md — always non-fatal.
fn check_existing_issues_file(
    workspace: &dyn Workspace,
    issues_path: &Path,
) -> Vec<PreflightDiagnostic> {
    if !workspace.exists(issues_path) {
        return vec![];
    }
    match workspace.read(issues_path) {
        Ok(content) if !content.is_empty() => vec![PreflightDiagnostic::ExistingIssuesFile {
            size_bytes: IssueFileSize::from(content.len()),
        }],
        Ok(_) => vec![PreflightDiagnostic::EmptyIssuesFile],
        Err(e) => vec![PreflightDiagnostic::IssuesFileReadFailure {
            error: e.to_string(),
        }],
    }
}

/// Check 1: ensure .agent exists and is writable.
///
/// Returns `Ok(diagnostics)` when the directory is ready, or
/// `Err(terminal)` when the run must halt.
fn check_agent_dir_writable(
    workspace: &dyn Workspace,
    agent_dir: &Path,
    cycle: u32,
    prior_diagnostics: Vec<PreflightDiagnostic>,
) -> Result<Vec<PreflightDiagnostic>, WithDiagnostics<PreflightResult, PreflightDiagnostic>> {
    // Ensure the directory exists.
    let dir_created_diagnostics: Vec<PreflightDiagnostic> = if !workspace.is_dir(agent_dir) {
        match workspace.create_dir_all(agent_dir) {
            Ok(()) => vec![],
            Err(e) => {
                let error_message = e.to_string();
                return Err(WithDiagnostics {
                    value: PreflightResult::Error(format!(
                        "Cannot create .agent directory: {error_message}. Check directory permissions."
                    )),
                    diagnostics: prior_diagnostics
                        .into_iter()
                        .chain(std::iter::once(
                            PreflightDiagnostic::AgentDirectoryCreationFailed {
                                error: error_message,
                            },
                        ))
                        .collect(),
                });
            }
        }
    } else {
        vec![]
    };

    // Test write by touching a temp file.
    let test_file = agent_dir.join(format!(".write_test_{cycle}"));
    match workspace.write(&test_file, "test") {
        Ok(()) => {
            let _ = workspace.remove(&test_file);
            Ok(prior_diagnostics
                .into_iter()
                .chain(dir_created_diagnostics)
                .collect())
        }
        Err(e) => {
            let error_message = e.to_string();
            Err(WithDiagnostics {
                value: PreflightResult::Error(format!(
                    ".agent directory is not writable: {error_message}. Check file permissions."
                )),
                diagnostics: prior_diagnostics
                    .into_iter()
                    .chain(dir_created_diagnostics)
                    .chain(std::iter::once(
                        PreflightDiagnostic::AgentDirectoryNotWritable {
                            error: error_message,
                        },
                    ))
                    .collect(),
            })
        }
    }
}

/// Check 2: ensure .agent directory is not too large.
///
/// Returns `Ok(diagnostics)` when the directory size is acceptable, or
/// `Err(terminal)` when the run should halt with a warning.
fn check_agent_dir_size(
    workspace: &dyn Workspace,
    agent_dir: &Path,
    prior_diagnostics: Vec<PreflightDiagnostic>,
) -> Result<
    WithDiagnostics<PreflightResult, PreflightDiagnostic>,
    WithDiagnostics<PreflightResult, PreflightDiagnostic>,
> {
    let too_large: Option<(usize, PreflightDiagnostic)> = workspace
        .read_dir(agent_dir)
        .ok()
        .filter(|entries| entries.len() > MAX_AGENT_DIR_ENTRY_COUNT)
        .map(|entries| {
            let count = entries.len();
            (
                count,
                PreflightDiagnostic::AgentDirectoryTooLarge {
                    entry_count: AgentDirectoryEntryCount::from(count),
                },
            )
        });

    match too_large {
        Some((_, diag)) => Err(WithDiagnostics {
            value: PreflightResult::Warning(
                "Large .agent directory detected. Review may be slow.".to_string(),
            ),
            diagnostics: prior_diagnostics
                .into_iter()
                .chain(std::iter::once(diag))
                .collect(),
        }),
        None => Ok(WithDiagnostics {
            value: PreflightResult::Ok,
            diagnostics: prior_diagnostics,
        }),
    }
}

/// Run post-flight validation after a review pass completes.
///
/// These checks verify that the review agent produced expected output.
///
/// Uses workspace abstraction for file operations, enabling testing with
/// `MemoryWorkspace`.
pub fn post_flight_review_check(
    workspace: &dyn Workspace,
    logger: &crate::logger::Logger,
    cycle: u32,
) -> PostflightResult {
    let issues_path = Path::new(".agent/ISSUES.md");

    // Check 1: Verify ISSUES.md exists
    if !workspace.exists(issues_path) {
        logger.warn(&format!(
            "Review cycle {cycle} completed but ISSUES.md was not created. \
             The agent may have failed or used a different output format."
        ));
        logger.info("Possible causes:");
        logger.info("  - Agent failed to write the file (permission/execution error)");
        logger.info("  - Agent used a different output filename or format");
        logger.info("  - Agent was interrupted during execution");
        return PostflightResult::Missing(
            "ISSUES.md not found after review. Agent may have failed.".to_string(),
        );
    }

    // Check 2: Verify ISSUES.md is not empty and log its size
    let file_size = match workspace.read(issues_path) {
        Ok(content) if content.is_empty() => {
            logger.warn(&format!("Review cycle {cycle} created an empty ISSUES.md."));
            logger.info("Possible causes:");
            logger.info("  - Agent reviewed but found no issues (should write 'No issues found.')");
            logger.info("  - Agent failed during file write");
            logger.info("  - Agent doesn't understand the expected output format");
            return PostflightResult::Missing("ISSUES.md is empty".to_string());
        }
        Ok(content) => {
            // Log the file size for debugging
            let size = content.len() as u64;
            logger.info(&format!("ISSUES.md created ({size} bytes)"));
            size
        }
        Err(e) => {
            logger.warn(&format!("Cannot read ISSUES.md: {e}"));
            return PostflightResult::Missing(format!("Cannot read ISSUES.md: {e}"));
        }
    };

    // Check 3: Verify ISSUES.md has valid structure
    match ReviewMetrics::from_issues_file_with_workspace(workspace) {
        Ok(metrics) => {
            // Track whether metrics were successfully parsed from the file
            let _parsed = metrics.issues_file_found;
            // Check if metrics indicate reasonable content
            if metrics.total_issues == 0 && !metrics.no_issues_declared {
                // Partial recovery: file has content but no parseable issues
                logger.warn(&format!(
                    "Review cycle {cycle} produced ISSUES.md ({file_size} bytes) but no parseable issues detected."
                ));
                logger.info("Content may be in unexpected format. The fix pass may still work.");
                logger.info(
                    "Consider checking .agent/ISSUES.md manually to see what the agent wrote.",
                );
                return PostflightResult::Malformed(
                    "ISSUES.md exists but no issues detected. Check format.".to_string(),
                );
            }

            // Log a summary of what was found
            if metrics.total_issues > 0 {
                logger.info(&format!(
                    "Review found {} issues ({} critical, {} high, {} medium, {} low)",
                    metrics.total_issues,
                    metrics.critical_issues,
                    metrics.high_issues,
                    metrics.medium_issues,
                    metrics.low_issues
                ));
                if metrics.resolved_issues > 0 {
                    logger.info(&format!(
                        "  {} issues already resolved",
                        metrics.resolved_issues
                    ));
                }
            } else if metrics.no_issues_declared {
                logger.info("Review declared no issues found.");
            }

            PostflightResult::Valid
        }
        Err(e) => {
            // Partial recovery: attempt to show what content we can
            logger.warn(&format!("Failed to parse ISSUES.md: {e}"));
            logger.info(&format!(
                "ISSUES.md has {file_size} bytes but failed to parse."
            ));
            logger.info("The file may be malformed or in an unexpected format.");
            logger.info(
                "Attempting partial recovery: fix pass will proceed but may have limited success.",
            );

            // Try to read first few lines to give user a hint
            if let Ok(content) = workspace.read(issues_path) {
                let preview: String = content.lines().take(5).collect::<Vec<_>>().join("\n");
                if !preview.is_empty() {
                    logger.info("ISSUES.md preview (first 5 lines):");
                    preview.lines().for_each(|line| {
                        logger.info(&format!("  {line}"));
                    });
                }
            }

            PostflightResult::Malformed(format!("Failed to parse ISSUES.md: {e}"))
        }
    }
}

/// Check if the given agent/model combination is a problematic prompt target.
///
/// Certain AI agents have known compatibility issues with complex structured prompts.
/// This function detects those agents for which alternative handling may be needed.
fn is_problematic_prompt_target(agent: &str, model_flag: Option<&str>) -> bool {
    contains_glm_model(agent) || model_flag.is_some_and(contains_glm_model)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::domain_types::{AgentName, IssueFileSize, ModelName};
    use crate::workspace::{DirEntry, MemoryWorkspace, Workspace};
    use std::io;
    use std::path::{Path, PathBuf};

    // -------------------------------------------------------------------------
    // Workspace stubs for failure injection
    // -------------------------------------------------------------------------

    /// Workspace that reports `.agent` as non-existent and fails `create_dir_all`,
    /// used to exercise the `AgentDirectoryCreationFailed` diagnostic path.
    struct AgentDirCreationFailingWorkspace {
        inner: MemoryWorkspace,
    }

    impl AgentDirCreationFailingWorkspace {
        fn new() -> Self {
            Self {
                inner: MemoryWorkspace::new_test(),
            }
        }
    }

    impl Workspace for AgentDirCreationFailingWorkspace {
        fn root(&self) -> &Path {
            self.inner.root()
        }

        fn read(&self, relative: &Path) -> io::Result<String> {
            self.inner.read(relative)
        }

        fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
            self.inner.read_bytes(relative)
        }

        fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write(relative, content)
        }

        fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.write_bytes(relative, content)
        }

        fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.append_bytes(relative, content)
        }

        fn exists(&self, relative: &Path) -> bool {
            self.inner.exists(relative)
        }

        fn is_file(&self, relative: &Path) -> bool {
            self.inner.is_file(relative)
        }

        /// Always reports `.agent` as absent so the creation path is triggered.
        fn is_dir(&self, _relative: &Path) -> bool {
            false
        }

        fn remove(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove(relative)
        }

        fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_if_exists(relative)
        }

        fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all(relative)
        }

        fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all_if_exists(relative)
        }

        /// Always fails so `AgentDirectoryCreationFailed` is emitted.
        fn create_dir_all(&self, _relative: &Path) -> io::Result<()> {
            Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "simulated: cannot create .agent directory",
            ))
        }

        fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
            self.inner.read_dir(relative)
        }

        fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
            self.inner.rename(from, to)
        }

        fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write_atomic(relative, content)
        }

        fn set_readonly(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_readonly(relative)
        }

        fn set_writable(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_writable(relative)
        }
    }

    /// Workspace where `.agent` appears to exist but any write into it fails,
    /// used to exercise the `AgentDirectoryNotWritable` diagnostic path.
    struct AgentDirWriteFailingWorkspace {
        inner: MemoryWorkspace,
    }

    impl AgentDirWriteFailingWorkspace {
        fn new() -> Self {
            Self {
                inner: MemoryWorkspace::new_test().with_dir(".agent"),
            }
        }
    }

    impl Workspace for AgentDirWriteFailingWorkspace {
        fn root(&self) -> &Path {
            self.inner.root()
        }

        fn read(&self, relative: &Path) -> io::Result<String> {
            self.inner.read(relative)
        }

        fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
            self.inner.read_bytes(relative)
        }

        /// Fails for every path to simulate a read-only `.agent` directory.
        fn write(&self, _relative: &Path, _content: &str) -> io::Result<()> {
            Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "simulated: .agent directory is not writable",
            ))
        }

        fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.write_bytes(relative, content)
        }

        fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.append_bytes(relative, content)
        }

        fn exists(&self, relative: &Path) -> bool {
            self.inner.exists(relative)
        }

        fn is_file(&self, relative: &Path) -> bool {
            self.inner.is_file(relative)
        }

        fn is_dir(&self, relative: &Path) -> bool {
            self.inner.is_dir(relative)
        }

        fn remove(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove(relative)
        }

        fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_if_exists(relative)
        }

        fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all(relative)
        }

        fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all_if_exists(relative)
        }

        fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.create_dir_all(relative)
        }

        fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
            self.inner.read_dir(relative)
        }

        fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
            self.inner.rename(from, to)
        }

        fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write_atomic(relative, content)
        }

        fn set_readonly(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_readonly(relative)
        }

        fn set_writable(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_writable(relative)
        }
    }

    /// Workspace where a specific path appears to exist (via `exists`) but fails to read,
    /// used to exercise the `IssuesFileReadFailure` diagnostic path.
    struct IssuesFileReadFailingWorkspace {
        inner: MemoryWorkspace,
        failing_path: PathBuf,
    }

    impl IssuesFileReadFailingWorkspace {
        fn new(failing_path: &str) -> Self {
            Self {
                inner: MemoryWorkspace::new_test().with_dir(".agent"),
                failing_path: PathBuf::from(failing_path),
            }
        }
    }

    impl Workspace for IssuesFileReadFailingWorkspace {
        fn root(&self) -> &Path {
            self.inner.root()
        }

        /// Returns `Err` for the configured path to simulate a read failure.
        fn read(&self, relative: &Path) -> io::Result<String> {
            if relative == self.failing_path.as_path() {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "simulated: cannot read ISSUES.md",
                ));
            }
            self.inner.read(relative)
        }

        fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
            self.inner.read_bytes(relative)
        }

        fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write(relative, content)
        }

        fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.write_bytes(relative, content)
        }

        fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.append_bytes(relative, content)
        }

        /// Reports the configured path as existing even though reads fail.
        fn exists(&self, relative: &Path) -> bool {
            relative == self.failing_path.as_path() || self.inner.exists(relative)
        }

        fn is_file(&self, relative: &Path) -> bool {
            self.inner.is_file(relative)
        }

        fn is_dir(&self, relative: &Path) -> bool {
            self.inner.is_dir(relative)
        }

        fn remove(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove(relative)
        }

        fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_if_exists(relative)
        }

        fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all(relative)
        }

        fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all_if_exists(relative)
        }

        fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.create_dir_all(relative)
        }

        fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
            self.inner.read_dir(relative)
        }

        fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
            self.inner.rename(from, to)
        }

        fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write_atomic(relative, content)
        }

        fn set_readonly(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_readonly(relative)
        }

        fn set_writable(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_writable(relative)
        }
    }

    #[test]
    fn preflight_problematic_reviewer_carries_agent_name_newtype() {
        let workspace = MemoryWorkspace::new_test();
        let outcome = pre_flight_review_check(&workspace, 1, "glm-4", Some("glm-4-flash"));

        let found = outcome.diagnostics.iter().find_map(|diag| match diag {
            PreflightDiagnostic::ProblematicReviewer { agent, model } => Some((agent, model)),
            _ => None,
        });
        let (agent, model) = found.expect("ProblematicReviewer diagnostic expected");
        assert_eq!(agent, &AgentName::from("glm-4"));
        assert_eq!(model.as_ref(), Some(&ModelName::from("glm-4-flash")));
    }

    #[test]
    fn preflight_glm_agent_detected_carries_agent_name_newtype() {
        let workspace = MemoryWorkspace::new_test();
        let outcome = pre_flight_review_check(&workspace, 1, "glm-4", None);

        let found = outcome.diagnostics.iter().find_map(|diag| match diag {
            PreflightDiagnostic::GlmAgentDetected { agent } => Some(agent),
            _ => None,
        });
        let agent = found.expect("GlmAgentDetected diagnostic expected");
        assert_eq!(agent, &AgentName::from("glm-4"));
    }

    #[test]
    fn preflight_reports_existing_issues_diagnostic() {
        let content = "previous ISSUES.md contents";
        let workspace = MemoryWorkspace::new_test().with_file(".agent/ISSUES.md", content);

        let outcome = pre_flight_review_check(&workspace, 1, "reviewer", None);

        assert_eq!(outcome.value, PreflightResult::Ok);
        assert!(outcome.diagnostics.iter().any(|diag| matches!(
            diag,
            PreflightDiagnostic::ExistingIssuesFile { size_bytes } if *size_bytes == IssueFileSize::from(content.len())
        )));
    }

    #[test]
    fn preflight_warns_on_large_agent_directory() {
        let workspace = (0..=MAX_AGENT_DIR_ENTRY_COUNT)
            .fold(MemoryWorkspace::new_test(), |workspace, index| {
                workspace.with_file(&format!(".agent/extra_{index}.log"), "x")
            });

        let outcome = pre_flight_review_check(&workspace, 2, "reviewer", None);

        assert!(matches!(
            outcome.value,
            PreflightResult::Warning(message)
            if message == "Large .agent directory detected. Review may be slow."
        ));
        assert!(outcome.diagnostics.iter().any(|diag| matches!(
            diag,
            PreflightDiagnostic::AgentDirectoryTooLarge { entry_count }
            if entry_count.as_count() > MAX_AGENT_DIR_ENTRY_COUNT
        )));
    }

    // -------------------------------------------------------------------------
    // P12-diagnostics: `.value` shape and empty-diagnostics coverage
    // -------------------------------------------------------------------------

    /// Fully-valid input (non-problematic reviewer, no existing ISSUES.md,
    /// writable `.agent` directory) must produce `PreflightResult::Ok` with
    /// an empty diagnostics list.
    #[test]
    fn preflight_clean_env_produces_ok_with_empty_diagnostics() {
        let workspace = MemoryWorkspace::new_test().with_dir(".agent");

        let outcome = pre_flight_review_check(&workspace, 1, "claude", None);

        assert_eq!(
            outcome.value,
            PreflightResult::Ok,
            "expected Ok for fully-valid input"
        );
        assert!(
            outcome.diagnostics.is_empty(),
            "expected no diagnostics for fully-valid input, got: {:?}",
            outcome.diagnostics
        );
    }

    /// An empty ISSUES.md from a previous run must produce the `EmptyIssuesFile`
    /// diagnostic while still allowing the check to proceed (`Ok` result).
    #[test]
    fn preflight_empty_issues_file_produces_empty_issues_file_diagnostic() {
        let workspace = MemoryWorkspace::new_test().with_file(".agent/ISSUES.md", "");

        let outcome = pre_flight_review_check(&workspace, 1, "reviewer", None);

        assert_eq!(
            outcome.value,
            PreflightResult::Ok,
            "an empty ISSUES.md should not block the preflight"
        );
        assert!(
            outcome
                .diagnostics
                .iter()
                .any(|d| matches!(d, PreflightDiagnostic::EmptyIssuesFile)),
            "expected EmptyIssuesFile diagnostic, got: {:?}",
            outcome.diagnostics
        );
    }

    /// When ISSUES.md is reported as present but its read fails, the check must
    /// emit an `IssuesFileReadFailure` diagnostic with the underlying error message.
    #[test]
    fn preflight_issues_file_read_failure_produces_read_failure_diagnostic() {
        let workspace = IssuesFileReadFailingWorkspace::new(".agent/ISSUES.md");

        let outcome = pre_flight_review_check(&workspace, 1, "reviewer", None);

        let failure_msg = outcome.diagnostics.iter().find_map(|d| match d {
            PreflightDiagnostic::IssuesFileReadFailure { error } => Some(error.as_str()),
            _ => None,
        });
        assert!(
            failure_msg.is_some(),
            "expected IssuesFileReadFailure diagnostic, got: {:?}",
            outcome.diagnostics
        );
        assert!(
            failure_msg.unwrap().contains("simulated"),
            "diagnostic error message should contain the underlying cause"
        );
    }

    /// When the `.agent` directory cannot be created, the check must:
    /// - return `PreflightResult::Error` (not proceed),
    /// - include an `AgentDirectoryCreationFailed` diagnostic with the error message.
    #[test]
    fn preflight_agent_directory_creation_failed_produces_error_and_diagnostic() {
        let workspace = AgentDirCreationFailingWorkspace::new();

        let outcome = pre_flight_review_check(&workspace, 1, "reviewer", None);

        assert!(
            matches!(outcome.value, PreflightResult::Error(_)),
            "expected Error when .agent cannot be created, got: {:?}",
            outcome.value
        );
        let creation_error = outcome.diagnostics.iter().find_map(|d| match d {
            PreflightDiagnostic::AgentDirectoryCreationFailed { error } => Some(error.as_str()),
            _ => None,
        });
        assert!(
            creation_error.is_some(),
            "expected AgentDirectoryCreationFailed diagnostic, got: {:?}",
            outcome.diagnostics
        );
        assert!(
            creation_error.unwrap().contains("simulated"),
            "diagnostic should carry the underlying error message"
        );
    }

    /// When `.agent` exists but writing into it fails, the check must:
    /// - return `PreflightResult::Error`,
    /// - include an `AgentDirectoryNotWritable` diagnostic with the error message.
    #[test]
    fn preflight_agent_directory_not_writable_produces_error_and_diagnostic() {
        let workspace = AgentDirWriteFailingWorkspace::new();

        let outcome = pre_flight_review_check(&workspace, 1, "reviewer", None);

        assert!(
            matches!(outcome.value, PreflightResult::Error(_)),
            "expected Error when .agent is not writable, got: {:?}",
            outcome.value
        );
        let write_error = outcome.diagnostics.iter().find_map(|d| match d {
            PreflightDiagnostic::AgentDirectoryNotWritable { error } => Some(error.as_str()),
            _ => None,
        });
        assert!(
            write_error.is_some(),
            "expected AgentDirectoryNotWritable diagnostic, got: {:?}",
            outcome.diagnostics
        );
        assert!(
            write_error.unwrap().contains("simulated"),
            "diagnostic should carry the underlying error message"
        );
    }
}
