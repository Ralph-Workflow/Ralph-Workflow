// WorkspaceFs - Production filesystem implementation of the Workspace trait.
//
// This file contains the production implementation that performs actual
// filesystem operations relative to the repository root.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use crate::workspace::{DirEntry, Workspace};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AtomicWriteSync {
    Full,
    SkipInterrupt,
}

pub fn decide_atomic_write_sync(interrupted: bool) -> AtomicWriteSync {
    if interrupted {
        AtomicWriteSync::SkipInterrupt
    } else {
        AtomicWriteSync::Full
    }
}

pub fn sync_temp_file(file: &std::fs::File, policy: AtomicWriteSync) -> io::Result<()> {
    match policy {
        AtomicWriteSync::Full => {
            file.sync_all()?;
            Ok(())
        }
        AtomicWriteSync::SkipInterrupt => Ok(()),
    }
}

#[cfg(unix)]
pub fn set_restrictive_permissions(path: &std::path::Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let metadata = fs::metadata(path)?;
    let mut perms = metadata.permissions();
    perms.set_mode(0o600);
    fs::set_permissions(path, perms)
}

#[cfg(not(unix))]
pub fn set_restrictive_permissions(_path: &std::path::Path) -> io::Result<()> {
    Ok(())
}

/// Production workspace implementation using the real filesystem.
///
/// All file operations are performed relative to the repository root using `std::fs`.
#[derive(Debug, Clone)]
pub struct WorkspaceFs {
    root: PathBuf,
}

impl WorkspaceFs {
    /// Create a new workspace filesystem rooted at the given path.
    ///
    /// # Arguments
    ///
    /// * `repo_root` - The repository root directory (typically discovered via git)
    #[must_use]
    pub const fn new(repo_root: PathBuf) -> Self {
        Self { root: repo_root }
    }
}

impl Workspace for WorkspaceFs {
    fn root(&self) -> &Path {
        &self.root
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        fs::read_to_string(self.root.join(relative))
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        fs::read(self.root.join(relative))
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        let path = self.root.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        let path = self.root.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        use std::io::Write;
        let path = self.root.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        file.write_all(content)?;
        file.flush()
    }

    fn exists(&self, relative: &Path) -> bool {
        self.root.join(relative).exists()
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.root.join(relative).is_file()
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.root.join(relative).is_dir()
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        fs::remove_file(self.root.join(relative))
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        let path = self.root.join(relative);
        if path.exists() {
            fs::remove_file(path)?;
        }
        Ok(())
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        fs::remove_dir_all(self.root.join(relative))
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        let path = self.root.join(relative);
        if path.exists() {
            fs::remove_dir_all(path)?;
        }
        Ok(())
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        fs::create_dir_all(self.root.join(relative))
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
        let abs_path = self.root.join(relative);
        let entries: Vec<DirEntry> = fs::read_dir(abs_path)?
            .map(|entry| -> io::Result<DirEntry> {
                let entry = entry?;
                let metadata = entry.metadata()?;
                let rel_path = relative.join(entry.file_name());
                let modified = metadata.modified().ok();
                Ok(if let Some(mod_time) = modified {
                    DirEntry::with_modified(
                        rel_path,
                        metadata.is_file(),
                        metadata.is_dir(),
                        mod_time,
                    )
                } else {
                    DirEntry::new(rel_path, metadata.is_file(), metadata.is_dir())
                })
            })
            .collect::<io::Result<Vec<_>>>()?;
        Ok(entries)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        fs::rename(self.root.join(from), self.root.join(to))
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        use std::io::Write;
        use tempfile::NamedTempFile;

        let path = self.root.join(relative);

        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }

        let parent_dir = path.parent().unwrap_or_else(|| Path::new("."));
        let mut temp_file = NamedTempFile::new_in(parent_dir)?;

        #[cfg(unix)]
        set_restrictive_permissions(temp_file.path())?;

        temp_file.write_all(content.as_bytes())?;
        temp_file.flush()?;

        let policy = decide_atomic_write_sync(crate::interrupt::user_interrupted_occurred());
        sync_temp_file(temp_file.as_file(), policy)?;

        temp_file.persist(&path).map_err(|e| e.error)?;

        Ok(())
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        let path = self.root.join(relative);
        if !path.exists() {
            return Ok(());
        }

        let metadata = fs::metadata(&path)?;
        let mut perms = metadata.permissions();

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            perms.set_mode(0o444);
        }

        #[cfg(windows)]
        {
            perms.set_readonly(true);
        }

        fs::set_permissions(path, perms)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        let path = self.root.join(relative);
        if !path.exists() {
            return Ok(());
        }

        let metadata = fs::metadata(&path)?;
        let mut perms = metadata.permissions();

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            perms.set_mode(0o644);
        }

        #[cfg(windows)]
        {
            perms.set_readonly(false);
        }

        fs::set_permissions(path, perms)
    }
}
