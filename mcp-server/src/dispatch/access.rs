//! Pure access control types for MCP server dispatch layer.
//!
//! This module contains the typed access control model types that have no I/O
//! dependencies. These types can be safely used in non-boundary modules.

/// Typed MCP capabilities - replaces string-based capability checking.
///
/// Each variant gates a specific class of operations. The capability check is the
/// only access decision delegated to the host via `HostSession::check_capability`.
/// All other decisions (ReadOnlyMode, OutsideRootDir, ToolNotAllowed) are made
/// internally by `mcp-server`.
///
/// ## Standard Tool Mapping
///
/// | Capability | Standard Tools That Require It |
/// |-------------|----------------------------------|
/// | `WorkspaceRead` | `ralph_workspace_read_file`, `ralph_workspace_list_directory` |
/// | `WorkspaceWriteEphemeral` | `ralph_workspace_write_file` (ephemeral mode) |
/// | `WorkspaceWriteTracked` | `ralph_workspace_write_file` (tracked mode), `ralph_workspace_edit_file` |
/// | `GitStatusRead` | `ralph_git_status`, `ralph_git_log`, `ralph_git_diff` |
/// | `GitWrite` | `ralph_git_commit`, `ralph_git_checkout` |
/// | `ProcessExecBounded` | `ralph_exec_command` (with timeout/resource limits) |
/// | `ProcessExecUnbounded` | `ralph_exec_command` (no limits) |
/// | `ArtifactSubmit` | `ralph_workspace_submit_result` |
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[non_exhaustive]
pub enum McpCapability {
    /// Read access to workspace files.
    /// Required by: read_file, list_directory, glob, etc.
    WorkspaceRead,

    /// Write access to workspace files that are not tracked by version control.
    /// Ephemeral writes do not create git commits or modify tracked history.
    /// Required by: write_file (when saving untracked files)
    WorkspaceWriteEphemeral,

    /// Write access to workspace files that are tracked by version control.
    /// Tracked writes may create commits or modify git history.
    /// Required by: write_file (when saving existing tracked files), edit_file
    WorkspaceWriteTracked,

    /// Any workspace write capability (either tracked or ephemeral).
    /// Used when the tool handler determines the actual capability needed at runtime
    /// based on whether the target file is tracked.
    WorkspaceWriteAny,

    /// Read-only access to git repository state (status, log, diff).
    /// Required by: git_status, git_log, git_diff
    GitStatusRead,

    /// Write access to git repository (commits, branches, etc.).
    /// Required by: git_commit, git_checkout, git_branch
    GitWrite,

    /// Read access to environment variables.
    /// Required by: env_get, env_list
    EnvRead,

    /// Write access to environment variables.
    /// Required by: env_set, env_delete
    EnvWrite,

    /// Execute external commands with bounded resource limits (timeout, memory).
    /// Required by: exec_command (when limits are enforced)
    ProcessExecBounded,

    /// Execute external commands without resource limits.
    /// Required by: exec_command (when running without limits - dangerous)
    ProcessExecUnbounded,

    /// Submit artifacts to the workflow for processing.
    /// Required by: workspace_submit_result
    ArtifactSubmit,

    /// Report progress to the running workflow.
    /// Required by: run_report_progress
    RunReportProgress,
}

impl McpCapability {
    pub fn as_str(&self) -> &'static str {
        match self {
            McpCapability::WorkspaceRead => "WorkspaceRead",
            McpCapability::WorkspaceWriteEphemeral => "WorkspaceWriteEphemeral",
            McpCapability::WorkspaceWriteTracked => "WorkspaceWriteTracked",
            McpCapability::WorkspaceWriteAny => "WorkspaceWriteAny",
            McpCapability::GitStatusRead => "GitStatusRead",
            McpCapability::GitWrite => "GitWrite",
            McpCapability::EnvRead => "EnvRead",
            McpCapability::EnvWrite => "EnvWrite",
            McpCapability::ProcessExecBounded => "ProcessExecBounded",
            McpCapability::ProcessExecUnbounded => "ProcessExecUnbounded",
            McpCapability::ArtifactSubmit => "ArtifactSubmit",
            McpCapability::RunReportProgress => "RunReportProgress",
        }
    }

    pub fn try_from_str(s: &str) -> Option<Self> {
        match s {
            "WorkspaceRead" => Some(McpCapability::WorkspaceRead),
            "WorkspaceWriteEphemeral" => Some(McpCapability::WorkspaceWriteEphemeral),
            "WorkspaceWriteTracked" => Some(McpCapability::WorkspaceWriteTracked),
            "WorkspaceWriteAny" => Some(McpCapability::WorkspaceWriteAny),
            "GitStatusRead" => Some(McpCapability::GitStatusRead),
            "GitWrite" => Some(McpCapability::GitWrite),
            "EnvRead" => Some(McpCapability::EnvRead),
            "EnvWrite" => Some(McpCapability::EnvWrite),
            "ProcessExecBounded" => Some(McpCapability::ProcessExecBounded),
            "ProcessExecUnbounded" => Some(McpCapability::ProcessExecUnbounded),
            "ArtifactSubmit" => Some(McpCapability::ArtifactSubmit),
            "RunReportProgress" => Some(McpCapability::RunReportProgress),
            _ => None,
        }
    }

    pub fn is_write(&self) -> bool {
        matches!(
            self,
            McpCapability::WorkspaceWriteEphemeral
                | McpCapability::WorkspaceWriteTracked
                | McpCapability::WorkspaceWriteAny
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

/// Server access mode controlling which operations are permitted.
///
/// Access mode is enforced by `mcp-server` at dispatch time, before calling
/// any tool handler. The host adapter is never called for rejected operations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AccessMode {
    /// No operations permitted. Server rejects all tool calls with `ReadOnlyMode`.
    #[default]
    Locked,

    /// Only read operations permitted. File writes, git writes, and process exec
    /// are rejected with `ReadOnlyMode`. Reads (WorkspaceRead, GitStatusRead, EnvRead)
    /// are allowed.
    ReadOnly,

    /// Only read and ephemeral writes permitted. Tracked file modifications and git
    /// writes are rejected. Useful for sandboxed environments where temporary files
    /// can be created but version control must not be modified.
    EphemeralOnly,

    /// All permitted operations allowed. ReadWrite mode still respects `root_dir`
    /// path boundaries and `ToolFilter` restrictions.
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

/// Tool filter controlling which registered tools can be dispatched.
///
/// Tool filter is checked by `mcp-server` at dispatch time, before capability checks
/// and before calling any tool handler. The host adapter never sees blocked tool calls.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum ToolFilter {
    /// All registered tools are accessible (subject to access_mode and capability checks).
    #[default]
    Unrestricted,

    /// Only the listed tool names can be dispatched. Any tool not in the list is
    /// rejected with `ToolNotAllowed` before capability checking.
    /// An empty allowlist means no tools are accessible.
    Allowlist(Vec<String>),

    /// All registered tools except those in the list can be dispatched.
    /// Tools in the blocklist are rejected with `ToolNotAllowed`.
    /// An empty blocklist means all tools are accessible.
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

/// Result of an access control decision.
///
/// Returned by `HostSession::check_capability` and enforcement checks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AccessDecision {
    /// Access is permitted.
    Allow,

    /// Access is denied. The `reason` describes why, and `code` indicates the category.
    Deny {
        /// Human-readable explanation of the denial.
        reason: String,
        /// Categorical denial code for programmatic handling.
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

/// Denial codes returned by access control decisions.
///
/// Each code indicates which layer of the access control system generated the denial:
/// - `NotInitialized`, `ReadOnlyMode`, `OutsideRootDir`, `ToolNotAllowed` are generated
///   internally by `mcp-server` and are NOT delegatable to the host.
/// - `CapabilityDenied` is generated by the host via `HostSession::check_capability` and
///   is the only denial code that originates from the host.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AccessDeniedCode {
    /// Server received a tool call before the `initialize` handshake completed.
    /// Client must call `initialize` first.
    NotInitialized,

    /// Host session denied the capability required for this tool.
    /// This is the only denial code that originates from the host.
    CapabilityDenied,

    /// Server is in `ReadOnly` or `Locked` access mode and cannot perform the requested
    /// mutation. Check `McpServerConfig::access_mode` to determine allowed operations.
    ReadOnlyMode,

    /// The requested path resolves outside the server's authorized `root_dir`.
    /// Check `McpServerConfig::root_dir` to determine the authorized boundary.
    OutsideRootDir,

    /// The tool is blocked by the active `ToolFilter` (Allowlist or Blocklist).
    /// Check which filter mode is active and whether the tool name is in the list.
    ToolNotAllowed,

    /// Request was rate-limited. The server has exceeded allowed request frequency.
    RateLimitExceeded,

    /// Audit log recording failed. The server could not record the access decision.
    /// This may indicate a storage or connectivity issue with the audit sink.
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
///
/// Implementations receive [`crate::dispatch::AuditRecord`] from the dispatch layer and are
/// responsible for persisting or transmitting them. The io layer provides
/// the concrete implementation with real timestamps.
pub trait AuditSink: Send + Sync {
    fn emit(&self, record: crate::dispatch::audit::AuditRecord);
    fn flush(&self) {}
}

#[derive(Debug, Clone, Copy, Default)]
pub struct NoOpAuditSink;

impl AuditSink for NoOpAuditSink {
    fn emit(&self, _record: crate::dispatch::audit::AuditRecord) {}
    fn flush(&self) {}
}
