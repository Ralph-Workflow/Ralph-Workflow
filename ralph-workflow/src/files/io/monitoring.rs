//! PROMPT.md monitoring for protection against deletion.
//!
//! This module provides proactive monitoring to detect deletion attempts
//! on PROMPT.md immediately, rather than waiting for periodic checks.
//! It uses the `notify` crate for cross-platform file system events.
//!
//! # Effect System Exception
//!
//! This module uses `std::fs` directly rather than the `Workspace` trait.
//! This is a documented exception to the effect system architecture because:
//!
//! 1. **Real-time filesystem monitoring**: The `notify` crate requires watching
//!    the actual filesystem for events (inotify, `FSEvents`, `ReadDirectoryChangesW`).
//! 2. **Background thread operation**: The monitor runs in a separate thread
//!    that cannot share `PhaseContext` or workspace references.
//! 3. **OS-level event handling**: File system events are inherently tied to
//!    the real filesystem, not an abstraction layer.
//!
//! This exception is documented in `docs/architecture/effect-system.md`.

use std::fs;
use std::fs::OpenOptions;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

const NOTIFY_EVENT_QUEUE_CAPACITY: usize = 1024;

fn bounded_event_queue<T>() -> (std::sync::mpsc::SyncSender<T>, std::sync::mpsc::Receiver<T>) {
    std::sync::mpsc::sync_channel(NOTIFY_EVENT_QUEUE_CAPACITY)
}

/// File system monitor for detecting PROMPT.md deletion events.
///
/// The monitor watches for deletion events and automatically restores
/// PROMPT.md from backup when detected. Monitoring happens in a background
/// thread, so the main thread is not blocked.
///
/// # Example
///
/// ```no_run
/// # use ralph_workflow::files::io::monitoring::PromptMonitor;
/// let mut monitor = PromptMonitor::new().unwrap();
/// monitor.start().unwrap();
///
/// // ... run pipeline phases ...
///
/// // Check if any restoration occurred
/// if monitor.check_and_restore() {
///     println!("PROMPT.md was restored!");
/// }
///
/// monitor.stop();
/// # Ok::<(), std::io::Error>(())
/// ```
pub struct PromptMonitor {
    /// Flag indicating if PROMPT.md was deleted and restored
    restoration_detected: Arc<AtomicBool>,
    /// Flag to signal the monitor thread to stop
    stop_signal: Arc<AtomicBool>,
    /// Handle to the monitor thread (None if not started)
    monitor_thread: Option<thread::JoinHandle<()>>,
    /// Warnings from the monitor thread (via thread-safe channel).
    ///
    /// Uses Mutex only for thread communication, not to hide state changes.
    /// This is a documented exception for background thread coordination.
    warnings: Arc<std::sync::Mutex<Vec<String>>>,
}

impl PromptMonitor {
    /// Create a new file system monitor for PROMPT.md.
    ///
    /// Returns an error if the current directory cannot be accessed or
    /// if PROMPT.md doesn't exist (we need to know what to watch for).
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn new() -> std::io::Result<Self> {
        // Verify we're in a valid directory with PROMPT.md
        let prompt_path = Path::new("PROMPT.md");
        if !prompt_path.exists() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "PROMPT.md does not exist - cannot monitor",
            ));
        }

        Ok(Self {
            restoration_detected: Arc::new(AtomicBool::new(false)),
            stop_signal: Arc::new(AtomicBool::new(false)),
            monitor_thread: None,
            warnings: Arc::new(std::sync::Mutex::new(Vec::new())),
        })
    }

    /// Start monitoring PROMPT.md for deletion events.
    ///
    /// This spawns a background thread that watches for file system events.
    /// Returns immediately; monitoring happens asynchronously.
    ///
    /// The monitor will automatically restore PROMPT.md from backup if
    /// deletion is detected.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn start(&mut self) -> std::io::Result<()> {
        if self.monitor_thread.is_some() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::AlreadyExists,
                "Monitor is already running",
            ));
        }

        let restoration_flag = Arc::clone(&self.restoration_detected);
        let stop_signal = Arc::clone(&self.stop_signal);
        let warnings = Arc::clone(&self.warnings);

        let handle = thread::spawn(move || {
            Self::monitor_thread_main(&restoration_flag, &stop_signal, &warnings);
        });

        self.monitor_thread = Some(handle);
        Ok(())
    }

    /// Background thread entry point for file system monitoring.
    ///
    /// This thread watches the current directory for deletion events on
    /// PROMPT.md and restores from backup when detected.
    fn monitor_thread_main(
        restoration_detected: &Arc<AtomicBool>,
        stop_signal: &Arc<AtomicBool>,
        warnings: &Arc<std::sync::Mutex<Vec<String>>>,
    ) {
        use notify::Watcher;

        // Bounded queue for notify events.
        //
        // The notify crate can emit bursts of events under heavy filesystem activity.
        // We cap the in-memory queue to avoid unbounded growth; when full, we drop
        // events because PROMPT.md deletion protection is best-effort and repeated
        // events are coalescable (the polling fallback also covers missed events).
        let (tx, rx) = bounded_event_queue();
        let event_sender = tx;

        // Create a watcher for the current directory
        let mut watcher = match notify::recommended_watcher(move |res| {
            // Drop if full to keep memory bounded.
            let _ = event_sender.try_send(res);
        }) {
            Ok(w) => w,
            Err(e) => {
                push_warning(
                    warnings,
                    format!(
                        "Failed to create file system watcher: {e}. Falling back to periodic polling for PROMPT.md protection."
                    ),
                );
                // Fallback to polling if watcher creation fails
                Self::polling_monitor(restoration_detected, stop_signal);
                return;
            }
        };

        // Watch the current directory for events
        if let Err(e) = watcher.watch(Path::new("."), notify::RecursiveMode::NonRecursive) {
            push_warning(
                warnings,
                format!(
                    "Failed to watch current directory: {e}. Falling back to periodic polling for PROMPT.md protection."
                ),
            );
            Self::polling_monitor(restoration_detected, stop_signal);
            return;
        }

        // Process events until stop signal is received
        while !stop_signal.load(Ordering::Relaxed) {
            // Check for events with a short timeout
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(Ok(event)) => {
                    Self::handle_fs_event(&event, restoration_detected);

                    // Drain any queued events to coalesce bursts.
                    while let Ok(next) = rx.try_recv() {
                        if let Ok(next_event) = next {
                            Self::handle_fs_event(&next_event, restoration_detected);
                        }
                    }
                }
                Ok(Err(_)) | Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                    // Error in watcher or timeout - continue anyway
                }
                Err(_) => {
                    // Channel disconnected - stop monitoring
                    break;
                }
            }
        }
    }

    /// Handle a file system event from the watcher.
    fn handle_fs_event(event: &notify::Event, restoration_detected: &Arc<AtomicBool>) {
        for path in &event.paths {
            if is_prompt_md_path(path) {
                // Check for remove event
                if matches!(event.kind, notify::EventKind::Remove(_)) {
                    // PROMPT.md was removed - restore it
                    if Self::restore_from_backup() {
                        restoration_detected.store(true, Ordering::Release);
                    }
                }
            }
        }
    }

    /// Fallback polling-based monitor when file system watcher fails.
    ///
    /// Some filesystems (NFS, network drives) don't support file system
    /// events. This fallback polls every 100ms to check if PROMPT.md exists.
    fn polling_monitor(restoration_detected: &Arc<AtomicBool>, stop_signal: &Arc<AtomicBool>) {
        let check_deletion = || {
            let prompt_exists_now = Path::new("PROMPT.md").exists();
            (prompt_exists_now, prompt_exists_now)
        };

        let mut previous_exists = Path::new("PROMPT.md").exists();

        while !stop_signal.load(Ordering::Relaxed) {
            thread::sleep(Duration::from_millis(100));

            let (current_exists, _) = check_deletion();

            // Detect deletion (transition from exists to not exists)
            if previous_exists && !current_exists && Self::restore_from_backup() {
                restoration_detected.store(true, Ordering::Release);
            }

            previous_exists = current_exists;
        }
    }

    /// Restore PROMPT.md from backup.
    ///
    /// Tries backups in order:
    /// - .agent/PROMPT.md.backup
    /// - .agent/PROMPT.md.backup.1
    /// - .agent/PROMPT.md.backup.2
    ///
    /// Returns true if restoration succeeded, false otherwise.
    ///
    /// Uses atomic open to avoid TOCTOU race conditions - opens and reads
    /// the file in one operation rather than checking existence separately.
    #[must_use]
    pub fn restore_from_backup() -> bool {
        let backup_paths = [
            Path::new(".agent/PROMPT.md.backup"),
            Path::new(".agent/PROMPT.md.backup.1"),
            Path::new(".agent/PROMPT.md.backup.2"),
        ];

        let prompt_path = Path::new("PROMPT.md");

        for backup_path in &backup_paths {
            let Some(backup_content) = read_backup_content_secure(backup_path) else {
                continue;
            };

            if backup_content.trim().is_empty() {
                continue;
            }

            if restore_prompt_content_atomic(prompt_path, backup_content.as_bytes()).is_err() {
                continue;
            }

            return true;
        }

        false
    }

    /// Check if any restoration events were detected and reset the flag.
    ///
    /// Returns true if PROMPT.md was deleted and restored since the last
    /// check. This is a one-time check - the flag is reset after reading.
    ///
    /// # Example
    ///
    /// ```no_run
    /// # use ralph_workflow::files::io::monitoring::PromptMonitor;
    /// # let mut monitor = PromptMonitor::new().unwrap();
    /// # monitor.start().unwrap();
    /// // After running some agent code
    /// if monitor.check_and_restore() {
    ///     println!("PROMPT.md was restored during this phase!");
    /// }
    /// ```
    #[must_use]
    pub fn check_and_restore(&self) -> bool {
        self.restoration_detected.swap(false, Ordering::AcqRel)
    }

    /// Drain any warnings produced by the monitor thread.
    #[must_use]
    pub fn drain_warnings(&self) -> Vec<String> {
        drain_warnings(&self.warnings)
    }

    /// Stop monitoring and cleanup resources.
    ///
    /// Signals the monitor thread to stop and waits for it to complete.
    #[must_use]
    pub fn stop(mut self) -> Vec<String> {
        // Signal the thread to stop
        self.stop_signal.store(true, Ordering::Release);

        // Wait for the thread to finish and check for panics
        if let Some(handle) = self.monitor_thread.take() {
            if let Err(panic_payload) = handle.join() {
                // Thread panicked - extract and log panic message for diagnostics
                // Try common panic payload types
                let panic_msg = panic_payload
                    .downcast_ref::<String>()
                    .cloned()
                    .or_else(|| {
                        panic_payload
                            .downcast_ref::<&str>()
                            .map(ToString::to_string)
                    })
                    .or_else(|| {
                        panic_payload
                            .downcast_ref::<&String>()
                            .map(|s| (*s).clone())
                    })
                    .unwrap_or_else(|| {
                        // Fallback: Try to get any available information
                        format!(
                            "<unknown panic type: {}>",
                            std::any::type_name_of_val(&panic_payload)
                        )
                    });
                push_warning(
                    &self.warnings,
                    format!("File monitoring thread panicked: {panic_msg}"),
                );
            }
        }

        self.drain_warnings()
    }
}

// ============================================================================
// Helper functions (boundary module - mutation and I/O permitted)
// ============================================================================

fn push_warning(warnings: &Arc<std::sync::Mutex<Vec<String>>>, warning: String) {
    if let Ok(mut guard) = warnings.lock() {
        guard.push(warning);
    }
}

fn drain_warnings(warnings: &Arc<std::sync::Mutex<Vec<String>>>) -> Vec<String> {
    warnings
        .lock()
        .map(|guard| std::mem::take(&mut guard.clone()))
        .unwrap_or_default()
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

// Tests are in tests/system_tests/file_protection/
