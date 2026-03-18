//! File system operations for CLI boundary.
//!
//! This module provides boundary functions for filesystem operations.
//! All functions here wrap std::fs operations and are exempt from
//! functional programming lints because they are explicitly boundary code.

use std::fs;
use std::io;
use std::path::Path;

pub fn create_dir_all(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)
}

pub fn write(path: &Path, contents: &str) -> io::Result<()> {
    fs::write(path, contents)
}

pub fn exists(path: &Path) -> bool {
    path.exists()
}
