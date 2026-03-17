//! Mock cloud reporter for testing.
//!
//! This module uses interior mutability for test infrastructure because the
//! CloudReporter trait uses &self methods, requiring shared state tracking.
//! This is exempt from functional lints as it's test-only infrastructure.

use super::{CloudError, CloudReporter, PipelineResult, ProgressUpdate};
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone)]
pub enum MockCloudCall {
    Progress(ProgressUpdate),
    Heartbeat,
    Completion(PipelineResult),
}

/// Mock cloud reporter that records all calls for test verification.
#[derive(Clone)]
pub struct MockCloudReporter {
    calls: Arc<Mutex<Vec<MockCloudCall>>>,
    should_fail: Arc<Mutex<bool>>,
}

impl MockCloudReporter {
    #[must_use]
    pub fn new() -> Self {
        Self {
            calls: Arc::new(Mutex::new(Vec::new())),
            should_fail: Arc::new(Mutex::new(false)),
        }
    }

    pub fn set_should_fail(&self, fail: bool) {
        if let Ok(mut guard) = self.should_fail.lock() {
            *guard = fail;
        }
    }

    #[must_use]
    pub fn calls(&self) -> Vec<MockCloudCall> {
        self.calls
            .lock()
            .map(|guard| guard.clone())
            .unwrap_or_default()
    }

    #[must_use]
    pub fn progress_count(&self) -> usize {
        self.calls
            .lock()
            .map(|guard| {
                guard
                    .iter()
                    .filter(|c| matches!(c, MockCloudCall::Progress(_)))
                    .count()
            })
            .unwrap_or(0)
    }

    #[must_use]
    pub fn heartbeat_count(&self) -> usize {
        self.calls
            .lock()
            .map(|guard| {
                guard
                    .iter()
                    .filter(|c| matches!(c, MockCloudCall::Heartbeat))
                    .count()
            })
            .unwrap_or(0)
    }
}

impl Default for MockCloudReporter {
    fn default() -> Self {
        Self::new()
    }
}

impl CloudReporter for MockCloudReporter {
    fn report_progress(&self, update: &ProgressUpdate) -> Result<(), CloudError> {
        let should_fail = self.should_fail.lock().map(|guard| *guard).unwrap_or(false);

        if should_fail {
            return Err(CloudError::NetworkError("Mock failure".to_string()));
        }

        if let Ok(mut guard) = self.calls.lock() {
            guard.push(MockCloudCall::Progress(update.clone()));
        }
        Ok(())
    }

    fn heartbeat(&self) -> Result<(), CloudError> {
        let should_fail = self.should_fail.lock().map(|guard| *guard).unwrap_or(false);

        if should_fail {
            return Err(CloudError::NetworkError("Mock failure".to_string()));
        }

        if let Ok(mut guard) = self.calls.lock() {
            guard.push(MockCloudCall::Heartbeat);
        }
        Ok(())
    }

    fn report_completion(&self, result: &PipelineResult) -> Result<(), CloudError> {
        let should_fail = self.should_fail.lock().map(|guard| *guard).unwrap_or(false);

        if should_fail {
            return Err(CloudError::NetworkError("Mock failure".to_string()));
        }

        if let Ok(mut guard) = self.calls.lock() {
            guard.push(MockCloudCall::Completion(result.clone()));
        }
        Ok(())
    }
}
