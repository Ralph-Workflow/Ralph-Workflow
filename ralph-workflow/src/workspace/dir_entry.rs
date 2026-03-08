// DirEntry - abstraction for directory entries.
//
// This file is included by workspace.rs via include!().

/// A directory entry returned by `Workspace::read_dir`.
///
/// This abstracts `std::fs::DirEntry` to allow in-memory implementations.
#[derive(Debug, Clone)]
pub struct DirEntry {
    /// The path of this entry (relative to workspace root).
    path: PathBuf,
    /// Whether this entry is a file.
    is_file: bool,
    /// Whether this entry is a directory.
    is_dir: bool,
    /// Optional modification time (for sorting by recency).
    modified: Option<std::time::SystemTime>,
}

impl DirEntry {
    /// Create a new directory entry.
    #[must_use]
    pub const fn new(path: PathBuf, is_file: bool, is_dir: bool) -> Self {
        Self {
            path,
            is_file,
            is_dir,
            modified: None,
        }
    }

    /// Create a new directory entry with modification time.
    #[must_use]
    pub const fn with_modified(
        path: PathBuf,
        is_file: bool,
        is_dir: bool,
        modified: std::time::SystemTime,
    ) -> Self {
        Self {
            path,
            is_file,
            is_dir,
            modified: Some(modified),
        }
    }

    /// Get the path of this entry.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Check if this entry is a file.
    #[must_use]
    pub const fn is_file(&self) -> bool {
        self.is_file
    }

    /// Check if this entry is a directory.
    #[must_use]
    pub const fn is_dir(&self) -> bool {
        self.is_dir
    }

    /// Get the file name of this entry.
    #[must_use]
    pub fn file_name(&self) -> Option<&std::ffi::OsStr> {
        self.path.file_name()
    }

    /// Get the modification time of this entry, if available.
    #[must_use]
    pub const fn modified(&self) -> Option<std::time::SystemTime> {
        self.modified
    }
}
