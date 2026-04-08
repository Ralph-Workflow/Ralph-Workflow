/// Cancel an active run by removing its lock file.
///
/// # Errors
///
/// Returns an error if the lock file cannot be removed.
#[tauri::command]
#[specta::specta]
pub fn cancel_run(repo_path: String, worktree_path: Option<String>) -> Result<(), String> {
    let base = worktree_path.map_or_else(
        || std::path::PathBuf::from(&repo_path),
        std::path::PathBuf::from,
    );
    let lock_file = base.join(".agent").join("tmp").join("run.lock");

    if lock_file.exists() {
        std::fs::remove_file(&lock_file).map_err(|e| format!("Failed to remove lock file: {e}"))?;
    }

    Ok(())
}

/// Open a path in the system file manager (Finder on macOS, Explorer on Windows).
///
/// # Errors
///
/// Returns an error if the path does not exist or the system file manager cannot be opened.
#[tauri::command]
#[specta::specta]
pub fn open_in_file_manager(path: String) -> Result<(), String> {
    let path_buf = std::path::PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("Path does not exist: {path}"));
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {e}"))?;
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {e}"))?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {e}"))?;
    }

    Ok(())
}

/// Open a terminal at the specified path.
///
/// # Errors
///
/// Returns an error if the path does not exist or a terminal cannot be opened.
#[tauri::command]
#[specta::specta]
pub fn open_in_terminal(path: String) -> Result<(), String> {
    let path_buf = std::path::PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("Path does not exist: {path}"));
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .args(["-a", "Terminal", &path])
            .spawn()
            .map_err(|e| format!("Failed to open terminal: {e}"))?;
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/c", "start", "cmd", "/k", "cd", "/d"])
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open terminal: {e}"))?;
    }

    #[cfg(target_os = "linux")]
    {
        let terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"];
        let mut opened = false;
        for terminal in terminals {
            if std::process::Command::new(terminal)
                .args(["--working-directory", &path])
                .spawn()
                .is_ok()
            {
                opened = true;
                break;
            }
        }
        if !opened {
            return Err("Failed to open any known terminal emulator".to_string());
        }
    }

    Ok(())
}
