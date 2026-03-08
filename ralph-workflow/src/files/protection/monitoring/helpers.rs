fn push_warning(warnings: &Arc<Mutex<Vec<String>>>, warning: String) {
    let mut guard = match warnings.lock() {
        Ok(g) => g,
        Err(poisoned) => poisoned.into_inner(),
    };
    guard.push(warning);
}

fn drain_warnings(warnings: &Arc<Mutex<Vec<String>>>) -> Vec<String> {
    let mut guard = match warnings.lock() {
        Ok(g) => g,
        Err(poisoned) => poisoned.into_inner(),
    };
    std::mem::take(&mut *guard)
}

fn read_backup_content_secure(path: &Path) -> Option<String> {
    // Defense-in-depth against symlink/hardlink attacks:
    // - Reject symlink backups (symlink_metadata)
    // - On Unix, open with O_NOFOLLOW and reject nlink != 1
    // - Ensure it's a regular file
    #[cfg(unix)]
    {
        use std::io::Read;
        use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)
            .ok()?;

        let metadata = file.metadata().ok()?;
        if !metadata.is_file() {
            return None;
        }
        if metadata.nlink() != 1 {
            return None;
        }

        let mut buf = String::new();
        file.read_to_string(&mut buf).ok()?;
        Some(buf)
    }

    #[cfg(not(unix))]
    {
        use std::io::Read;

        let meta = fs::symlink_metadata(path).ok()?;
        if meta.file_type().is_symlink() {
            return None;
        }
        if !meta.is_file() {
            return None;
        }

        let mut file = std::fs::File::open(path).ok()?;
        let mut buf = String::new();
        file.read_to_string(&mut buf).ok()?;
        Some(buf)
    }
}

fn restore_prompt_content_atomic(prompt_path: &Path, content: &[u8]) -> std::io::Result<()> {
    use std::io::Write;

    // Ensure destination is not a directory.
    if let Ok(meta) = fs::symlink_metadata(prompt_path) {
        if meta.is_dir() {
            return Err(std::io::Error::other("PROMPT.md path is a directory"));
        }
    }

    let temp_name = unique_temp_name();
    let temp_path = Path::new(&temp_name);

    // Create temp file in the same directory to keep rename on same filesystem.
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(temp_path)?;
    file.write_all(content)?;
    file.flush()?;
    let _ = file.sync_all();
    drop(file);

    // Make the temp file read-only before publishing it.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(temp_path)?.permissions();
        perms.set_mode(0o444);
        fs::set_permissions(temp_path, perms)?;
    }

    #[cfg(windows)]
    {
        let mut perms = fs::metadata(temp_path)?.permissions();
        perms.set_readonly(true);
        fs::set_permissions(temp_path, perms)?;
    }

    // Rename is symlink-safe: it replaces the directory entry rather than following
    // a symlink target.
    #[cfg(windows)]
    {
        // std::fs::rename does not replace existing destinations on Windows.
        if prompt_path.exists() {
            let _ = fs::remove_file(prompt_path);
        }
    }

    let rename_result = fs::rename(temp_path, prompt_path);
    if let Err(e) = rename_result {
        let _ = fs::remove_file(temp_path);
        return Err(e);
    }

    Ok(())
}

fn unique_temp_name() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let pid = std::process::id();
    format!(".prompt_restore_tmp_{pid}_{nanos}")
}

fn is_prompt_md_path(path: &Path) -> bool {
    matches!(path.file_name(), Some(name) if name == "PROMPT.md")
}

impl Drop for PromptMonitor {
    fn drop(&mut self) {
        // Signal the thread to stop when dropped
        self.stop_signal.store(true, Ordering::Release);

        // Take the handle and let it finish on its own
        // (we can't wait in Drop because we might be panicking)
        let _ = self.monitor_thread.take();
    }
}
