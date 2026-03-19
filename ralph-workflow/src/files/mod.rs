//! File management utilities for Ralph's agent files.
//!
//! This module manages the `.agent/` directory structure and files:
//! - PLAN.md, ISSUES.md, STATUS.md, NOTES.md lifecycle
//! - commit-message.txt management
//! - PROMPT.md validation
//! - Isolation mode file cleanup
//! - File path extraction from ISSUES content
//! - File integrity verification and checksums
//! - Error recovery and state repair
//! - Real-time file system monitoring for PROMPT.md protection
//!
//! # Module Organization
//!
//! The files module is organized by domain concern:
//!
//! - [`protection`] - File protection and integrity (validation, integrity, monitoring)
//! - [`llm_output_extraction`] - LLM output extraction (commit message, JSON extraction)
//! - [`result_extraction`] - File path extraction from ISSUES content
//!
//! # Isolation Mode
//!
//! By default, Ralph operates in isolation mode where STATUS.md, NOTES.md,
//! and ISSUES.md are not persisted between runs. This prevents context
//! contamination from previous runs.
//!
//! # Orchestrator-Controlled File I/O
//!
//! The orchestrator is the sole entity responsible for writing output files.
//! Agent JSON output is extracted and written by the orchestrator, ensuring
//! consistent file handling regardless of agent behavior.

pub mod agent_files;
pub mod backup;
pub mod context;
pub mod integrity;
pub mod monitoring;
pub mod recovery;

pub mod protection;

pub mod llm_output_extraction;
pub mod result_extraction;

pub use agent_files::{
    cleanup_generated_files_with_workspace, delete_commit_message_file_with_workspace,
    delete_plan_file_with_workspace, ensure_files_with_workspace, file_contains_marker,
    file_contains_marker_with_workspace, read_commit_message_file_with_workspace,
    setup_xsd_schemas_with_workspace, write_commit_message_file_with_workspace, GENERATED_FILES,
};

pub use backup::{
    create_prompt_backup_with_workspace, make_prompt_read_only_with_workspace,
    make_prompt_writable_with_workspace, write_diff_backup_with_workspace,
};

pub use context::{
    clean_context_for_reviewer_with_workspace, delete_issues_file_for_isolation_with_workspace,
    update_status_with_workspace,
};

pub use integrity::{
    check_and_cleanup_xml_before_retry_with_workspace, check_filesystem_ready_with_workspace,
    check_xml_file_writable_with_workspace, cleanup_stale_xml_files_with_workspace,
    verify_file_not_corrupted_with_workspace, write_file_atomic_with_workspace,
};

pub use protection::{
    restore_prompt_if_needed, validate_prompt_md, validate_prompt_md_with_workspace,
};

pub use recovery::{auto_repair_with_workspace, RecoveryStatus};
