//! Workspace filesystem abstraction for explicit path resolution.
//!
//! This module provides the [`Workspace`] trait and implementations that eliminate
//! CWD dependencies by making all path operations explicit relative to the repository root.
//!
//! # Problem
//!
//! The codebase previously relied on `std::env::set_current_dir()` to set the
//! process CWD to the repository root, then used relative paths (`.agent/`,
//! `PROMPT.md`, etc.) throughout. This caused:
//!
//! - Test flakiness when tests ran in parallel (CWD is process-global)
//! - Background thread bugs when CWD changed after thread started
//! - Poor testability without complex CWD manipulation
//!
//! # Solution
//!
//! The [`Workspace`] trait defines the interface for file operations, with two implementations:
//!
//! - [`WorkspaceFs`] - Production implementation using the real filesystem
//! - `MemoryWorkspace` - Test implementation with in-memory storage (available with `test-utils` feature)
//!
//! # Well-Known Paths
//!
//! This module defines constants for all Ralph artifact paths:
//!
//! - [`AGENT_DIR`] - `.agent/` directory
//! - [`PLAN_MD`] - `.agent/PLAN.md`
//! - [`ISSUES_MD`] - `.agent/ISSUES.md`
//! - [`PROMPT_MD`] - `PROMPT.md` (repository root)
//! - [`CHECKPOINT_JSON`] - `.agent/checkpoint.json`
//!
//! The [`Workspace`] trait provides convenience methods for these paths (e.g., [`Workspace::plan_md`]).
//!
//! # Production Example
//!
//! ```ignore
//! use ralph_workflow::workspace::WorkspaceFs;
//! use std::path::PathBuf;
//!
//! let ws = WorkspaceFs::new(PathBuf::from("/path/to/repo"));
//!
//! // Get paths to well-known files
//! let plan = ws.plan_md();  // /path/to/repo/.agent/PLAN.md
//! let prompt = ws.prompt_md();  // /path/to/repo/PROMPT.md
//!
//! // Perform file operations
//! ws.write(Path::new(".agent/test.txt"), "content")?;
//! let content = ws.read(Path::new(".agent/test.txt"))?;
//! ```
//!
//! # Testing with `MemoryWorkspace`
//!
//! The `test-utils` feature enables `MemoryWorkspace` for integration tests:
//!
//! ```ignore
//! use ralph_workflow::workspace::{MemoryWorkspace, Workspace};
//! use std::path::Path;
//!
//! // Create a test workspace with pre-populated files
//! let ws = MemoryWorkspace::new_test()
//!     .with_file("PROMPT.md", "# Task: Add logging")
//!     .with_file(".agent/PLAN.md", "1. Add log statements");
//!
//! // Verify file operations
//! assert!(ws.exists(Path::new("PROMPT.md")));
//! assert_eq!(ws.read(Path::new("PROMPT.md"))?, "# Task: Add logging");
//!
//! // Write and verify
//! ws.write(Path::new(".agent/output.txt"), "result")?;
//! assert!(ws.was_written(".agent/output.txt"));
//! ```
//!
//! # See Also
//!
//! - [`crate::executor::ProcessExecutor`] - Similar abstraction for process execution

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

mod validation_error;
pub use validation_error::{ErrorCode, ValidationError};

// ============================================================================
// Well-known path constants
// ============================================================================

include!("workspace/paths.rs");

// ============================================================================
// DirEntry - abstraction for directory entries
// ============================================================================

include!("workspace/dir_entry.rs");

// ============================================================================
// Artifact Envelope
// ============================================================================

/// JSON artifact envelope for broker-owned persistence.
///
/// Wraps artifact content with metadata for the MCP artifact submission flow.
/// Stored in `.agent/tmp/{artifact_type}.json`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ArtifactEnvelope {
    /// The artifact type (e.g., "plan", "development_result", "issues").
    pub artifact_type: String,
    /// Schema version for forward compatibility.
    pub version: String,
    /// The validated artifact content as a JSON value.
    pub content: serde_json::Value,
    /// ISO 8601 timestamp when the artifact was validated.
    pub validated_at: String,
    /// Whether this is a partial (incomplete) artifact submission.
    #[serde(default)]
    pub partial: bool,
    /// Validation errors present in a partial submission.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub errors: Vec<ValidationError>,
}

impl ArtifactEnvelope {
    /// Create a new complete artifact envelope.
    pub fn new(
        artifact_type: impl Into<String>,
        content: serde_json::Value,
        validated_at: impl Into<String>,
    ) -> Self {
        Self {
            artifact_type: artifact_type.into(),
            version: "1.0".to_string(),
            content,
            validated_at: validated_at.into(),
            partial: false,
            errors: Vec::new(),
        }
    }

    /// Create a new partial artifact envelope with validation errors.
    pub fn new_partial(
        artifact_type: impl Into<String>,
        content: serde_json::Value,
        validated_at: impl Into<String>,
        errors: Vec<ValidationError>,
    ) -> Self {
        Self {
            artifact_type: artifact_type.into(),
            version: "1.0".to_string(),
            content,
            validated_at: validated_at.into(),
            partial: true,
            errors,
        }
    }
}

// ============================================================================
// Workspace Trait
// ============================================================================

/// Trait defining the workspace filesystem interface.
///
/// This trait abstracts file operations relative to a repository root, allowing
/// for both real filesystem access (production) and in-memory storage (testing).
pub trait Workspace: Send + Sync {
    /// Get the repository root path.
    fn root(&self) -> &Path;

    // =========================================================================
    // File operations
    // =========================================================================

    /// Read a file relative to the repository root.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn read(&self, relative: &Path) -> std::io::Result<String>;

    /// Read a file as bytes relative to the repository root.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn read_bytes(&self, relative: &Path) -> std::io::Result<Vec<u8>>;

    /// Write content to a file relative to the repository root.
    /// Creates parent directories if they don't exist.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn write(&self, relative: &Path, content: &str) -> std::io::Result<()>;

    /// Write bytes to a file relative to the repository root.
    /// Creates parent directories if they don't exist.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn write_bytes(&self, relative: &Path, content: &[u8]) -> std::io::Result<()>;

    /// Append bytes to a file relative to the repository root.
    /// Creates the file if it doesn't exist. Creates parent directories if needed.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn append_bytes(&self, relative: &Path, content: &[u8]) -> std::io::Result<()>;

    /// Check if a path exists relative to the repository root.
    fn exists(&self, relative: &Path) -> bool;

    /// Check if a path is a file relative to the repository root.
    fn is_file(&self, relative: &Path) -> bool;

    /// Check if a path is a directory relative to the repository root.
    fn is_dir(&self, relative: &Path) -> bool;

    /// Remove a file relative to the repository root.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn remove(&self, relative: &Path) -> std::io::Result<()>;

    /// Remove a file if it exists, silently succeeding if it doesn't.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn remove_if_exists(&self, relative: &Path) -> std::io::Result<()>;

    /// Remove a directory and all its contents relative to the repository root.
    ///
    /// Similar to `std::fs::remove_dir_all`, this removes a directory and everything inside it.
    /// Returns an error if the directory doesn't exist.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn remove_dir_all(&self, relative: &Path) -> std::io::Result<()>;

    /// Remove a directory and all its contents if it exists, silently succeeding if it doesn't.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn remove_dir_all_if_exists(&self, relative: &Path) -> std::io::Result<()>;

    /// Create a directory and all parent directories relative to the repository root.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn create_dir_all(&self, relative: &Path) -> std::io::Result<()>;

    /// List entries in a directory relative to the repository root.
    ///
    /// Returns a vector of `DirEntry`-like information for each entry.
    /// For production, this wraps `std::fs::read_dir`.
    /// For testing, this returns entries from the in-memory filesystem.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn read_dir(&self, relative: &Path) -> std::io::Result<Vec<DirEntry>>;

    /// Rename/move a file from one path to another relative to the repository root.
    ///
    /// This is used for backup rotation where files are moved to new names.
    /// Returns an error if the source file doesn't exist.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn rename(&self, from: &Path, to: &Path) -> std::io::Result<()>;

    /// Write content to a file atomically using temp file + rename pattern.
    ///
    /// This ensures the file is either fully written or not written at all,
    /// preventing partial writes or corruption from crashes/interruptions.
    ///
    /// # Implementation details
    ///
    /// - `WorkspaceFs`: Uses `tempfile::NamedTempFile` in the same directory,
    ///   writes content, syncs to disk, then atomically renames to target.
    ///   On Unix, temp file has mode 0600 for security.
    /// - `MemoryWorkspace`: Just calls `write()` since in-memory operations
    ///   are inherently atomic (no partial state possible).
    ///
    /// # When to use
    ///
    /// Use `write_atomic()` for critical files where corruption would be problematic:
    /// - XML outputs (issues.xml, plan.xml, `commit_message.xml`)
    /// - Agent artifacts (PLAN.md, commit-message.txt)
    /// - Any file that must not have partial content
    ///
    /// Use regular `write()` for:
    /// - Log files (append-only, partial is acceptable)
    /// - Temporary/debug files
    /// - Files where performance matters more than atomicity
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn write_atomic(&self, relative: &Path, content: &str) -> std::io::Result<()>;

    /// Set a file to read-only permissions.
    ///
    /// This is a best-effort operation for protecting files like PROMPT.md backups.
    /// On Unix, sets permissions to 0o444.
    /// On Windows, sets the readonly flag.
    /// In-memory implementations may no-op since permissions aren't relevant for testing.
    ///
    /// Returns Ok(()) on success or if the file doesn't exist (nothing to protect).
    /// Returns Err only if the file exists but permissions cannot be changed.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn set_readonly(&self, relative: &Path) -> std::io::Result<()>;

    /// Set a file to writable permissions.
    ///
    /// Reverses the effect of `set_readonly`.
    /// On Unix, sets permissions to 0o644.
    /// On Windows, clears the readonly flag.
    /// In-memory implementations may no-op since permissions aren't relevant for testing.
    ///
    /// Returns Ok(()) on success or if the file doesn't exist.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    fn set_writable(&self, relative: &Path) -> std::io::Result<()>;

    // =========================================================================
    // Path resolution (default implementations)
    // =========================================================================

    /// Resolve a relative path to an absolute path.
    fn absolute(&self, relative: &Path) -> PathBuf {
        self.root().join(relative)
    }

    /// Resolve a relative path to an absolute path as a string.
    fn absolute_str(&self, relative: &str) -> String {
        self.root().join(relative).display().to_string()
    }

    // =========================================================================
    // Well-known paths (default implementations)
    // =========================================================================

    /// Path to the `.agent` directory.
    fn agent_dir(&self) -> PathBuf {
        self.root().join(AGENT_DIR)
    }

    /// Path to the `.agent/tmp` directory.
    fn agent_tmp(&self) -> PathBuf {
        self.root().join(AGENT_TMP)
    }

    /// Path to `.agent/PLAN.md`.
    fn plan_md(&self) -> PathBuf {
        self.root().join(PLAN_MD)
    }

    /// Path to `.agent/ISSUES.md`.
    fn issues_md(&self) -> PathBuf {
        self.root().join(ISSUES_MD)
    }

    /// Path to `.agent/STATUS.md`.
    fn status_md(&self) -> PathBuf {
        self.root().join(STATUS_MD)
    }

    /// Path to `.agent/NOTES.md`.
    fn notes_md(&self) -> PathBuf {
        self.root().join(NOTES_MD)
    }

    /// Path to `.agent/commit-message.txt`.
    fn commit_message(&self) -> PathBuf {
        self.root().join(COMMIT_MESSAGE_TXT)
    }

    /// Path to `.agent/checkpoint.json`.
    fn checkpoint(&self) -> PathBuf {
        self.root().join(CHECKPOINT_JSON)
    }

    /// Path to `.agent/start_commit`.
    fn start_commit(&self) -> PathBuf {
        self.root().join(START_COMMIT)
    }

    /// Path to `.agent/review_baseline.txt`.
    fn review_baseline(&self) -> PathBuf {
        self.root().join(REVIEW_BASELINE_TXT)
    }

    /// Path to `PROMPT.md` in the repository root.
    fn prompt_md(&self) -> PathBuf {
        self.root().join(PROMPT_MD)
    }

    /// Path to `.agent/PROMPT.md.backup`.
    fn prompt_backup(&self) -> PathBuf {
        self.root().join(PROMPT_BACKUP)
    }

    /// Path to `.agent/config.toml`.
    fn agent_config(&self) -> PathBuf {
        self.root().join(AGENT_CONFIG_TOML)
    }

    /// Path to `.agent/agents.toml`.
    fn agents_toml(&self) -> PathBuf {
        self.root().join(AGENTS_TOML)
    }

    /// Path to an XSD schema file in `.agent/tmp/`.
    fn xsd_path(&self, name: &str) -> PathBuf {
        self.root().join(format!(".agent/tmp/{name}.xsd"))
    }

    /// Path to an XML file in `.agent/tmp/`.
    fn xml_path(&self, name: &str) -> PathBuf {
        self.root().join(format!(".agent/tmp/{name}.xml"))
    }

    /// Path to a JSON artifact file in `.agent/tmp/`.
    fn json_artifact_path(&self, artifact_type: &str) -> PathBuf {
        self.root().join(format!(".agent/tmp/{artifact_type}.json"))
    }

    /// Path to a partial JSON artifact file in `.agent/tmp/`.
    ///
    /// Partial artifacts are intermediate files for agent resumption only.
    /// Reducers and boundaries do NOT consume partial files.
    fn partial_json_artifact_path(&self, artifact_type: &str) -> PathBuf {
        self.root()
            .join(format!(".agent/tmp/{artifact_type}.partial.json"))
    }

    /// Path to a log file in `.agent/logs/`.
    fn log_path(&self, name: &str) -> PathBuf {
        self.root().join(format!(".agent/logs/{name}"))
    }

    // =========================================================================
    // JSON artifact persistence (default implementations)
    // =========================================================================

    /// Write a JSON artifact envelope to `.agent/tmp/{artifact_type}.json`.
    ///
    /// Uses atomic write to prevent partial artifacts. Creates parent
    /// directories if needed.
    ///
    /// # Errors
    ///
    /// Returns error if serialization or file write fails.
    fn write_artifact_json(&self, envelope: &ArtifactEnvelope) -> std::io::Result<()> {
        let json = serde_json::to_string_pretty(envelope)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let path = Path::new(AGENT_TMP).join(format!("{}.json", envelope.artifact_type));
        self.write_atomic(&path, &json)
    }

    /// Write a partial JSON artifact envelope to `.agent/tmp/{artifact_type}.partial.json`.
    ///
    /// Partial artifacts are intermediate files for agent resumption only.
    /// When a complete submission arrives, it should replace the partial file.
    ///
    /// # Errors
    ///
    /// Returns error if serialization or file write fails.
    fn write_partial_artifact_json(&self, envelope: &ArtifactEnvelope) -> std::io::Result<()> {
        let json = serde_json::to_string_pretty(envelope)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let path = Path::new(AGENT_TMP).join(format!("{}.partial.json", envelope.artifact_type));
        self.write_atomic(&path, &json)
    }

    /// Read a JSON artifact envelope from `.agent/tmp/{artifact_type}.json`.
    ///
    /// Returns `None` if the file does not exist.
    ///
    /// # Errors
    ///
    /// Returns error if the file exists but cannot be read or parsed.
    fn read_artifact_json(&self, artifact_type: &str) -> std::io::Result<Option<ArtifactEnvelope>> {
        let path = Path::new(AGENT_TMP).join(format!("{artifact_type}.json"));
        if !self.exists(&path) {
            return Ok(None);
        }
        let content = self.read(&path)?;
        let envelope: ArtifactEnvelope = serde_json::from_str(&content)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        Ok(Some(envelope))
    }
}

// ============================================================================
// Production Implementation: WorkspaceFs
// ============================================================================

pub mod files;

// Re-export WorkspaceFs for backward compatibility
pub use files::WorkspaceFs;

// ============================================================================
// Test Implementation: MemoryWorkspace
// ============================================================================

#[cfg(any(test, feature = "test-utils"))]
pub mod memory_workspace;

#[cfg(any(test, feature = "test-utils"))]
pub use memory_workspace::MemoryWorkspace;

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    include!("workspace/tests.rs");
}
