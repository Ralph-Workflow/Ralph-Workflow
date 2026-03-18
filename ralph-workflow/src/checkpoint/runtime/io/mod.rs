//! IO boundary for checkpoint file-state capture.
//!
//! This module exists because the `forbid_io_effects` lint forbids domain code from
//! calling `std::fs` and `std::process` directly. File-reading and git-command-execution
//! logic lives here so domain code can call it without triggering the lint.

pub mod file_capture;
pub mod git_capture;

pub use file_capture::read_file_bytes;
pub use git_capture::{git_branch_name, git_head_oid, git_modified_files, git_status};
