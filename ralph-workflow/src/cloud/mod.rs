//! Cloud integration for containerized deployments.
//!
//! This module provides abstractions for ralph-workflow to run in cloud
//! environments with external orchestration. All cloud functionality is:
//!
//! - **Environment-variable configured only** (not in config files)
//! - **Disabled by default**
//! - **Invisible to CLI users** (no CLI flags, no help text)
//! - **Purely additive** (zero behavior change when disabled)
//!
//! ## Architecture
//!
//! Cloud support is trait-based for testability:
//! - `CloudReporter` - Abstract interface for progress reporting
//! - `NoopCloudReporter` - Default (does nothing)
//! - `HttpCloudReporter` - Production HTTP API client (in io boundary)
//! - `MockCloudReporter` - Testing (captures calls)
//!
//! ## Boundary Modules
//!
//! - `runtime/` - Thread spawning for heartbeat background task
//! - `io/` - HTTP client for cloud API communication

pub mod http;
pub mod io_redaction;
pub mod redaction;
pub mod runtime;
pub mod types;

pub use http::HttpCloudReporter;
pub use runtime::HeartbeatGuard;
pub use types::{CloudError, PipelineResult, ProgressEventType, ProgressUpdate};

pub trait CloudReporter: Send + Sync {
    fn report_progress(&self, update: &ProgressUpdate) -> Result<(), CloudError>;
    fn heartbeat(&self) -> Result<(), CloudError>;
    fn report_completion(&self, result: &PipelineResult) -> Result<(), CloudError>;
}

pub struct NoopCloudReporter;

impl CloudReporter for NoopCloudReporter {
    fn report_progress(&self, _update: &ProgressUpdate) -> Result<(), CloudError> {
        Ok(())
    }

    fn heartbeat(&self) -> Result<(), CloudError> {
        Ok(())
    }

    fn report_completion(&self, _result: &PipelineResult) -> Result<(), CloudError> {
        Ok(())
    }
}

#[cfg(any(test, feature = "test-utils"))]
pub mod mock;
#[cfg(any(test, feature = "test-utils"))]
pub use mock::MockCloudReporter;
