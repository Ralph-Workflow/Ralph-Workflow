//! Git wrapper for blocking commits during agent phase.
//!
//! This module provides safety mechanisms to prevent accidental commits while
//! an AI agent is actively modifying files. It works through two mechanisms:
//!
//! - **Marker file**: Creates `<git-dir>/ralph/no_agent_commit` during agent
//!   execution. Both the git wrapper and hooks check for this file.
//! - **PATH wrapper**: Installs a temporary `git` wrapper script that intercepts
//!   `commit`, `push`, and `tag` commands when the marker file exists.
//!
//! All enforcement state files live inside the git metadata directory (`<git-dir>/ralph/`)
//! so they are invisible to working-tree scans and cannot be confused with product code.
//!
//! The wrapper is automatically cleaned up when the agent phase ends, even on
//! unexpected exits (Ctrl+C, panics) via [`cleanup_agent_phase_silent`].
//!
//! # Module structure
//!
//! This module is split into focused submodules:
//! - [`marker`] — marker file creation and repair
//! - [`path_wrapper`] — PATH wrapper directory management
//! - [`script`] — wrapper shell script generation
//! - [`phase`] — agent phase lifecycle and self-healing checks
//! - [`cleanup`] — cleanup utilities

// Real implementation — boundary module (file stem is `io`).
include!("wrapper/io.rs");
