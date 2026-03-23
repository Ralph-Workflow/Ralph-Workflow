//! Boundary shim re-exporting executor types for crate-root API.
//!
//! This module lives under `boundary/` so it can safely import the executor
//! boundary while exposing the types through a non-boundary module path.

pub use crate::executor::{
    AgentChild, AgentChildHandle, AgentCommandResult, AgentSpawnConfig, ChildProcessInfo,
    ProcessExecutor, ProcessOutput, RealAgentChild, RealProcessExecutor,
};

#[cfg(any(test, feature = "test-utils"))]
pub use crate::executor::{MockAgentChild, MockProcessExecutor};
