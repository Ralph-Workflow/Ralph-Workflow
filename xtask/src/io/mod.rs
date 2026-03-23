//! I/O layer for xtask: filesystem and external data transport work.
//!
//! This module contains code that reads/writes files, scans directories,
//! and handles caching with filesystem persistence.

pub mod cache;
pub mod scanner;
