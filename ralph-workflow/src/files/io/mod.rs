//! File I/O operations for Ralph's agent files.
//!
//! This module handles basic file input/output operations:
//! - Agent directory management (.agent/)
//! - Commit message file operations
//! - Context cleanup and file operations
//! - Error recovery for file operations
//! - File integrity and atomic writes
//! - PROMPT.md backup management
//!
//! # Submodules
//!
//! - `integrity` - File integrity and atomic writes (internal)
//! - `recovery` - Error recovery and state repair (internal)
//! - [`context`] - Context file management (STATUS.md, NOTES.md, ISSUES.md)
//! - [`agent_files`] - Agent file operations (`ensure_files`, commit message, etc.)
//! - [`backup`] - PROMPT.md backup and read-only protection
//!
//! # File Operations
//!
//! All file operations should go through the `Workspace` trait for testability.
//! See `crate::workspace` for the `Workspace` trait and `MemoryWorkspace` for testing.

pub(in crate::files) mod integrity;
pub(in crate::files) mod recovery;

pub mod agent_files;
pub mod backup;
pub mod context;
pub mod monitoring;

// Re-exports from submodules for API convenience
pub use agent_files::{
    cleanup_generated_files_with_workspace, delete_commit_message_file_with_workspace,
    delete_plan_file_with_workspace, ensure_files_with_workspace, file_contains_marker,
    file_contains_marker_with_workspace, read_commit_message_file_with_workspace,
    setup_xsd_schemas_with_workspace, write_commit_message_file_with_workspace, GENERATED_FILES,
};

pub use integrity::{
    check_and_cleanup_xml_before_retry_with_workspace, check_filesystem_ready_with_workspace,
    check_xml_file_writable_with_workspace, cleanup_stale_xml_files_with_workspace,
    verify_file_not_corrupted_with_workspace, write_file_atomic_with_workspace,
};

pub use backup::{
    create_prompt_backup_with_workspace, make_prompt_read_only_with_workspace,
    make_prompt_writable_with_workspace, write_diff_backup_with_workspace,
};

pub use context::{
    clean_context_for_reviewer_with_workspace, delete_issues_file_for_isolation_with_workspace,
    update_status_with_workspace,
};

pub use recovery::{auto_repair_with_workspace, RecoveryStatus};
