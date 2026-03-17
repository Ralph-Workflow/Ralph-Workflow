//! In-memory workspace implementation for testing.
//!
//! [`MemoryWorkspace`] provides a fully in-memory implementation of the [`Workspace`]
//! trait, enabling fast, isolated tests without filesystem I/O or cleanup.
//!
//! ## Architecture
//!
//! Storage uses a `RwLock<HashMap<PathBuf, MemoryFile>>` for thread-safe concurrent access.
//! All paths are stored relative to the workspace root. Files include both content and
//! modification time metadata.
//!
//! ## Thread Safety and `RwLock` Poisoning
//!
//! The workspace uses `RwLock` for interior mutability to allow concurrent reads while
//! serializing writes. Lock operations use `.expect()` instead of `.unwrap()` with
//! descriptive panic messages for clarity when failures occur.
//!
//! **`RwLock` Poisoning:** An `RwLock` becomes "poisoned" when a thread panics while holding
//! the lock. This prevents data corruption by ensuring no thread can access potentially
//! inconsistent state left by the panicked thread.
//!
//! In test infrastructure like `MemoryWorkspace`, poisoning indicates a serious test bug
//! (a panic while holding the workspace lock). Using `.expect()` with a clear message
//! helps diagnose these issues quickly:
//! - The panic message identifies which lock was poisoned
//! - The message explains what poisoning means (panic in another thread)
//! - The original panic that caused poisoning is preserved in the stack trace
//!
//! For production code paths that must not panic, prefer returning `Result` and handling
//! lock poisoning errors explicitly. For test infrastructure, `.expect()` with descriptive
//! messages is acceptable as poisoning indicates a test bug that should be fixed.
//!
//! ## Usage
//!
//! ```rust
//! use ralph_workflow::workspace::{Workspace, MemoryWorkspace};
//! use std::path::Path;
//!
//! let workspace = MemoryWorkspace::new_test()
//!     .with_file(".agent/PLAN.md", "# Plan\n...")
//!     .with_file("src/main.rs", "fn main() {}");
//!
//! assert!(workspace.exists(Path::new(".agent/PLAN.md")));
//! let content = workspace.read(Path::new("src/main.rs")).unwrap();
//! ```
//!
//! ## See Also
//!
//! - [`crate::workspace::WorkspaceFs`] - Production filesystem implementation
//! - [`crate::workspace::Workspace`] - Trait definition

use std::path::{Path, PathBuf};

mod core;
mod test_helpers;

/// In-memory file entry with content and metadata.
#[derive(Debug, Clone)]
struct MemoryFile {
    content: Vec<u8>,
    modified: std::time::SystemTime,
}

impl MemoryFile {
    fn new(content: Vec<u8>) -> Self {
        Self {
            content,
            modified: std::time::SystemTime::now(),
        }
    }

    const fn with_modified(content: Vec<u8>, modified: std::time::SystemTime) -> Self {
        Self { content, modified }
    }
}

/// In-memory workspace implementation for testing.
///
/// All file operations are performed against an in-memory `HashMap`, allowing tests to:
/// - Verify what was written without touching real files
/// - Control what reads return
/// - Run in parallel without filesystem conflicts
/// - Be deterministic and fast
#[derive(Debug)]
pub struct MemoryWorkspace {
    root: PathBuf,
    files: std::sync::RwLock<std::collections::HashMap<PathBuf, MemoryFile>>,
    directories: std::sync::RwLock<std::collections::HashSet<PathBuf>>,
}

impl MemoryWorkspace {
    /// Create a new in-memory workspace with the given virtual root path.
    ///
    /// The root path is used for path resolution but no real filesystem access occurs.
    #[must_use]
    pub fn new(root: PathBuf) -> Self {
        Self {
            root,
            files: std::sync::RwLock::new(std::collections::HashMap::new()),
            directories: std::sync::RwLock::new(std::collections::HashSet::new()),
        }
    }

    /// Create a new in-memory workspace with a default test root path.
    #[must_use]
    pub fn new_test() -> Self {
        Self::new(PathBuf::from("/test/repo"))
    }

    /// Ensure all parent directories exist for the given path.
    ///
    /// This is a helper to reduce duplication in file/directory creation methods.
    fn ensure_parent_dirs(&self, path: &Path) {
        if let Some(parent) = path.parent() {
            if parent.as_os_str().is_empty() {
                return;
            }
            let dirs_to_create: Vec<PathBuf> = parent
                .components()
                .scan(PathBuf::new(), |state, component| {
                    state.push(component);
                    Some(state.clone())
                })
                .collect();
            let mut dirs = self.directories.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock");
            for dir in dirs_to_create {
                dirs.insert(dir);
            }
        }
    }

    /// Ensure all components of the path exist as directories.
    ///
    /// Used for creating directories themselves (not just parents).
    fn ensure_dir_path(&self, path: &Path) {
        let dirs_to_create: Vec<PathBuf> = path
            .components()
            .scan(PathBuf::new(), |state, component| {
                state.push(component);
                Some(state.clone())
            })
            .collect();
        let mut dirs = self.directories.write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock");
        for dir in dirs_to_create {
            dirs.insert(dir);
        }
    }
}

impl Clone for MemoryWorkspace {
    fn clone(&self) -> Self {
        Self {
            root: self.root.clone(),
            files: std::sync::RwLock::new(self.files.read()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
                .clone()),
            directories: std::sync::RwLock::new(self.directories.read()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock")
                .clone()),
        }
    }
}
