// Lock management and state transition helpers for rebase operations.
//
// This file contains the RebaseLock RAII guard and lock acquisition/release functions.
// Lock file I/O operations have been moved to the runtime boundary module.

use crate::git_helpers::runtime::lock::{acquire_rebase_lock, release_rebase_lock};

/// RAII-style guard for rebase lock.
///
/// Automatically releases the lock when dropped.
pub struct RebaseLock {
    /// Whether we own the lock
    owns_lock: bool,
}

impl Drop for RebaseLock {
    fn drop(&mut self) {
        if self.owns_lock {
            let _ = release_rebase_lock();
        }
    }
}

impl RebaseLock {
    /// Create a new lock guard that owns the lock.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn new() -> std::io::Result<Self> {
        acquire_rebase_lock()?;
        Ok(Self { owns_lock: true })
    }

    /// Relinquish ownership of the lock without releasing it.
    ///
    /// This is useful when transferring ownership.
    #[must_use]
    #[cfg(any(test, feature = "test-utils"))]
    pub fn leak(mut self) -> bool {
        let owned = self.owns_lock;
        self.owns_lock = false;
        owned
    }
}

