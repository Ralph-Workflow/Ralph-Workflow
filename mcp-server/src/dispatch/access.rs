//! Pure access control types for MCP server dispatch layer.
//!
//! This module contains the typed access control model types that have no I/O
//! dependencies. These types can be safely used in non-boundary modules.

/// Typed MCP capabilities - replaces string-based capability checking.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[non_exhaustive]
pub enum McpCapability {
    WorkspaceRead,
    WorkspaceWriteEphemeral,
    WorkspaceWriteTracked,
    GitStatusRead,
    GitWrite,
    EnvRead,
    EnvWrite,
    ProcessExecBounded,
    ProcessExecUnbounded,
    ArtifactSubmit,
}

impl McpCapability {
    pub fn as_str(&self) -> &'static str {
        match self {
            McpCapability::WorkspaceRead => "WorkspaceRead",
            McpCapability::WorkspaceWriteEphemeral => "WorkspaceWriteEphemeral",
            McpCapability::WorkspaceWriteTracked => "WorkspaceWriteTracked",
            McpCapability::GitStatusRead => "GitStatusRead",
            McpCapability::GitWrite => "GitWrite",
            McpCapability::EnvRead => "EnvRead",
            McpCapability::EnvWrite => "EnvWrite",
            McpCapability::ProcessExecBounded => "ProcessExecBounded",
            McpCapability::ProcessExecUnbounded => "ProcessExecUnbounded",
            McpCapability::ArtifactSubmit => "ArtifactSubmit",
        }
    }

    pub fn try_from_str(s: &str) -> Option<Self> {
        match s {
            "WorkspaceRead" => Some(McpCapability::WorkspaceRead),
            "WorkspaceWriteEphemeral" => Some(McpCapability::WorkspaceWriteEphemeral),
            "WorkspaceWriteTracked" => Some(McpCapability::WorkspaceWriteTracked),
            "GitStatusRead" => Some(McpCapability::GitStatusRead),
            "GitWrite" => Some(McpCapability::GitWrite),
            "EnvRead" => Some(McpCapability::EnvRead),
            "EnvWrite" => Some(McpCapability::EnvWrite),
            "ProcessExecBounded" => Some(McpCapability::ProcessExecBounded),
            "ProcessExecUnbounded" => Some(McpCapability::ProcessExecUnbounded),
            "ArtifactSubmit" => Some(McpCapability::ArtifactSubmit),
            _ => None,
        }
    }

    pub fn is_write(&self) -> bool {
        matches!(
            self,
            McpCapability::WorkspaceWriteEphemeral
                | McpCapability::WorkspaceWriteTracked
                | McpCapability::GitWrite
                | McpCapability::EnvWrite
        )
    }

    pub fn is_read(&self) -> bool {
        matches!(
            self,
            McpCapability::WorkspaceRead | McpCapability::GitStatusRead | McpCapability::EnvRead
        )
    }

    pub fn is_git(&self) -> bool {
        matches!(self, McpCapability::GitStatusRead | McpCapability::GitWrite)
    }

    pub fn is_process(&self) -> bool {
        matches!(
            self,
            McpCapability::ProcessExecBounded | McpCapability::ProcessExecUnbounded
        )
    }
}

impl std::fmt::Display for McpCapability {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AccessMode {
    #[default]
    Locked,
    ReadOnly,
    EphemeralOnly,
    ReadWrite,
}

impl AccessMode {
    pub fn allows(&self, capability: McpCapability) -> bool {
        use McpCapability::*;
        match (self, capability) {
            (AccessMode::Locked, _) => false,
            (AccessMode::ReadOnly, cap) if cap.is_read() => true,
            (AccessMode::ReadOnly, _) => false,
            (AccessMode::EphemeralOnly, cap) => cap.is_read() || cap == WorkspaceWriteEphemeral,
            (AccessMode::ReadWrite, _) => true,
        }
    }

    pub fn allows_write(&self) -> bool {
        matches!(self, AccessMode::ReadWrite | AccessMode::EphemeralOnly)
    }

    pub fn allows_git(&self) -> bool {
        matches!(self, AccessMode::ReadWrite)
    }

    pub fn allows_process(&self) -> bool {
        matches!(self, AccessMode::ReadWrite)
    }
}

impl std::fmt::Display for AccessMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AccessMode::Locked => write!(f, "Locked"),
            AccessMode::ReadOnly => write!(f, "ReadOnly"),
            AccessMode::EphemeralOnly => write!(f, "EphemeralOnly"),
            AccessMode::ReadWrite => write!(f, "ReadWrite"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum ToolFilter {
    #[default]
    Unrestricted,
    Allowlist(Vec<String>),
    Blocklist(Vec<String>),
}

impl ToolFilter {
    pub fn allows(&self, tool_name: &str) -> bool {
        match self {
            ToolFilter::Unrestricted => true,
            ToolFilter::Allowlist(allowed) => allowed.iter().any(|t| t == tool_name),
            ToolFilter::Blocklist(blocked) => !blocked.iter().any(|t| t == tool_name),
        }
    }

    pub fn blocked_tools<'a>(&'a self) -> Box<dyn Iterator<Item = &'a str> + 'a> {
        match self {
            ToolFilter::Unrestricted => Box::new(std::iter::empty()),
            ToolFilter::Allowlist(_) => Box::new(std::iter::empty()),
            ToolFilter::Blocklist(blocked) => Box::new(blocked.iter().map(|s| s.as_str())),
        }
    }

    pub fn len(&self) -> usize {
        match self {
            ToolFilter::Unrestricted => 0,
            ToolFilter::Allowlist(v) => v.len(),
            ToolFilter::Blocklist(v) => v.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn is_unrestricted(&self) -> bool {
        matches!(self, ToolFilter::Unrestricted)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AccessDecision {
    Allow,
    Deny {
        reason: String,
        code: AccessDeniedCode,
    },
}

impl AccessDecision {
    pub fn is_allowed(&self) -> bool {
        matches!(self, AccessDecision::Allow)
    }

    pub fn denial_code(&self) -> Option<AccessDeniedCode> {
        match self {
            AccessDecision::Deny { code, .. } => Some(*code),
            _ => None,
        }
    }

    pub fn to_error_string(&self) -> String {
        match self {
            AccessDecision::Allow => "Allowed".to_string(),
            AccessDecision::Deny { reason, code } => format!("[{:?}] {}", code, reason),
        }
    }
}

impl std::fmt::Display for AccessDecision {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AccessDecision::Allow => write!(f, "Allowed"),
            AccessDecision::Deny { reason, code } => write!(f, "Denied({:?}): {}", code, reason),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AccessDeniedCode {
    NotInitialized,
    CapabilityDenied,
    ReadOnlyMode,
    OutsideRootDir,
    ToolNotAllowed,
    RateLimitExceeded,
    AuditFailure,
}

impl std::fmt::Display for AccessDeniedCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AccessDeniedCode::NotInitialized => write!(f, "NotInitialized"),
            AccessDeniedCode::CapabilityDenied => write!(f, "CapabilityDenied"),
            AccessDeniedCode::ReadOnlyMode => write!(f, "ReadOnlyMode"),
            AccessDeniedCode::OutsideRootDir => write!(f, "OutsideRootDir"),
            AccessDeniedCode::ToolNotAllowed => write!(f, "ToolNotAllowed"),
            AccessDeniedCode::RateLimitExceeded => write!(f, "RateLimitExceeded"),
            AccessDeniedCode::AuditFailure => write!(f, "AuditFailure"),
        }
    }
}

// ---------------------------------------------------------------------------
// Pure policy helpers for enforcement (used by boundary module)
// ---------------------------------------------------------------------------

/// Result of a single enforcement check.
#[derive(Debug)]
pub enum EnforcementCheck {
    /// Access allowed.
    Allow,
    /// Access denied.
    Deny {
        code: AccessDeniedCode,
        reason: String,
    },
}

/// Pure policy: check if tool is allowed by filter.
pub fn policy_tool_allowed(tool_name: &str, filter: &ToolFilter) -> Option<AccessDeniedCode> {
    if filter.allows(tool_name) {
        None
    } else {
        Some(AccessDeniedCode::ToolNotAllowed)
    }
}

/// Pure policy: check if mutating operation is allowed by access mode.
pub fn policy_mutating_allowed(
    is_mutating: bool,
    access_mode: &AccessMode,
) -> Option<AccessDeniedCode> {
    if !is_mutating || access_mode.allows_write() {
        None
    } else {
        Some(AccessDeniedCode::ReadOnlyMode)
    }
}

/// Pure policy: check if path is within root (given canonicalized paths).
pub fn policy_path_within_root(
    canonical_path: &std::path::Path,
    canonical_root: &std::path::Path,
) -> bool {
    canonical_path.starts_with(canonical_root)
}

/// Pure policy: compute denial reason for tool filter.
pub fn denial_reason_tool_not_allowed(tool_name: &str) -> String {
    format!("Tool '{}' is not allowed by current filter", tool_name)
}

/// Pure policy: compute denial reason for read-only mode.
pub fn denial_reason_read_only(access_mode: &AccessMode) -> String {
    format!(
        "Access mode '{}' does not allow write operations",
        access_mode
    )
}

/// Pure policy: compute denial reason for path outside root.
pub fn denial_reason_path_outside_root(
    path: &std::path::Path,
    root_dir: &std::path::Path,
) -> String {
    format!(
        "Path '{}' is outside the allowed root directory '{}'",
        path.display(),
        root_dir.display()
    )
}

/// Pure: evaluate all enforcement checks and return first denial if any.
///
/// Returns the first denial found, or `Allow` if all checks pass.
pub fn evaluate_enforcement_pure(
    tool_name: &str,
    tool_filter: &ToolFilter,
    is_mutating: bool,
    access_mode: &AccessMode,
    path: Option<&std::path::Path>,
    root_dir: &std::path::Path,
    is_path_allowed: impl Fn(&std::path::Path) -> bool,
) -> EnforcementCheck {
    // Check tool filter (priority 1)
    if let Some(code) = policy_tool_allowed(tool_name, tool_filter) {
        let reason = denial_reason_tool_not_allowed(tool_name);
        return EnforcementCheck::Deny { code, reason };
    }

    // Check access mode for mutating (priority 2)
    if let Some(code) = policy_mutating_allowed(is_mutating, access_mode) {
        let reason = denial_reason_read_only(access_mode);
        return EnforcementCheck::Deny { code, reason };
    }

    // Check path (priority 3)
    if let Some(p) = path {
        if !is_path_allowed(p) {
            let reason = denial_reason_path_outside_root(p, root_dir);
            return EnforcementCheck::Deny {
                code: AccessDeniedCode::OutsideRootDir,
                reason,
            };
        }
    }

    EnforcementCheck::Allow
}

/// Trait for audit sink implementations.
pub trait AuditSink: Send + Sync {
    fn emit(&self, record: crate::io::access::AuditRecord);
    fn flush(&self) {}
}

#[derive(Debug, Clone, Copy, Default)]
pub struct NoOpAuditSink;

impl AuditSink for NoOpAuditSink {
    fn emit(&self, _record: crate::io::access::AuditRecord) {}
    fn flush(&self) {}
}
