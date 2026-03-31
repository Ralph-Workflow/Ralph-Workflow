//! Capability mapping between MCP capabilities and Ralph capabilities.
//!
//! This module provides the pure policy functions for mapping McpCapability
//! values from the MCP protocol to Ralph's internal Capability model.

use crate::agents::session::{Capability, PolicyOutcome};
use mcp_server::dispatch::access::{AccessDecision, AccessDeniedCode, McpCapability};

// ---------------------------------------------------------------------------
// Static mapping table
// ---------------------------------------------------------------------------

/// Static mapping table from McpCapability to Ralph Capability.
///
/// Used by the capability policy to translate between MCP capabilities and Ralph
/// capabilities. Capabilities that require special handling (FileWrite, WorkspaceWriteAny,
/// WorkspaceCoordination) are not in this table - they return None.
const CAPABILITY_MAP: &[(McpCapability, Capability)] = &[
    // Minimum spec variants
    (McpCapability::FileRead, Capability::WorkspaceRead),
    (McpCapability::GitRead, Capability::GitStatusRead),
    (McpCapability::ProcessExec, Capability::ProcessExecBounded),
    (McpCapability::ArtifactSubmit, Capability::ArtifactSubmit),
    // Ralph-specific variants
    (McpCapability::WorkspaceRead, Capability::WorkspaceRead),
    (
        McpCapability::WorkspaceWriteEphemeral,
        Capability::WorkspaceWriteEphemeral,
    ),
    (
        McpCapability::WorkspaceWriteTracked,
        Capability::WorkspaceWriteTracked,
    ),
    (McpCapability::GitStatusRead, Capability::GitStatusRead),
    (McpCapability::GitWrite, Capability::GitWrite),
    (McpCapability::EnvRead, Capability::EnvRead),
    (McpCapability::EnvWrite, Capability::EnvWrite),
    (
        McpCapability::ProcessExecBounded,
        Capability::ProcessExecBounded,
    ),
    (
        McpCapability::ProcessExecUnbounded,
        Capability::ProcessExecUnbounded,
    ),
    (
        McpCapability::RunReportProgress,
        Capability::RunReportProgress,
    ),
];

// ---------------------------------------------------------------------------
// Pure policy helpers
// ---------------------------------------------------------------------------

/// Pure policy: look up Ralph Capability for a McpCapability.
///
/// Returns `None` for capabilities that need special handling
/// (FileWrite, WorkspaceWriteAny, WorkspaceCoordination).
pub(crate) fn lookup_ralph_capability(cap: McpCapability) -> Option<Capability> {
    CAPABILITY_MAP
        .iter()
        .find(|(mcp, _)| *mcp == cap)
        .map(|(_, ralph)| *ralph)
}

/// Policy: convert Ralph PolicyOutcome to AccessDecision.
pub(crate) fn policy_from_outcome(outcome: PolicyOutcome) -> AccessDecision {
    match outcome {
        PolicyOutcome::Approved => AccessDecision::Allow,
        PolicyOutcome::ApprovedWithRestriction { .. } => AccessDecision::Allow,
        PolicyOutcome::Denied { reason } => AccessDecision::Deny {
            reason,
            code: AccessDeniedCode::CapabilityDenied,
        },
    }
}

/// Policy: decide access for WorkspaceWriteAny capability.
pub(crate) fn decide_workspace_write_any(
    ephemeral_outcome: PolicyOutcome,
    tracked_outcome: PolicyOutcome,
) -> AccessDecision {
    let allowed = matches!(
        (ephemeral_outcome, tracked_outcome),
        (PolicyOutcome::Approved, _)
            | (_, PolicyOutcome::Approved)
            | (PolicyOutcome::ApprovedWithRestriction { .. }, _)
            | (_, PolicyOutcome::ApprovedWithRestriction { .. })
    );
    if allowed {
        AccessDecision::Allow
    } else {
        AccessDecision::Deny {
            reason: "Workspace write capability not granted".to_string(),
            code: AccessDeniedCode::CapabilityDenied,
        }
    }
}

/// Policy: decide access based on mapped capability outcome.
pub(crate) fn decide_from_mapped_outcome(outcome: PolicyOutcome) -> AccessDecision {
    policy_from_outcome(outcome)
}

/// Policy: deny unknown capabilities.
pub(crate) fn deny_unknown_capability(cap: McpCapability) -> AccessDecision {
    AccessDecision::Deny {
        reason: format!("Unknown capability: {:?}", cap),
        code: AccessDeniedCode::CapabilityDenied,
    }
}

// ---------------------------------------------------------------------------
// Thin capability policy (boundary function)
// ---------------------------------------------------------------------------

/// Boundary function: decide access for any capability given all session outcomes.
///
/// This is a thin boundary that routes to pure policy helpers based on capability type.
/// It gathers inputs and delegates all branching logic to pure functions.
pub(crate) fn capability_policy(
    cap: McpCapability,
    ephemeral: PolicyOutcome,
    tracked: PolicyOutcome,
    mapped: Option<(Capability, PolicyOutcome)>,
) -> AccessDecision {
    match cap {
        McpCapability::WorkspaceWriteAny | McpCapability::FileWrite => {
            decide_workspace_write_any(ephemeral, tracked)
        }
        McpCapability::WorkspaceCoordination => AccessDecision::Allow,
        _ => mapped.map_or_else(
            || deny_unknown_capability(cap),
            |(_c, o)| decide_from_mapped_outcome(o),
        ),
    }
}
