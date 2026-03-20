//! Rebase lock management in the runtime boundary.
//!
//! This module provides lock file operations for rebase operations.
//! All functions here may use mutation, imperative loops, and I/O.

use std::fs;
use std::path::Path;

/// Rebase lock file name.
const REBASE_LOCK_FILE: &str = "rebase.lock";

/// Default lock timeout in seconds (30 minutes).
const DEFAULT_LOCK_TIMEOUT_SECONDS: u64 = 1800;

/// Get the rebase lock file path.
///
/// The lock is stored in `.agent/rebase.lock`
/// relative to the current working directory.
fn rebase_lock_path() -> String {
    format!(".agent/{REBASE_LOCK_FILE}")
}

fn build_lock_content() -> String {
    let pid = std::process::id();
    let timestamp = chrono::Utc::now().to_rfc3339();
    format!("pid={pid}\ntimestamp={timestamp}\n")
}

fn should_acquire_lock(lock_path: &Path) -> std::io::Result<bool> {
    if !lock_path.exists() {
        return Ok(true);
    }
    is_lock_stale().map(|stale| stale)
}

/// Acquire the rebase lock.
///
/// Creates a lock file with the current process ID and timestamp.
/// Returns an error if the lock is held by another process.
///
/// # Errors
///
/// Returns an error if:
/// - The lock file exists and is not stale
/// - The lock file cannot be created
pub fn acquire_rebase_lock() -> std::io::Result<()> {
    let lock_path_str = rebase_lock_path();
    let lock_path = Path::new(&lock_path_str);

    if let Some(parent) = lock_path.parent() {
        fs::create_dir_all(parent)?;
    }

    if !should_acquire_lock(lock_path)? {
        return Err(lock_already_held_error());
    }

    if lock_path.exists() {
        fs::remove_file(lock_path)?;
    }

    let lock_content = build_lock_content();
    let mut file = fs::File::create(lock_path)?;
    std::io::Write::write_all(&mut file, lock_content.as_bytes())?;
    file.sync_all()?;

    Ok(())
}

fn lock_already_held_error() -> std::io::Error {
    std::io::Error::new(
        std::io::ErrorKind::PermissionDenied,
        "Rebase is already in progress. If you believe this is incorrect, \
         wait 30 minutes for the lock to expire or manually remove `.agent/rebase.lock`.",
    )
}

/// Release the rebase lock.
///
/// Removes the lock file. Does nothing if no lock exists.
///
/// # Errors
///
/// Returns an error if the lock file exists but cannot be removed.
pub fn release_rebase_lock() -> std::io::Result<()> {
    let lock_path = rebase_lock_path();
    let path = Path::new(&lock_path);

    if path.exists() {
        fs::remove_file(path)?;
    }

    Ok(())
}

/// Check if the lock file is stale.
///
/// A lock is considered stale if it's older than the timeout period.
///
/// # Returns
///
/// Returns `true` if the lock is stale, `false` otherwise.
fn is_lock_stale() -> std::io::Result<bool> {
    let lock_path = rebase_lock_path();
    let path = Path::new(&lock_path);

    if !path.exists() {
        return Ok(false);
    }

    let content = fs::read_to_string(path)?;
    let timestamp = parse_lock_timestamp(&content)?;
    let now = chrono::Utc::now();
    let elapsed = now.signed_duration_since(timestamp);
    let timeout_seconds = i64::try_from(DEFAULT_LOCK_TIMEOUT_SECONDS).unwrap_or(i64::MAX);

    Ok(elapsed.num_seconds() > timeout_seconds)
}

fn parse_lock_timestamp(content: &str) -> std::io::Result<chrono::DateTime<chrono::Utc>> {
    let timestamp_line = content
        .lines()
        .find(|line| line.starts_with("timestamp="))
        .ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "Lock file missing timestamp",
            )
        })?;

    let timestamp_str = timestamp_line.strip_prefix("timestamp=").ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "Invalid timestamp format in lock file",
        )
    })?;

    chrono::DateTime::parse_from_rfc3339(timestamp_str)
        .map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "Invalid timestamp format in lock file",
            )
        })
        .map(|dt| dt.with_timezone(&chrono::Utc))
}
