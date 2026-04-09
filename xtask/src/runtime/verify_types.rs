use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerifyExitCode {
    Success = 0,
    Failure = 1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CheckStatus {
    Pass,
    Warning,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum FailurePriority {
    Scan = 0,
    Fmt = 1,
    CoreCargo = 2,
    XtaskCargo = 3,
    Release = 4,
}

pub struct CancellationState {
    highest_priority_failure: AtomicUsize,
}

impl Default for CancellationState {
    fn default() -> Self {
        Self::new()
    }
}

impl CancellationState {
    const NO_FAILURE: usize = usize::MAX;

    pub fn new() -> Self {
        Self {
            highest_priority_failure: AtomicUsize::new(Self::NO_FAILURE),
        }
    }

    pub fn record_failure(&self, priority: FailurePriority) {
        let priority = priority as usize;
        let _ = self.highest_priority_failure.fetch_update(
            Ordering::SeqCst,
            Ordering::SeqCst,
            |current| (priority < current).then_some(priority),
        );
    }

    pub fn should_cancel(&self, priority: FailurePriority) -> bool {
        self.highest_priority_failure.load(Ordering::SeqCst) < priority as usize
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckFailure {
    pub name: &'static str,
    pub status: CheckStatus,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifyReport {
    pub exit: VerifyExitCode,
    pub failure: Option<CheckFailure>,
}

pub struct NativeCheckResult {
    pub status: CheckStatus,
    pub message: String,
}

pub struct NativeCheck {
    pub name: &'static str,
    pub run: fn(&std::path::Path) -> NativeCheckResult,
}
