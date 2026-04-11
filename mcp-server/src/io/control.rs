use std::sync::mpsc;
use thiserror::Error;

/// Required length for the private policy challenge phrase.
pub const POLICY_CHALLENGE_LENGTH: usize = 256;

/// Sender half of the private control channel.
pub type ControlSender = mpsc::Sender<ControlRequest>;

/// Receiver half of the private control channel.
pub type ControlReceiver = mpsc::Receiver<ControlRequest>;

/// Control commands sent by the orchestrator over the private channel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlCommand {
    /// Switch the MCP runtime mode.
    ModeSwitch {
        /// Mode identifier provided by the orchestrator.
        mode: String,
    },
    /// Request the MCP server to begin shutdown.
    Shutdown,
    /// A heartbeat ACK from the orchestrator to reset the monitor.
    HeartbeatAck,
}

/// Result returned by the control request handler.
pub type ControlResult = Result<(), ControlError>;

/// Error returned when a control request could not be processed.
#[derive(Debug, Error)]
pub enum ControlError {
    /// The control channel has been closed (server stopped).
    #[error("control channel closed")]
    ChannelClosed,
    /// Access to the channel was denied.
    #[error("access denied: {0}")]
    AccessDenied(String),
    /// Policy challenge string is not available.
    #[error("policy challenge unavailable")]
    ChallengeMissing,
    /// The command was rejected for another reason.
    #[error("control command rejected: {0}")]
    Rejected(String),
}

/// Request sent over the control channel.
pub struct ControlRequest {
    /// Challenge phrase proving the caller is the orchestrator.
    pub challenge: String,
    /// Command payload.
    pub command: ControlCommand,
    /// Identity of the requester (for audit correlation).
    pub requester_id: String,
    /// Optional machine-readable requester context (serialized JSON string).
    pub requester_context: Option<String>,
    /// Channel to send the response back to the caller.
    pub response: mpsc::Sender<ControlResult>,
}
