//! Capability mapping between MCP capabilities and Ralph capabilities.
//!
//! This module provides pure translation functions for mapping McpCapability
//! values from the MCP protocol to Ralph's internal Capability model.
//!
//! It also provides thin boundary functions that wire capability checking inputs
//! to pure domain policy helpers.

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
// Pure translation functions
// ---------------------------------------------------------------------------

/// Pure translation: look up Ralph Capability for a McpCapability.
///
/// Returns `None` for capabilities that need special handling
/// (FileWrite, WorkspaceWriteAny, WorkspaceCoordination).
pub(crate) fn lookup_ralph_capability(cap: McpCapability) -> Option<Capability> {
    CAPABILITY_MAP
        .iter()
        .find(|(mcp, _)| *mcp == cap)
        .map(|(_, ralph)| *ralph)
}

/// Pure translation: convert Ralph PolicyOutcome to AccessDecision.
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

// ---------------------------------------------------------------------------
// Pure domain policy helpers (no wiring, no side effects)
// ---------------------------------------------------------------------------

/// Pure domain policy: evaluate whether workspace write access is allowed
/// based on ephemeral and tracked write outcomes.
fn evaluate_workspace_write(ephemeral: PolicyOutcome, tracked: PolicyOutcome) -> AccessDecision {
    let allowed = matches!(
        (ephemeral, tracked),
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

/// Pure domain policy: evaluate access for a mapped Ralph capability.
fn evaluate_mapped_capability(
    cap: McpCapability,
    mapped_outcome: Option<(Capability, PolicyOutcome)>,
) -> AccessDecision {
    mapped_outcome.map_or_else(
        || AccessDecision::Deny {
            reason: format!("Unknown capability: {:?}", cap),
            code: AccessDeniedCode::CapabilityDenied,
        },
        |(_, outcome)| policy_from_outcome(outcome),
    )
}

// ---------------------------------------------------------------------------
// Thin boundary function (wiring only)
// ---------------------------------------------------------------------------

/// Thin boundary: decide access for a McpCapability given session capability outcomes.
///
/// This function is the boundary seam — it only wires inputs together and delegates
/// to pure domain policy helpers. All branching policy lives in the domain helpers.
pub(crate) fn check_mcp_capability_policy(
    cap: McpCapability,
    ephemeral: PolicyOutcome,
    tracked: PolicyOutcome,
    mapped_outcome: Option<(Capability, PolicyOutcome)>,
) -> AccessDecision {
    match cap {
        // Workspace write capabilities use Ralph's special policy: either ephemeral OR
        // tracked write approved means access is granted.
        McpCapability::WorkspaceWriteAny | McpCapability::FileWrite => {
            evaluate_workspace_write(ephemeral, tracked)
        }
        // Workspace coordination is always allowed.
        McpCapability::WorkspaceCoordination => AccessDecision::Allow,
        // All other capabilities are evaluated based on their mapped Ralph capability.
        // Each variant is listed explicitly so that adding a new McpCapability variant
        // without updating this mapping produces a compile error rather than a silent
        // fall-through. The `_` arm below is required only because McpCapability is
        // #[non_exhaustive] (defined in a different crate), and will catch genuinely
        // unknown future variants with an explicit deny.
        McpCapability::FileRead
        | McpCapability::GitRead
        | McpCapability::ProcessExec
        | McpCapability::ArtifactSubmit
        | McpCapability::WorkspaceRead
        | McpCapability::WorkspaceWriteEphemeral
        | McpCapability::WorkspaceWriteTracked
        | McpCapability::GitStatusRead
        | McpCapability::GitWrite
        | McpCapability::EnvRead
        | McpCapability::EnvWrite
        | McpCapability::ProcessExecBounded
        | McpCapability::ProcessExecUnbounded
        | McpCapability::RunReportProgress => evaluate_mapped_capability(cap, mapped_outcome),
        // Required wildcard arm for #[non_exhaustive] McpCapability — deny any future
        // variant added to mcp-server that ralph-workflow has not yet been updated to handle.
        _ => AccessDecision::Deny {
            reason: format!(
                "Unrecognized McpCapability {:?}: ralph-workflow has not been updated to \
                handle this capability variant",
                cap
            ),
            code: AccessDeniedCode::CapabilityDenied,
        },
    }
}
