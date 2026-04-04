//! I/O layer for xtask: filesystem and external data transport work.
//!
//! This module contains code that reads/writes files, scans directories,
//! and handles caching with filesystem persistence.

pub mod cache;
pub mod fingerprint;
pub mod hash;
pub mod native_scan_checks;
pub mod native_scan_types;
pub mod scanner;
pub mod scanner_diagnostics;
pub mod scope;
pub mod shell_scripts;
pub mod string_search;
