//! I/O boundary module for MCP server access control.
//!
//! This module contains access control types that perform I/O (SystemTime, Mutex, std::fs).
//! As a boundary module, it is exempt from functional purity restrictions.

use crate::dispatch::access::{
    evaluate_enforcement_pure, policy_path_within_root, AccessDecision, AuditSink,
    EnforcementCheck, McpCapability, ToolFilter,
};
use crate::dispatch::audit::AuditRecord as PureAuditRecord;
use std::path::{Path, PathBuf};

// Re-export AuditRecord from dispatch::audit for convenience.
// The io layer is responsible for stamping timestamp_nanos on emit.
pub use crate::dispatch::audit::AuditRecord;

/// Thread-safe in-memory audit sink for testing.
/// Stores records from the dispatch layer and stamps timestamps on emit.
pub struct InMemoryAuditSink {
    records: std::sync::Mutex<Vec<PureAuditRecord>>,
    max_records: usize,
}

impl InMemoryAuditSink {
    pub fn new(max_records: usize) -> Self {
        Self {
            records: std::sync::Mutex::new(Vec::with_capacity(max_records)),
            max_records,
        }
    }

    pub fn records(&self) -> Vec<PureAuditRecord> {
        self.records.lock().unwrap().clone()
    }

    pub fn clear(&self) {
        self.records.lock().unwrap().clear();
    }

    pub fn has_records(&self) -> bool {
        !self.records.lock().unwrap().is_empty()
    }
}

impl Default for InMemoryAuditSink {
    fn default() -> Self {
        Self::new(1000)
    }
}

impl AuditSink for InMemoryAuditSink {
    fn emit(&self, mut record: PureAuditRecord) {
        // Stamp the timestamp with real time (io layer responsibility)
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0);
        record.timestamp_nanos = now;

        let mut records = self.records.lock().unwrap();
        if records.len() >= self.max_records {
            records.remove(0);
        }
        records.push(record);
    }

    fn flush(&self) {}
}

// ---------------------------------------------------------------------------
// Server configuration
// ---------------------------------------------------------------------------

/// Server configuration for MCP server.
///
/// `McpServerConfig` establishes the server's initialization contract. It is set once at
/// construction and cannot be changed during server operation.
///
/// # Fields
///
/// * `root_dir` — The authorized directory boundary. All file operations must resolve
///   within this directory. The server rejects any read or write request whose path
///   resolves outside `root_dir`, regardless of host adapter permissions.
/// * `access_mode` — Operations permitted by the server. `ReadOnly` rejects mutations;
///   `ReadWrite` allows all operations subject to `tool_filter` and capability checks.
/// * `tool_filter` — Tool dispatch filter. `Unrestricted` allows all registered tools;
///   `Allowlist` restricts to only named tools; `Blocklist` excludes named tools.
/// * `session_id` — Optional session identifier used for audit record correlation.
///   If `None`, audit records use "unknown" as the session identifier.
#[derive(Debug, Clone)]
pub struct McpServerConfig {
    /// The authorized directory boundary. All file operations must resolve within this
    /// directory. The server rejects any read or write request whose path resolves outside
    /// `root_dir`, regardless of what the host adapter would allow.
    pub root_dir: PathBuf,

    /// Operations permitted by the server. Enforced at dispatch time before capability
    /// checks. `ReadOnly` rejects all mutations; `ReadWrite` allows all operations
    /// subject to `tool_filter` and capability checks.
    pub access_mode: crate::dispatch::access::AccessMode,

    /// Tool dispatch filter. `Unrestricted` allows all registered tools;
    /// `Allowlist(names)` restricts dispatch to only the named tools;
    /// `Blocklist(names)` excludes only the named tools.
    pub tool_filter: ToolFilter,

    /// Optional session identifier for audit record correlation.
    /// If `None`, audit records use "unknown" as the session identifier.
    pub session_id: Option<String>,
}

impl McpServerConfig {
    pub fn new(root_dir: PathBuf) -> Self {
        Self {
            root_dir,
            access_mode: crate::dispatch::access::AccessMode::ReadWrite,
            tool_filter: ToolFilter::Unrestricted,
            session_id: None,
        }
    }

    pub fn locked(root_dir: PathBuf) -> Self {
        Self {
            root_dir,
            access_mode: crate::dispatch::access::AccessMode::Locked,
            tool_filter: ToolFilter::Unrestricted,
            session_id: None,
        }
    }

    pub fn read_only(root_dir: PathBuf) -> Self {
        Self {
            root_dir,
            access_mode: crate::dispatch::access::AccessMode::ReadOnly,
            tool_filter: ToolFilter::Unrestricted,
            session_id: None,
        }
    }

    pub fn with_access_mode(mut self, mode: crate::dispatch::access::AccessMode) -> Self {
        self.access_mode = mode;
        self
    }

    pub fn with_tool_filter(mut self, filter: ToolFilter) -> Self {
        self.tool_filter = filter;
        self
    }

    pub fn with_session_id(mut self, id: String) -> Self {
        self.session_id = Some(id);
        self
    }

    /// Check if path is allowed - boundary function.
    /// Complexity: gathers I/O (canonicalize) then delegates to pure policy.
    pub fn is_path_allowed(&self, path: &Path) -> bool {
        let joined = if path.is_relative() {
            self.root_dir.join(path)
        } else {
            path.to_path_buf()
        };

        let canonical = canonicalize_for_policy(&joined);
        let root_canonical = canonicalize_root(&self.root_dir);

        match (canonical, root_canonical) {
            (Some(cp), Some(cr)) => policy_path_within_root(&cp, &cr),
            _ => true, // If canonicalization fails, allow
        }
    }
}

/// Boundary helper: canonicalize path for policy check.
fn canonicalize_for_policy(path: &Path) -> Option<PathBuf> {
    if path.exists() {
        std::fs::canonicalize(path).ok()
    } else if path.parent().map(|p| p.exists()).unwrap_or(false) {
        std::fs::canonicalize(path.parent()?).ok()
    } else {
        Some(path.to_path_buf())
    }
}

/// Boundary helper: canonicalize root directory.
fn canonicalize_root(root_dir: &Path) -> Option<PathBuf> {
    std::fs::canonicalize(root_dir).ok()
}

// ---------------------------------------------------------------------------
// Enforcement context
// ---------------------------------------------------------------------------

/// Pre-dispatch enforcement context.
///
/// This type gathers all inputs needed for an enforcement decision and delegates
/// to pure policy evaluation.
///
/// # Check Ordering
///
/// When [`check()`][EnforcementContext::check] is called, enforcement is evaluated
/// in the following strict order. The first denial short-circuits; subsequent checks
/// are not evaluated.
///
/// 1. **Tool filter check** — Is the tool in the allowlist, or blocked by blocklist?
///    If not allowed, returns `ToolNotAllowed`. The host is not consulted.
/// 2. **Access mode check** — Does the access mode permit this operation?
///    If `ReadOnly`/`Locked` and the tool is mutating, returns `ReadOnlyMode`.
///    The host is not consulted.
/// 3. **Path boundary check** — Does the path resolve within `root_dir`?
///    If outside, returns `OutsideRootDir`. The host is not consulted.
/// 4. **Capability check** — Does the session have the required capability?
///    This is the only check that delegates to the host via `HostSession`.
#[derive(Clone)]
pub struct EnforcementContext<'a> {
    pub config: &'a McpServerConfig,
    pub tool_name: &'a str,
    pub required_capability: Option<McpCapability>,
    pub capability_outcome: Option<AccessDecision>,
    pub path: Option<&'a Path>,
    pub is_mutating: bool,
    pub audit_sink: &'a dyn AuditSink,
}

impl<'a> EnforcementContext<'a> {
    pub fn new(
        config: &'a McpServerConfig,
        tool_name: &'a str,
        audit_sink: &'a dyn AuditSink,
    ) -> Self {
        Self {
            config,
            tool_name,
            required_capability: None,
            capability_outcome: None,
            path: None,
            is_mutating: false,
            audit_sink,
        }
    }

    pub fn with_capability(mut self, cap: McpCapability) -> Self {
        self.required_capability = Some(cap);
        self
    }

    pub fn with_path(mut self, path: &'a Path) -> Self {
        self.path = Some(path);
        self
    }

    pub fn with_mutating(mut self, is_mutating: bool) -> Self {
        self.is_mutating = is_mutating;
        self
    }

    pub fn with_capability_outcome(mut self, outcome: AccessDecision) -> Self {
        self.capability_outcome = Some(outcome);
        self
    }

    /// Evaluate enforcement - thin boundary: gather inputs, call pure helper, return result.
    fn evaluate_enforcement(&self) -> (AccessDecision, bool) {
        let params = crate::dispatch::access::EnforcementParams {
            tool_name: self.tool_name,
            tool_filter: &self.config.tool_filter,
            is_mutating: self.is_mutating,
            access_mode: &self.config.access_mode,
            path: self.path,
            root_dir: &self.config.root_dir,
            is_path_allowed: Box::new(|p| self.config.is_path_allowed(p)),
            capability_outcome: &self.capability_outcome,
        };
        let result = evaluate_enforcement_pure(&params);
        match result {
            EnforcementCheck::Allow => (AccessDecision::Allow, false),
            EnforcementCheck::Deny { code, reason } => {
                (AccessDecision::Deny { code, reason }, true)
            }
        }
    }

    /// Check enforcement - boundary function (thin wiring).
    pub fn check(&self) -> AccessDecision {
        let (decision, should_audit) = self.evaluate_enforcement();
        if should_audit {
            self.emit_audit(&decision);
        }
        decision
    }

    fn emit_audit(&self, decision: &AccessDecision) {
        let session_id = self
            .config
            .session_id
            .clone()
            .unwrap_or_else(|| "unknown".to_string());

        let mut record =
            PureAuditRecord::new(session_id, self.tool_name.to_string(), decision.clone());
        record.capability = self.required_capability;
        if let Some(path) = self.path {
            record.path = Some(path.to_path_buf());
        }

        self.audit_sink.emit(record);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dispatch::access::{AccessDeniedCode, NoOpAuditSink};

    #[test]
    fn test_in_memory_audit_sink() {
        let sink = InMemoryAuditSink::new(10);
        assert!(!sink.has_records());

        let record = AuditRecord::new(
            "session1".to_string(),
            "tool_a".to_string(),
            AccessDecision::Allow,
        );
        sink.emit(record);

        assert!(sink.has_records());
        let records = sink.records();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].session_id, "session1");

        sink.clear();
        assert!(!sink.has_records());
    }

    #[test]
    fn test_enforcement_context_tool_filter() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"))
            .with_tool_filter(ToolFilter::Allowlist(vec!["allowed_tool".to_string()]));

        let sink = NoOpAuditSink;
        let ctx = EnforcementContext::new(&config, "blocked_tool", &sink);

        let decision = ctx.check();
        assert!(!decision.is_allowed());
        assert_eq!(
            decision.denial_code(),
            Some(AccessDeniedCode::ToolNotAllowed)
        );
    }

    #[test]
    fn test_enforcement_context_access_mode() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"))
            .with_access_mode(crate::dispatch::access::AccessMode::ReadOnly);

        let sink = NoOpAuditSink;
        let ctx = EnforcementContext::new(&config, "write_tool", &sink).with_mutating(true);

        let decision = ctx.check();
        assert!(!decision.is_allowed());
        assert_eq!(decision.denial_code(), Some(AccessDeniedCode::ReadOnlyMode));
    }
}
