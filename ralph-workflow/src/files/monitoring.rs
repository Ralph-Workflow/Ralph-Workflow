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

pub struct PromptMonitor {
    restoration_detected: Arc<AtomicBool>,
    stop_signal: Arc<AtomicBool>,
    monitor_thread: Option<thread::JoinHandle<()>>,
    warnings: Arc<std::sync::Mutex<Vec<String>>>,
}

impl PromptMonitor {
    pub fn new() -> std::io::Result<Self> {
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

    fn monitor_thread_main(
        restoration_detected: &Arc<AtomicBool>,
        stop_signal: &Arc<AtomicBool>,
        warnings: &Arc<std::sync::Mutex<Vec<String>>>,
    ) {
        use notify::Watcher;

        let (tx, rx) = bounded_event_queue();
        let event_sender = tx;

        let mut watcher = match notify::recommended_watcher(move |res| {
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
                Self::polling_monitor(restoration_detected, stop_signal);
                return;
            }
        };

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

        while !stop_signal.load(Ordering::Relaxed) {
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(Ok(event)) => {
                    Self::handle_fs_event(&event, restoration_detected);

                    while let Ok(next) = rx.try_recv() {
                        if let Ok(next_event) = next {
                            Self::handle_fs_event(&next_event, restoration_detected);
                        }
                    }
                }
                Ok(Err(_)) | Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                Err(_) => {
                    break;
                }
            }
        }
    }

    fn handle_fs_event(event: &notify::Event, restoration_detected: &Arc<AtomicBool>) {
        for path in &event.paths {
            if is_prompt_md_path(path) {
                if matches!(event.kind, notify::EventKind::Remove(_)) {
                    if Self::restore_from_backup() {
                        restoration_detected.store(true, Ordering::Release);
                    }
                }
            }
        }
    }

    fn polling_monitor(restoration_detected: &Arc<AtomicBool>, stop_signal: &Arc<AtomicBool>) {
        let check_deletion = || {
            let prompt_exists_now = Path::new("PROMPT.md").exists();
            (prompt_exists_now, prompt_exists_now)
        };

        let mut previous_exists = Path::new("PROMPT.md").exists();

        while !stop_signal.load(Ordering::Relaxed) {
            thread::sleep(Duration::from_millis(100));

            let (current_exists, _) = check_deletion();

            if previous_exists && !current_exists && Self::restore_from_backup() {
                restoration_detected.store(true, Ordering::Release);
            }

            previous_exists = current_exists;
        }
    }

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

    #[must_use]
    pub fn check_and_restore(&self) -> bool {
        self.restoration_detected.swap(false, Ordering::AcqRel)
    }

    #[must_use]
    pub fn drain_warnings(&self) -> Vec<String> {
        drain_warnings(&self.warnings)
    }

    #[must_use]
    pub fn stop(mut self) -> Vec<String> {
        self.stop_signal.store(true, Ordering::Release);

        if let Some(handle) = self.monitor_thread.take() {
            if let Err(panic_payload) = handle.join() {
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

    if let Ok(meta) = fs::symlink_metadata(prompt_path) {
        if meta.is_dir() {
            return Err(std::io::Error::other("PROMPT.md path is a directory"));
        }
    }

    let temp_name = unique_temp_name();
    let temp_path = Path::new(&temp_name);

    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(temp_path)?;
    file.write_all(content)?;
    file.flush()?;
    let _ = file.sync_all();
    drop(file);

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

    #[cfg(windows)]
    {
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
        self.stop_signal.store(true, Ordering::Release);

        let _ = self.monitor_thread.take();
    }
}
