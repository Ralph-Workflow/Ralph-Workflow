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
    // ---------------------------------------------------------------------------
    // Minimum required variants per SPEC (Step 2)
    // ---------------------------------------------------------------------------
    /// Read access to workspace files (minimum variant).
    /// Maps to WorkspaceRead in Ralph capability model.
    /// Required by: read_file, list_directory, glob, etc.
    FileRead,

    /// Write access to workspace files (minimum variant).
    /// Maps to WorkspaceWriteEphemeral or WorkspaceWriteTracked in Ralph.
    /// Required by: write_file, edit_file, etc.
    FileWrite,

    /// Read-only access to git repository (minimum variant).
    /// Maps to GitStatusRead in Ralph capability model.
    /// Required by: git_status, git_log, git_diff
    GitRead,

    /// Execute external commands (minimum variant).
    /// Maps to ProcessExecBounded in Ralph capability model.
    /// Required by: exec_command, bash, etc.
    ProcessExec,

    /// Submit artifacts to the workflow for processing.
    /// Required by: workspace_submit_result
    ArtifactSubmit,

    /// Coordinate workspace operations across parallel agents.
    /// Required by: workspace_coordinate, ralph_coordinate
    WorkspaceCoordination,

    // ---------------------------------------------------------------------------
    // Additional granular variants (Ralph-specific)
    // ---------------------------------------------------------------------------
    /// Read access to workspace files (Ralph-specific variant).
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

    /// Report progress to the running workflow.
    /// Required by: run_report_progress
    RunReportProgress,
}

impl McpCapability {
    /// Returns the string representation of this capability.
    pub fn as_str(&self) -> &'static str {
        match self {
            // Minimum spec variants
            McpCapability::FileRead => "FileRead",
            McpCapability::FileWrite => "FileWrite",
            McpCapability::GitRead => "GitRead",
            McpCapability::ProcessExec => "ProcessExec",
            McpCapability::ArtifactSubmit => "ArtifactSubmit",
            McpCapability::WorkspaceCoordination => "WorkspaceCoordination",
            // Ralph-specific variants
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
            McpCapability::RunReportProgress => "RunReportProgress",
        }
    }

    /// Attempts to parse a capability from a string.
    pub fn try_from_str(s: &str) -> Option<Self> {
        match s {
            // Minimum spec variants
            "FileRead" => Some(McpCapability::FileRead),
            "FileWrite" => Some(McpCapability::FileWrite),
            "GitRead" => Some(McpCapability::GitRead),
            "ProcessExec" => Some(McpCapability::ProcessExec),
            "ArtifactSubmit" => Some(McpCapability::ArtifactSubmit),
            "WorkspaceCoordination" => Some(McpCapability::WorkspaceCoordination),
            // Ralph-specific variants
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
            "RunReportProgress" => Some(McpCapability::RunReportProgress),
            _ => None,
        }
    }

    /// Returns true if this capability represents a write operation.
    pub fn is_write(&self) -> bool {
        matches!(
            self,
            McpCapability::FileWrite
                | McpCapability::WorkspaceWriteEphemeral
                | McpCapability::WorkspaceWriteTracked
                | McpCapability::WorkspaceWriteAny
                | McpCapability::GitWrite
                | McpCapability::EnvWrite
        )
    }

    /// Returns true if this capability represents a read operation.
    pub fn is_read(&self) -> bool {
        matches!(
            self,
            McpCapability::FileRead
                | McpCapability::WorkspaceRead
                | McpCapability::GitRead
                | McpCapability::GitStatusRead
                | McpCapability::EnvRead
        )
    }

    /// Returns true if this capability involves git operations.
    pub fn is_git(&self) -> bool {
        matches!(
            self,
            McpCapability::GitRead | McpCapability::GitStatusRead | McpCapability::GitWrite
        )
    }

    /// Returns true if this capability involves process execution.
    pub fn is_process(&self) -> bool {
        matches!(
            self,
            McpCapability::ProcessExec
                | McpCapability::ProcessExecBounded
                | McpCapability::ProcessExecUnbounded
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
///
/// # Public Variants
///
/// Only `ReadOnly` and `ReadWrite` are part of the public API.
/// Additional internal variants may exist for backwards compatibility
/// but are not constructible outside the crate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
#[non_exhaustive]
pub enum AccessMode {
    /// Only read operations permitted. File writes, git writes, and process exec
    /// are rejected with `ReadOnlyMode`. Reads (WorkspaceRead, GitStatusRead, EnvRead)
    /// are allowed.
    ReadOnly,

    /// All permitted operations allowed. ReadWrite mode still respects `root_dir`
    /// path boundaries and `ToolFilter` restrictions.
    #[default]
    ReadWrite,
}

impl AccessMode {
    /// Returns true if this access mode permits the given capability.
    pub fn allows(&self, capability: McpCapability) -> bool {
        match (self, capability) {
            (AccessMode::ReadOnly, cap) if cap.is_read() => true,
            (AccessMode::ReadOnly, _) => false,
            (AccessMode::ReadWrite, _) => true,
        }
    }

    /// Returns true if this access mode permits write operations.
    pub fn allows_write(&self) -> bool {
        matches!(self, AccessMode::ReadWrite)
    }

    /// Returns true if this access mode permits git operations.
    pub fn allows_git(&self) -> bool {
        matches!(self, AccessMode::ReadWrite)
    }

    /// Returns true if this access mode permits process execution.
    pub fn allows_process(&self) -> bool {
        matches!(self, AccessMode::ReadWrite)
    }
}

impl std::fmt::Display for AccessMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AccessMode::ReadOnly => write!(f, "ReadOnly"),
            AccessMode::ReadWrite => write!(f, "ReadWrite"),
        }
    }
}

/// Tool filter controlling which registered tools can be dispatched.
///
/// Tool filter is checked by `mcp-server` at dispatch time, before capability checks
/// and before calling any tool handler. The host adapter never sees blocked tool calls.
///
/// # Public Variants
///
/// Only `Allowlist` and `Blocklist` are part of the public API.
/// `Unrestricted` is removed from the public API; use `Blocklist(vec![])` to allow all tools.
///
/// An empty `Allowlist` means no tools are accessible.
/// An empty `Blocklist` means all tools are accessible.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ToolFilter {
    /// Only the listed tool names can be dispatched. Any tool not in the list is
    /// rejected with `ToolNotAllowed` before capability checking.
    /// An empty allowlist means no tools are accessible.
    Allowlist(Vec<String>),

    /// All registered tools except those in the list can be dispatched.
    /// Tools in the blocklist are rejected with `ToolNotAllowed`.
    /// An empty blocklist means all tools are accessible.
    Blocklist(Vec<String>),
}

impl Default for ToolFilter {
    fn default() -> Self {
        ToolFilter::Blocklist(vec![])
    }
}

impl ToolFilter {
    /// Returns true if the tool name is permitted by this filter.
    pub fn allows(&self, tool_name: &str) -> bool {
        match self {
            ToolFilter::Allowlist(allowed) => allowed.iter().any(|t| t == tool_name),
            ToolFilter::Blocklist(blocked) => !blocked.iter().any(|t| t == tool_name),
        }
    }

    /// Returns an iterator over the blocked tool names in this filter.
    /// For Allowlist, returns empty iterator (nothing is blocked by the filter itself).
    /// For Blocklist, returns the blocked tool names.
    pub fn blocked_tools<'a>(&'a self) -> Box<dyn Iterator<Item = &'a str> + 'a> {
        match self {
            ToolFilter::Allowlist(_) => Box::new(std::iter::empty()),
            ToolFilter::Blocklist(blocked) => Box::new(blocked.iter().map(|s| s.as_str())),
        }
    }

    /// Returns the number of tools in this filter.
    pub fn len(&self) -> usize {
        match self {
            ToolFilter::Allowlist(v) => v.len(),
            ToolFilter::Blocklist(v) => v.len(),
        }
    }

    /// Returns true if this filter has no entries.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
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
    /// Returns true if this decision is Allow.
    pub fn is_allowed(&self) -> bool {
        matches!(self, AccessDecision::Allow)
    }

    /// Returns the denial code if this decision is a denial.
    pub fn denial_code(&self) -> Option<AccessDeniedCode> {
        match self {
            AccessDecision::Deny { code, .. } => Some(*code),
            _ => None,
        }
    }

    /// Returns a human-readable string representation of this decision.
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
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize)]
pub enum AccessDeniedCode {
    /// Server received a tool call before the `initialize` handshake completed.
    /// Client must call `initialize` first.
    NotInitialized,

    /// Host session denied the capability required for this tool.
    /// This is the only denial code that originates from the host.
    CapabilityDenied,

    /// Server is in `ReadOnly` access mode and cannot perform the requested
    /// mutation. Check `McpServerConfig::access_mode` to determine allowed operations.
    ReadOnlyMode,

    /// The requested path resolves outside the server's authorized `root_dir`.
    /// Check `McpServerConfig::root_dir` to determine the authorized boundary.
    OutsideRootDir,

    /// The tool is blocked by the active `ToolFilter` (Allowlist or Blocklist).
    /// Check which filter mode is active and whether the tool name is in the list.
    ToolNotAllowed,
}

impl std::fmt::Display for AccessDeniedCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AccessDeniedCode::NotInitialized => write!(f, "NotInitialized"),
            AccessDeniedCode::CapabilityDenied => write!(f, "CapabilityDenied"),
            AccessDeniedCode::ReadOnlyMode => write!(f, "ReadOnlyMode"),
            AccessDeniedCode::OutsideRootDir => write!(f, "OutsideRootDir"),
            AccessDeniedCode::ToolNotAllowed => write!(f, "ToolNotAllowed"),
        }
    }
}

// ---------------------------------------------------------------------------
// Pure policy helpers for enforcement (used by boundary module)
// ---------------------------------------------------------------------------

/// Result of a single enforcement check.
#[derive(Debug)]
pub enum EnforcementCheck {
    /// Access allowed — the request passes this check.
    Allow,
    /// Access denied — the request fails this check with the given code and reason.
    Deny {
        /// Categorical denial code.
        code: AccessDeniedCode,
        /// Human-readable explanation of the denial.
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
    match (
        normalize_path_for_policy(canonical_path),
        normalize_path_for_policy(canonical_root),
    ) {
        (Some(normalized_path), Some(normalized_root)) => {
            normalized_path.starts_with(normalized_root)
        }
        _ => false,
    }
}

fn normalize_path_for_policy(path: &std::path::Path) -> Option<std::path::PathBuf> {
    use std::path::Component;

    let mut segments = Vec::new();
    let is_absolute = path.is_absolute();

    for component in path.components() {
        match component {
            Component::CurDir | Component::RootDir | Component::Prefix(_) => {}
            Component::Normal(segment) => segments.push(segment.to_os_string()),
            Component::ParentDir => {
                segments.pop()?;
            }
        }
    }

    let mut normalized = if is_absolute {
        std::path::PathBuf::from(std::path::MAIN_SEPARATOR.to_string())
    } else {
        std::path::PathBuf::new()
    };

    for segment in segments {
        normalized.push(segment);
    }

    Some(normalized)
}

/// Pure policy: evaluate path-under-root check given canonicalized forms.
///
/// This is the decision logic extracted from the boundary function `is_path_under_root`.
/// The io boundary layer is responsible for canonicalization; this function only compares.
///
/// Returns true if:
/// - Both paths canonicalize: path starts with root
/// - Neither exists (MemoryWorkspace case): allow
/// - Root exists but path doesn't: allow if no ".." components escape
/// - Path canonicalizes but root doesn't (MemoryWorkspace): allow if path starts with root string
pub fn policy_path_under_root_check(
    canonical_path: Option<&std::path::PathBuf>,
    canonical_root: Option<&std::path::PathBuf>,
    original_root: Option<&std::path::Path>,
) -> bool {
    match (canonical_path, canonical_root) {
        // Both canonicalize: use simple starts_with check
        (Some(cp), Some(cr)) => policy_path_within_root(cp, cr),
        // Both fail to canonicalize (neither exists on disk): allow.
        // This handles MemoryWorkspace where neither root nor path exist on disk.
        (None, None) => true,
        // Root doesn't exist but path does (MemoryWorkspace case):
        // Check if the path string starts with the root string.
        // This handles MemoryWorkspace where root=/test/repo doesn't exist on disk
        // but the joined path=/test/repo/src/lib.rs is conceptually valid.
        (Some(cp), None) => {
            // If the path has ".." components, deny for safety.
            if cp.components().any(|c| c.as_os_str() == "..") {
                return false;
            }
            // If the canonical path is absolute and doesn't start with the original root,
            // it has escaped. Deny. This catches absolute paths like /etc/passwd
            // when root is a non-existent temp dir.
            if let Some(root) = original_root {
                if cp.is_absolute() && !cp.starts_with(root) {
                    return false;
                }
            }
            // Allow - MemoryWorkspace case where root doesn't exist but path is safe
            true
        }
        // Path doesn't exist on disk but root does: check if path would be under root
        // by verifying the joined path doesn't escape via ".."
        (None, Some(_)) => {
            // This case is handled by the boundary layer calling this function
            // with the parent directory canonicalization. If we reach here with
            // path=None and root=Some, it means the path's parent couldn't be
            // canonicalized either, so we deny to be safe.
            false
        }
    }
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

/// Parameters for [`evaluate_enforcement_pure`].
///
/// Bundles all inputs needed for enforcement evaluation into a single struct
/// to avoid clippy's 7-parameter limit.
pub struct EnforcementParams<'a> {
    /// Tool name being dispatched.
    pub tool_name: &'a str,
    /// Active tool filter (Allowlist or Blocklist).
    pub tool_filter: &'a ToolFilter,
    /// Whether the tool is a mutating operation.
    pub is_mutating: bool,
    /// Server access mode (ReadOnly, ReadWrite, etc.).
    pub access_mode: &'a AccessMode,
    /// Path involved in the operation, if applicable.
    pub path: Option<&'a std::path::Path>,
    /// Authorized root directory for file operations.
    pub root_dir: &'a std::path::Path,
    /// Path canonicalization function.
    pub is_path_allowed: Box<dyn Fn(&std::path::Path) -> bool + 'a>,
    /// Optional function to check capability. If None, no capability check is performed.
    /// If Some, the function is called at step 4 (after earlier checks pass) to
    /// determine the capability check result.
    pub capability_fn: Option<Box<dyn Fn() -> AccessDecision + 'a>>,
}

/// Pure: evaluate all enforcement checks and return first denial if any.
///
/// Returns the first denial found, or `Allow` if all checks pass.
///
/// # Check Ordering
///
/// 1. Tool filter (tool not in allowlist, or in blocklist)
/// 2. Access mode (ReadOnly blocks mutating operations)
/// 3. Path boundary (path resolves outside root_dir)
/// 4. Capability (host session denied the required capability) — only called if earlier checks pass
pub fn evaluate_enforcement_pure(params: &EnforcementParams) -> EnforcementCheck {
    // Check tool filter (priority 1)
    if let Some(code) = policy_tool_allowed(params.tool_name, params.tool_filter) {
        let reason = denial_reason_tool_not_allowed(params.tool_name);
        return EnforcementCheck::Deny { code, reason };
    }

    // Check access mode for mutating (priority 2)
    if let Some(code) = policy_mutating_allowed(params.is_mutating, params.access_mode) {
        let reason = denial_reason_read_only(params.access_mode);
        return EnforcementCheck::Deny { code, reason };
    }

    // Check path (priority 3)
    if let Some(p) = params.path {
        if !(params.is_path_allowed)(p) {
            let reason = denial_reason_path_outside_root(p, params.root_dir);
            return EnforcementCheck::Deny {
                code: AccessDeniedCode::OutsideRootDir,
                reason,
            };
        }
    }

    // Check capability (priority 4) — host session decision
    // The capability function is ONLY called if we reach this point (earlier checks passed).
    // This ensures the host is never consulted when earlier checks deny.
    if let Some(check_cap) = &params.capability_fn {
        let outcome = check_cap();
        if let AccessDecision::Deny { reason, code } = outcome {
            return EnforcementCheck::Deny { code, reason };
        }
    }

    EnforcementCheck::Allow
}

/// Receives audit records from the MCP server dispatch layer.
///
/// Implementations receive [`crate::dispatch::AuditRecord`] from the dispatch layer and are
/// responsible for persisting or transmitting them. The io layer provides
/// the concrete implementation with real timestamps.
pub trait AuditSink: Send + Sync {
    /// Emit an audit record for a tool invocation or access decision.
    fn emit(&self, record: crate::dispatch::audit::AuditRecord);
    /// Flush any buffered records. Implementations may batch records for efficiency.
    fn flush(&self) {}
}

/// A no-op audit sink that discards all records.
///
/// Used when audit logging is disabled or not required.
#[derive(Debug, Clone, Copy, Default)]
pub struct NoOpAuditSink;

impl AuditSink for NoOpAuditSink {
    fn emit(&self, _record: crate::dispatch::audit::AuditRecord) {}
    fn flush(&self) {}
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn policy_path_within_root_rejects_non_normalized_parent_traversal() {
        let canonical_root = PathBuf::from("/safe/root");
        let non_normalized = PathBuf::from("/safe/root/subdir/../../escape/new.txt");

        assert!(
            !policy_path_within_root(&non_normalized, &canonical_root),
            "non-normalized traversal path must not pass root boundary checks"
        );
    }
}
