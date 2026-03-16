// WorkspaceFs - Production filesystem implementation of the Workspace trait.
//
// This file contains the production implementation that performs actual
// filesystem operations relative to the repository root.

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
        let mut entries = Vec::new();
        for entry in fs::read_dir(abs_path)? {
            let entry = entry?;
            let metadata = entry.metadata()?;
            // Store relative path from workspace root
            let rel_path = relative.join(entry.file_name());
            let modified = metadata.modified().ok();
            if let Some(mod_time) = modified {
                entries.push(DirEntry::with_modified(
                    rel_path,
                    metadata.is_file(),
                    metadata.is_dir(),
                    mod_time,
                ));
            } else {
                entries.push(DirEntry::new(
                    rel_path,
                    metadata.is_file(),
                    metadata.is_dir(),
                ));
            }
        }
        Ok(entries)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        fs::rename(self.root.join(from), self.root.join(to))
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        use std::io::Write;
        use tempfile::NamedTempFile;

        let path = self.root.join(relative);

        // Create parent directories if needed
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }

        // Create a NamedTempFile in the same directory as the target file.
        // This ensures atomic rename works (same filesystem).
        let parent_dir = path.parent().unwrap_or_else(|| Path::new("."));
        let mut temp_file = NamedTempFile::new_in(parent_dir)?;

        // Set restrictive permissions on temp file (0600 = owner read/write only)
        // This prevents other users from reading the temp file before rename
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(temp_file.path())?.permissions();
            perms.set_mode(0o600);
            fs::set_permissions(temp_file.path(), perms)?;
        }

        // Write content to the temp file
        temp_file.write_all(content.as_bytes())?;
        temp_file.flush()?;

        // Skip sync_all() when a user interrupt is in progress.
        //
        // On macOS, sync_all() maps to F_FULLFSYNC which waits for ALL pending I/O to
        // complete at the hardware level. After a SIGTERM-killed agent subprocess, the
        // kernel may defer this indefinitely (tens of seconds), causing the checkpoint
        // write to hang and the process to never exit.
        //
        // Skipping sync is safe in this context: we are shutting down due to Ctrl+C and
        // the user has already accepted the risk of incomplete I/O by pressing interrupt.
        // The checkpoint content is already in the OS page cache and will be visible to
        // any subsequent process that reads it (e.g., `ralph --resume`). The only risk
        // is data loss if the machine loses power between now and the OS flushing the
        // cache -- an acceptable trade-off for an interrupt scenario.
        if !crate::interrupt::user_interrupted_occurred() {
            temp_file.as_file().sync_all()?;
        }

        // Persist the temp file to the target location (atomic rename)
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
