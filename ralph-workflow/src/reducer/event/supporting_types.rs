//! Supporting types for event definitions.
//!
//! This module contains enums and types that support event definitions
//! but are not events themselves.

use serde::{Deserialize, Serialize};

/// Checkpoint save trigger.
///
/// Records what caused a checkpoint to be saved, enabling analysis of
/// checkpoint patterns and frequency.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CheckpointTrigger {
    /// Checkpoint saved during phase transition.
    PhaseTransition,
    /// Checkpoint saved after iteration completion.
    IterationComplete,
    /// Checkpoint saved before risky operation (rebase).
    BeforeRebase,
    /// Checkpoint saved due to interrupt signal.
    Interrupt,
}

/// Error kind for agent failures.
///
/// Classifies agent invocation failures to enable retry/fallback decisions in the reducer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AgentErrorKind {
    /// Network connectivity failure.
    Network,
    /// Authentication or authorization failure.
    Authentication,
    /// Rate limiting or quota exceeded.
    RateLimit,
    /// Request timeout.
    Timeout,
    /// Internal server error from agent API.
    InternalError,
    /// Requested model is unavailable.
    ModelUnavailable,
    /// Output parsing or validation error.
    ParsingError,
    /// Filesystem error during agent invocation.
    FileSystem,
}

/// Whether a timed-out agent produced a result file before being cut off.
///
/// Carried in `AgentEvent::TimedOut` so the reducer can apply different retry
/// policies based on the state of the expected output file.
///
/// # Classification Logic
///
/// - `NoResult`: No result file was produced at all — agent likely crashed, hit an
///   auth/API failure, or was never able to start work.
/// - `PartialResult`: Result file exists on disk but is not valid XML — agent started
///   work but was interrupted before writing a complete, parseable result.
///
/// Note: a *completed* result (valid file) is NOT represented here — that case is
/// promoted to `AgentInvocationSucceeded` before this enum is consulted.
///
/// # Serde Backward Compatibility
///
/// Old checkpoints used "NoOutput" and "PartialOutput"; serde aliases map those
/// serialized strings to the renamed variants.
///
/// Old checkpoints did not carry `output_kind`; the field uses an explicit default
/// function (`default_timeout_output_kind`) that defaults to `PartialResult` to
/// preserve pre-feature retry behavior (same-agent retry, not immediate switch).
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum TimeoutOutputKind {
    /// No result file was produced — likely a crash, auth failure, or network issue.
    #[serde(alias = "NoOutput")]
    NoResult,
    /// Result file exists but is invalid or incomplete — agent was interrupted mid-work.
    #[serde(alias = "PartialOutput")]
    PartialResult,
}

/// Default function for serde backward compatibility.
///
/// Old checkpoints did not carry `output_kind`; default to `PartialResult`
/// to preserve pre-feature retry behavior (same-agent retry, not immediate switch).
#[must_use]
pub const fn default_timeout_output_kind() -> TimeoutOutputKind {
    TimeoutOutputKind::PartialResult
}
