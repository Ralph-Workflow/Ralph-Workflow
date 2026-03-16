//! Interrupt signal handling for graceful checkpoint save.
//!
//! This module provides signal handling for the Ralph pipeline, ensuring
//! clean shutdown when the user interrupts with Ctrl+C.
//!
//! When an interrupt is received:
//!
//! - If the reducer event loop is running, the handler sets a global interrupt request
//!   flag and returns. The event loop consumes that flag and performs the reducer-driven
//!   termination sequence (`RestorePromptPermissions` -> `SaveCheckpoint` -> shutdown).
//! - If the event loop is not running yet (early startup), the handler falls back to a
//!   best-effort checkpoint save and exits with the standard SIGINT code (130).
//!
//! ## Ctrl+C Exception for Safety Check
//!
//! The `interrupted_by_user` flag distinguishes user-initiated interrupts (Ctrl+C)
//! from programmatic interrupts (`AwaitingDevFix` exhaustion, completion marker emission).
//! When set to `true`, the pre-termination commit safety check is skipped because
//! the user explicitly chose to interrupt execution. This respects user intent while
//! ensuring all other termination paths commit uncommitted work before exiting.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;

use std::path::Path;

pub(crate) mod checkpoint;

pub use checkpoint::InterruptContext;

/// Global interrupt context for checkpoint saving on interrupt.
///
/// This is set during pipeline initialization and used by the interrupt
/// handler to save a checkpoint when the user presses Ctrl+C.
pub(crate) static INTERRUPT_CONTEXT: Mutex<Option<InterruptContext>> = Mutex::new(None);

/// True when a user interrupt (SIGINT / Ctrl+C) has been requested.
///
/// The signal handler sets this flag. The reducer event loop consumes it and
/// transitions the pipeline to an Interrupted state so termination effects
/// (`RestorePromptPermissions`, `SaveCheckpoint`) execute deterministically.
static USER_INTERRUPT_REQUESTED: AtomicBool = AtomicBool::new(false);

/// True once a user interrupt has occurred during this process lifetime.
///
/// Unlike `USER_INTERRUPT_REQUESTED`, this flag is NEVER cleared. It remains
/// set even after the event loop consumes the pending interrupt request via
/// `take_user_interrupt_request()`. Use this flag in shutdown code paths
/// (e.g., `capture_git_state`) where you need to know whether the process is
/// shutting down due to Ctrl+C, even after the pending request has been consumed.
static USER_INTERRUPTED_OCCURRED: AtomicBool = AtomicBool::new(false);

/// True while the reducer event loop is running.
///
/// When true, the Ctrl+C handler must NOT call `process::exit()`.
/// Instead it requests interruption and lets the event loop drive:
/// - `RestorePromptPermissions`
/// - `SaveCheckpoint`
/// - orderly shutdown
pub(crate) static EVENT_LOOP_ACTIVE: AtomicBool = AtomicBool::new(false);

/// Number of SIGINTs received while the reducer event loop is active.
///
/// First Ctrl+C requests graceful reducer-driven shutdown.
/// Second Ctrl+C forces immediate process exit to avoid indefinite hangs.
pub(crate) static EVENT_LOOP_ACTIVE_SIGINT_COUNT: AtomicUsize = AtomicUsize::new(0);

/// True when the process should exit with code 130 after the pipeline returns.
///
/// We intentionally do not call `process::exit(130)` from inside the pipeline runner,
/// because that would bypass Rust destructors (RAII cleanup like `AgentPhaseGuard::drop()`).
/// Instead, the pipeline requests this exit code and `main()` performs the actual
/// exit after stack unwinding and cleanup completes.
static EXIT_130_AFTER_RUN: AtomicBool = AtomicBool::new(false);

/// Request that the process exit with code 130 once the pipeline returns.
pub fn request_exit_130_after_run() {
    EXIT_130_AFTER_RUN.store(true, Ordering::SeqCst);
}

/// Consume a pending exit-130 request.
pub fn take_exit_130_after_run() -> bool {
    EXIT_130_AFTER_RUN.swap(false, Ordering::SeqCst)
}

#[cfg(unix)]
fn restore_prompt_md_writable_via_std_fs() {
    use std::os::unix::fs::PermissionsExt;

    fn make_writable(path: &std::path::Path) -> bool {
        let Ok(metadata) = std::fs::metadata(path) else {
            return false;
        };

        let mut perms = metadata.permissions();
        // Preserve existing mode bits but ensure owner write is enabled.
        perms.set_mode(perms.mode() | 0o200);
        std::fs::set_permissions(path, perms).is_ok()
    }

    // Fast path: current working directory is already the repo root in normal runs.
    if make_writable(std::path::Path::new("PROMPT.md")) {
        return;
    }

    // Fallback: discover repo root.
    let Ok(repo_root) = crate::git_helpers::get_repo_root() else {
        return;
    };

    let prompt_path = repo_root.join("PROMPT.md");
    let _ = make_writable(&prompt_path);
}

fn remove_repo_root_ralph_dir_via_std_fs() {
    let repo_root = INTERRUPT_CONTEXT
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .as_ref()
        .map(|context| context.workspace.root().to_path_buf())
        .or_else(|| crate::git_helpers::get_repo_root().ok());

    if let Some(repo_root) = repo_root {
        let _ = std::fs::remove_dir_all(repo_root.join(".git/ralph"));
    }
}

#[cfg(not(unix))]
fn restore_prompt_md_writable_via_std_fs() {}

/// RAII guard that marks the reducer event loop as active.
pub struct EventLoopActiveGuard;

impl Drop for EventLoopActiveGuard {
    fn drop(&mut self) {
        EVENT_LOOP_ACTIVE.store(false, Ordering::SeqCst);
        EVENT_LOOP_ACTIVE_SIGINT_COUNT.store(0, Ordering::SeqCst);
    }
}

/// Mark the reducer event loop as active for the duration of the returned guard.
pub fn event_loop_active_guard() -> EventLoopActiveGuard {
    EVENT_LOOP_ACTIVE_SIGINT_COUNT.store(0, Ordering::SeqCst);
    EVENT_LOOP_ACTIVE.store(true, Ordering::SeqCst);
    EventLoopActiveGuard
}

fn is_event_loop_active() -> bool {
    EVENT_LOOP_ACTIVE.load(Ordering::SeqCst)
}

pub(crate) fn register_sigint_during_active_event_loop() -> bool {
    // Returns true on second (or later) SIGINT while event loop is active.
    let count = EVENT_LOOP_ACTIVE_SIGINT_COUNT.fetch_add(1, Ordering::SeqCst) + 1;
    count >= 2
}

/// Request that the running pipeline treat the run as user-interrupted.
///
/// This is called by the Ctrl+C handler. The event loop is responsible for
/// consuming the request and translating it into a reducer-visible transition.
///
/// Also sets the persistent `USER_INTERRUPTED_OCCURRED` flag, which is never
/// cleared and allows shutdown code paths (e.g., `capture_git_state`) to
/// detect the interrupt even after the event loop has consumed the pending
/// request via `take_user_interrupt_request()`.
pub fn request_user_interrupt() {
    USER_INTERRUPT_REQUESTED.store(true, Ordering::SeqCst);
    USER_INTERRUPTED_OCCURRED.store(true, Ordering::SeqCst);
}

/// Check if a user interrupt has occurred at any point during this process lifetime.
///
/// Returns true once a Ctrl+C has been received, and remains true for the rest
/// of the process lifetime even after `take_user_interrupt_request()` has consumed
/// the pending request.
///
/// Use this in shutdown code paths where you need to know whether the process
/// is shutting down due to user interruption, even if the event loop has already
/// consumed the interrupt request. For example, `capture_git_state` uses this
/// to skip git commands that could hang indefinitely during interrupt-triggered
/// shutdown.
pub fn user_interrupted_occurred() -> bool {
    USER_INTERRUPTED_OCCURRED.load(Ordering::SeqCst)
}

/// Check if a user interrupt request is pending without consuming it.
///
/// Returns true if an interrupt is pending. The flag remains set so that
/// the event loop can still consume it via `take_user_interrupt_request()`.
///
/// Use this when you need to react to an interrupt (e.g., kill a subprocess)
/// without stealing the flag from the event loop's per-iteration check.
pub fn is_user_interrupt_requested() -> bool {
    USER_INTERRUPT_REQUESTED.load(Ordering::SeqCst)
}

/// Consume a pending user interrupt request.
///
/// Returns true if an interrupt was pending.
pub fn take_user_interrupt_request() -> bool {
    USER_INTERRUPT_REQUESTED.swap(false, Ordering::SeqCst)
}

/// Reset the persistent user-interrupted flag.
///
/// Only intended for use in tests to restore a clean state between test cases
/// that exercise interrupt behavior. Production code must not call this.
#[cfg(test)]
pub fn reset_user_interrupted_occurred() {
    USER_INTERRUPTED_OCCURRED.store(false, Ordering::SeqCst);
}

/// Global mutex used by tests to serialize access to the process-global interrupt flags.
///
/// The interrupt flags are process-global (`static` atomics). Rust unit tests run in
/// parallel by default, so tests that call `request_user_interrupt()`,
/// `take_user_interrupt_request()`, or `reset_user_interrupted_occurred()` can interfere
/// with each other unless they coordinate.
///
/// This lock should be held for the full duration of any test that:
/// - sets or consumes the interrupt request flag, or
/// - requires the interrupt flags to remain in a known state while exercising behavior.
///
/// Production code must not use this.
#[cfg(test)]
static TEST_INTERRUPT_LOCK: Mutex<()> = Mutex::new(());

#[cfg(test)]
pub(crate) fn interrupt_test_lock() -> std::sync::MutexGuard<'static, ()> {
    TEST_INTERRUPT_LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Set the global interrupt context.
///
/// This function should be called during pipeline initialization to
/// provide the interrupt handler with the context needed to save
/// a checkpoint when interrupted.
///
/// # Arguments
///
/// * `context` - The interrupt context to store
///
/// # Note
///
/// This function is typically called at the start of `run_pipeline()`
/// to ensure the interrupt handler has the most up-to-date context.
pub fn set_interrupt_context(context: InterruptContext) {
    let mut ctx = INTERRUPT_CONTEXT.lock().unwrap_or_else(|poison| {
        // If mutex is poisoned, recover the guard and clear the state
        poison.into_inner()
    });
    *ctx = Some(context);
}

/// Clear the global interrupt context.
///
/// This should be called when the pipeline completes successfully
/// to prevent saving an interrupt checkpoint after normal completion.
pub fn clear_interrupt_context() {
    let mut ctx = INTERRUPT_CONTEXT.lock().unwrap_or_else(|poison| {
        // If mutex is poisoned, recover the guard and clear the state
        poison.into_inner()
    });
    *ctx = None;
}

/// Set up the interrupt handler for graceful shutdown with checkpoint saving.
///
/// This function registers a SIGINT handler that will:
/// 1. Save a checkpoint with the current pipeline state
/// 2. Clean up generated files
/// 3. Exit gracefully
///
/// Call this early in `main()` after initializing the pipeline context.
#[expect(clippy::print_stderr, reason = "critical interrupt handling messages")]
pub fn setup_interrupt_handler() {
    let install = ctrlc::set_handler(|| {
        request_user_interrupt();

        // If the reducer event loop is running, do not exit here.
        // The event loop will observe the request, restore permissions, and checkpoint.
        if is_event_loop_active() {
            if register_sigint_during_active_event_loop() {
                eprintln!("\nSecond interrupt received; forcing immediate exit.");
                restore_prompt_md_writable_via_std_fs();
                eprintln!("Cleaning up...");
                crate::git_helpers::cleanup_agent_phase_silent();
                remove_repo_root_ralph_dir_via_std_fs();
                std::process::exit(130);
            }

            eprintln!(
                "\nInterrupt received; requesting graceful shutdown (waiting for checkpoint)..."
            );
            return;
        }

        eprintln!("\nInterrupt received; saving checkpoint...");

        // Clone the entire context (small, Arc-backed) and then perform I/O without
        // holding the mutex.
        let context = {
            let ctx = INTERRUPT_CONTEXT
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            ctx.clone()
        };

        if let Some(ref context) = context {
            if let Err(e) = checkpoint::save_interrupt_checkpoint(context) {
                eprintln!("Warning: Failed to save checkpoint: {e}");
            } else {
                eprintln!("Checkpoint saved. Resume with: ralph --resume");
            }
        }

        // Best-effort: restore PROMPT.md permissions so we don't leave the repo locked.
        // This is primarily for early-interrupt cases before the reducer event loop starts.
        //
        // Always attempt a std::fs fallback using repo discovery. This covers:
        // - interrupt context not yet installed (very early SIGINT)
        // - workspace implementations that cannot mutate real filesystem permissions
        //   (e.g., MemoryWorkspace)
        restore_prompt_md_writable_via_std_fs();

        if let Some(ref context) = context {
            let _ = context.workspace.set_writable(Path::new("PROMPT.md"));
        }

        eprintln!("Cleaning up...");
        crate::git_helpers::cleanup_agent_phase_silent();
        remove_repo_root_ralph_dir_via_std_fs();
        std::process::exit(130); // Standard exit code for SIGINT
    });

    if let Err(e) = install {
        // Handler installation failure is a reliability issue: without it, Ctrl+C will not
        // trigger checkpointing/cleanup and can leave the repo in a broken state.
        eprintln!("Warning: failed to install Ctrl+C handler: {e}");
    }
}

#[cfg(test)]
mod tests {
    include!("tests.rs");
}
