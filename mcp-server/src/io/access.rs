//! I/O boundary module for MCP server access control.
//!
//! This module contains access control types that perform I/O (SystemTime, Mutex, std::fs).
//! As a boundary module, it is exempt from functional purity restrictions.

use crate::dispatch::access::{
    evaluate_enforcement_pure, policy_path_under_root_check, AccessDecision, AuditSink,
    EnforcementCheck, McpCapability, PolicyMode, ToolFilter,
};
use crate::dispatch::audit::AuditRecord as PureAuditRecord;
use crate::dispatch::audit::{AuditCorrelation, AuditEventType, AuditMetadata};
use std::path::{Path, PathBuf};

// Re-export AuditRecord from dispatch::audit for convenience.
// The io layer is responsible for stamping timestamp_nanos on emit.
pub use crate::dispatch::audit::AuditRecord;

// Re-export HostSession for EnforcementContext.
pub use crate::dispatch::host::HostSession;

/// Thread-safe in-memory audit sink for testing.
/// Stores records from the dispatch layer and stamps timestamps on emit.
pub struct InMemoryAuditSink {
    records: std::sync::Mutex<Vec<PureAuditRecord>>,
    max_records: usize,
}

impl InMemoryAuditSink {
    /// Creates a new InMemoryAuditSink with the specified maximum record capacity.
    pub fn new(max_records: usize) -> Self {
        Self {
            records: std::sync::Mutex::new(Vec::with_capacity(max_records)),
            max_records,
        }
    }

    /// Returns a copy of all stored audit records.
    pub fn records(&self) -> Vec<PureAuditRecord> {
        self.records.lock().unwrap().clone()
    }

    /// Clears all stored audit records.
    pub fn clear(&self) {
        self.records.lock().unwrap().clear();
    }

    /// Returns true if this sink has stored any audit records.
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

/// Drain classification used for capability defaults.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DrainClass {
    /// Development drain with write access to project files.
    Dev,
    /// Fixer drain that can patch files during review/fix phases.
    Fixer,
    /// Commit drain for generating commits; mostly read-only with artifact submission.
    Commit,
    /// Planning drain (design/analysis) with read-only access.
    Planning,
    /// Review drain with read-only operations and coordination tools.
    Review,
    /// Analysis drain (tooling/analysis) operating in read-only mode.
    Analysis,
    /// Unknown drain (fallback) that defaults to read-only restrictions.
    Unknown,
}

impl DrainClass {
    /// Returns true if this drain class allows write operations.
    pub fn allows_write(&self) -> bool {
        matches!(self, DrainClass::Dev | DrainClass::Fixer)
    }
}

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
/// * `tool_filter` — Tool dispatch filter. `Blocklist(vec![])` allows all registered tools;
///   `Allowlist(names)` restricts to only named tools; `Blocklist(names)` excludes named tools.
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

    /// Tool dispatch filter. `Blocklist(vec![])` allows all registered tools;
    /// `Allowlist(names)` restricts dispatch to only the named tools;
    /// `Blocklist(names)` excludes only the named tools.
    pub tool_filter: ToolFilter,

    /// Optional session identifier for audit record correlation.
    /// If `None`, audit records use "unknown" as the session identifier.
    pub session_id: Option<String>,

    /// Optional policy mode that this server instance should enforce.
    pub policy_mode: Option<PolicyMode>,

    /// Optional drain identifier (e.g., "development", "commit").
    pub drain: Option<String>,

    /// Drain class derived from the drain identifier.
    pub drain_class: DrainClass,

    /// Optional run identifier for audit and lease correlation.
    pub run_id: Option<String>,

    /// Optional generation counter for lease coordination.
    pub generation: Option<u32>,
}

impl McpServerConfig {
    /// Create a new config with the given root directory and default settings.
    ///
    /// Default access mode is `ReadWrite`, tool filter is `Blocklist(vec![])` (allows all tools).
    pub fn new(root_dir: PathBuf) -> Self {
        Self {
            root_dir,
            access_mode: crate::dispatch::access::AccessMode::ReadWrite,
            tool_filter: ToolFilter::Blocklist(vec![]),
            session_id: None,
            policy_mode: None,
            drain: None,
            drain_class: DrainClass::Unknown,
            run_id: None,
            generation: None,
        }
    }

    /// Create a config that allows only read operations.
    ///
    /// Access mode is `ReadOnly`, tool filter is `Blocklist(vec![])` (allows all tools).
    pub fn read_only(root_dir: PathBuf) -> Self {
        Self {
            root_dir,
            access_mode: crate::dispatch::access::AccessMode::ReadOnly,
            tool_filter: ToolFilter::Blocklist(vec![]),
            session_id: None,
            policy_mode: None,
            drain: None,
            drain_class: DrainClass::Unknown,
            run_id: None,
            generation: None,
        }
    }

    /// Set the access mode.
    pub fn with_access_mode(mut self, mode: crate::dispatch::access::AccessMode) -> Self {
        self.access_mode = mode;
        self
    }

    /// Set the tool filter.
    pub fn with_tool_filter(mut self, filter: ToolFilter) -> Self {
        self.tool_filter = filter;
        self
    }

    /// Set the session identifier for audit record correlation.
    pub fn with_session_id(mut self, id: String) -> Self {
        self.session_id = Some(id);
        self
    }

    /// Set the policy mode that should be enforced for this server instance.
    pub fn with_policy_mode(mut self, mode: PolicyMode) -> Self {
        self.policy_mode = Some(mode);
        self
    }

    /// Set the drain identifier for auditing purposes.
    pub fn with_drain(mut self, drain: String) -> Self {
        self.drain = Some(drain);
        self
    }

    /// Set the drain class used for capability defaults.
    pub fn with_drain_class(mut self, drain_class: DrainClass) -> Self {
        self.drain_class = drain_class;
        self
    }

    /// Set the run identifier for endpoint leases and auditing.
    pub fn with_run_id(mut self, run_id: String) -> Self {
        self.run_id = Some(run_id);
        self
    }

    /// Set the generation counter for endpoint leases.
    pub fn with_generation(mut self, generation: u32) -> Self {
        self.generation = Some(generation);
        self
    }

    /// Check if path is allowed - thin boundary wrapper.
    ///
    /// Gathers I/O (canonicalization) then delegates to pure policy.
    /// Policy decisions are made by `decide_path_allowed`.
    pub fn is_path_allowed(&self, path: &Path) -> bool {
        let boundary_result = boundary_gather_path_info(path, &self.root_dir);
        decide_path_allowed(boundary_result)
    }
}

/// Boundary helper: canonicalize path for policy check.
///
/// Uses parent directory canonicalization as fallback for non-existent paths.
fn canonicalize_for_policy(
    path: &Path,
    root: &Path,
    canonical_root: Option<&Path>,
) -> Option<PathBuf> {
    let path_exists = path.exists();
    let canonical_if_exists = path_exists
        .then(|| std::fs::canonicalize(path).ok())
        .flatten();
    let has_parent_segments = path
        .components()
        .any(|component| matches!(component, std::path::Component::ParentDir));
    let under_root = canonical_root.and_then(|root_path| {
        path.strip_prefix(root)
            .ok()
            .and_then(|relative| normalize_path_under_root(root_path, relative))
    });

    let from_existing_parent = path
        .parent()
        .filter(|parent| parent.exists())
        .and_then(|parent| std::fs::canonicalize(parent).ok())
        .map(|canonical_parent| {
            path.file_name()
                .map_or(canonical_parent.clone(), |name| canonical_parent.join(name))
        });
    let normalized_nonexistent = normalize_nonexistent_path(path);

    crate::dispatch::access::select_canonicalized_path(
        path_exists,
        canonical_if_exists,
        has_parent_segments,
        under_root,
        from_existing_parent,
        normalized_nonexistent,
    )
}

fn normalize_path_under_root(canonical_root: &Path, relative: &Path) -> Option<PathBuf> {
    crate::dispatch::access::normalize_path_for_policy(relative)
        .and_then(|normalized| (!normalized.is_absolute()).then(|| canonical_root.join(normalized)))
}

fn normalize_nonexistent_path(path: &Path) -> Option<PathBuf> {
    crate::dispatch::access::normalize_path_for_policy(path)
}

/// Boundary: gather canonicalized paths for policy evaluation.
fn boundary_gather_path_info(
    path: &Path,
    root: &Path,
) -> (Option<PathBuf>, Option<PathBuf>, Option<PathBuf>) {
    let joined = root.join(path);
    let cr = std::fs::canonicalize(root).ok();
    let cp = canonicalize_for_policy(&joined, root, cr.as_deref());
    (cp, cr, Some(root.to_path_buf()))
}

/// Pure: decide if path is allowed based on gathered boundary info.
fn decide_path_allowed(
    boundary_result: (Option<PathBuf>, Option<PathBuf>, Option<PathBuf>),
) -> bool {
    policy_path_under_root_check(
        boundary_result.0.as_ref(),
        boundary_result.1.as_ref(),
        boundary_result.2.as_deref(),
    )
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
///    If `ReadOnly` and the tool is mutating, returns `ReadOnlyMode`.
///    The host is not consulted.
/// 3. **Path boundary check** — Does the path resolve within `root_dir`?
///    If outside, returns `OutsideRootDir`. The host is not consulted.
/// 4. **Capability check** — Does the session have the required capability?
///    This is the only check that delegates to the host via `HostSession`.
///    The host is only consulted at this step, AFTER earlier checks have passed.
#[derive(Clone)]
pub struct EnforcementContext<'a> {
    /// Server configuration for access control.
    pub config: &'a McpServerConfig,
    /// Tool name being dispatched.
    pub tool_name: &'a str,
    /// Whether the tool exists in the registry.
    pub tool_exists: bool,
    /// Capability required for the tool.
    pub required_capability: Option<McpCapability>,
    /// Session for capability checks (called only at step 4, after earlier checks pass).
    pub session: &'a dyn HostSession,
    /// Path involved in the operation.
    pub path: Option<&'a Path>,
    /// Whether the tool is a mutating operation.
    pub is_mutating: bool,
    /// Audit sink for recording access decisions.
    pub audit_sink: &'a dyn AuditSink,
}

impl<'a> EnforcementContext<'a> {
    /// Create a new enforcement context with the given config and tool name.
    pub fn new(
        config: &'a McpServerConfig,
        tool_name: &'a str,
        audit_sink: &'a dyn AuditSink,
    ) -> Self {
        Self {
            config,
            tool_name,
            tool_exists: true,
            required_capability: None,
            session: &NoOpHostSession,
            path: None,
            is_mutating: false,
            audit_sink,
        }
    }

    /// Set whether this tool exists in the registry.
    pub fn with_tool_exists(mut self, tool_exists: bool) -> Self {
        self.tool_exists = tool_exists;
        self
    }

    /// Set the capability required for this tool.
    pub fn with_capability(mut self, cap: McpCapability) -> Self {
        self.required_capability = Some(cap);
        self
    }

    /// Set the path involved in the operation.
    pub fn with_path(mut self, path: &'a Path) -> Self {
        self.path = Some(path);
        self
    }

    /// Set whether the tool is a mutating operation.
    pub fn with_mutating(mut self, is_mutating: bool) -> Self {
        self.is_mutating = is_mutating;
        self
    }

    /// Set the session for capability checks.
    pub fn with_session(mut self, session: &'a dyn HostSession) -> Self {
        self.session = session;
        self
    }

    /// Evaluate enforcement - thin boundary: gather inputs, call pure helper, return result.
    ///
    /// The capability check is deferred until step 4. If required_capability is Some,
    /// the host session's check_capability is called at step 4, AFTER tool filter,
    /// access mode, and path checks have all passed.
    fn evaluate_enforcement(&self) -> (AccessDecision, bool) {
        // Build a closure that performs the capability check lazily at step 4.
        // This ensures the host is only consulted after earlier checks have passed.
        let capability_fn = self
            .required_capability
            .map::<Box<dyn Fn() -> AccessDecision>, _>(|cap| {
                let session: &dyn HostSession = self.session;
                Box::new(move || session.check_capability(cap))
            });

        let params = crate::dispatch::access::EnforcementParams {
            tool_name: self.tool_name,
            tool_exists: self.tool_exists,
            tool_filter: &self.config.tool_filter,
            is_mutating: self.is_mutating,
            access_mode: &self.config.access_mode,
            path: self.path,
            root_dir: &self.config.root_dir,
            is_path_allowed: Box::new(|p| self.config.is_path_allowed(p)),
            capability_fn,
        };
        let result = evaluate_enforcement_pure(&params);
        match result {
            EnforcementCheck::Allow => (AccessDecision::Allow, true),
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
        let record = build_enforcement_audit_record(
            self.config,
            self.tool_name,
            decision,
            self.required_capability,
            self.path,
        );
        self.audit_sink.emit(record);
    }
}

fn audit_event_type_for(decision: &AccessDecision) -> AuditEventType {
    if decision.is_allowed() {
        AuditEventType::Tool
    } else {
        AuditEventType::Denial
    }
}

fn audit_details_for(decision: &AccessDecision) -> Option<String> {
    if decision.is_allowed() {
        None
    } else {
        Some(
            serde_json::json!({
                "code": decision.denial_code().map(|c| format!("{:?}", c)),
                "reason": decision.to_error_string(),
            })
            .to_string(),
        )
    }
}

fn audit_correlation_for(config: &McpServerConfig) -> AuditCorrelation {
    AuditCorrelation {
        run_id: config.run_id.clone(),
        generation: config.generation,
        drain: config.drain.clone(),
        policy_mode: config.policy_mode,
    }
}

fn build_enforcement_audit_record(
    config: &McpServerConfig,
    tool_name: &str,
    decision: &AccessDecision,
    required_capability: Option<McpCapability>,
    path: Option<&Path>,
) -> PureAuditRecord {
    let session_id = config
        .session_id
        .clone()
        .unwrap_or_else(|| "unknown".to_string());
    let mut record = PureAuditRecord::new(session_id, tool_name.to_string(), decision.clone());
    record.capability = required_capability;
    record.path = path.map(Path::to_path_buf);
    record.metadata = AuditMetadata {
        event_type: audit_event_type_for(decision),
        details: audit_details_for(decision),
        correlation: audit_correlation_for(config),
    };
    record
}

/// A no-op host session used as default in EnforcementContext::new.
///
/// This is only used when no session is needed (e.g., tool filter checks).
/// In practice, all real uses of EnforcementContext set a proper session via
/// `with_session()`.
struct NoOpHostSession;

impl HostSession for NoOpHostSession {
    fn session_id(&self) -> &str {
        "noop"
    }
    fn run_id(&self) -> &str {
        "noop"
    }
    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        AccessDecision::Allow
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dispatch::access::{AccessDeniedCode, NoOpAuditSink, PolicyMode};
    use tempfile::TempDir;

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

    #[test]
    fn test_enforcement_context_check_emits_audit_for_allow() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"));
        let sink = InMemoryAuditSink::new(10);
        let ctx = EnforcementContext::new(&config, "allowed_tool", &sink);

        let decision = ctx.check();
        assert_eq!(decision, AccessDecision::Allow);

        let records = sink.records();
        assert_eq!(records.len(), 1);
        assert!(matches!(records[0].decision, AccessDecision::Allow));
    }

    #[test]
    fn test_enforcement_context_check_emits_audit_for_deny_with_metadata_intact() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"))
            .with_tool_filter(ToolFilter::Allowlist(vec!["allowed_tool".to_string()]));
        let sink = InMemoryAuditSink::new(10);
        let ctx = EnforcementContext::new(&config, "blocked_tool", &sink);

        let decision = ctx.check();
        assert!(!decision.is_allowed());

        let records = sink.records();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].decision, decision);
        match &records[0].decision {
            AccessDecision::Deny { code, reason } => {
                assert_eq!(*code, AccessDeniedCode::ToolNotAllowed);
                assert!(!reason.is_empty());
            }
            AccessDecision::Allow => panic!("expected deny audit decision"),
        }
    }

    #[test]
    fn test_is_path_allowed_denies_non_canonicalizable_parent_traversal() {
        let root = TempDir::new().expect("temp root");
        let config = McpServerConfig::new(root.path().to_path_buf());

        let bypass = Path::new("subdir/../../escape/new.txt");
        assert!(
            !config.is_path_allowed(bypass),
            "parent traversal for non-existent path must be denied"
        );
    }

    #[test]
    fn test_is_path_allowed_allows_non_existent_path_under_root_without_traversal() {
        let root = TempDir::new().expect("temp root");
        let config = McpServerConfig::new(root.path().to_path_buf());

        let safe = Path::new("nested/new-file.txt");
        assert!(
            config.is_path_allowed(safe),
            "non-existent path under root should remain allowed"
        );
    }

    #[test]
    fn drain_class_write_permissions_match_expectations() {
        assert!(DrainClass::Dev.allows_write());
        assert!(DrainClass::Fixer.allows_write());
        assert!(!DrainClass::Commit.allows_write());
        assert!(!DrainClass::Planning.allows_write());
        assert!(!DrainClass::Unknown.allows_write());
    }

    #[test]
    fn config_builder_sets_optional_metadata() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"))
            .with_policy_mode(PolicyMode::Dev)
            .with_drain("development".to_string())
            .with_drain_class(DrainClass::Dev)
            .with_run_id("run-123".to_string())
            .with_generation(7);

        assert_eq!(config.policy_mode, Some(PolicyMode::Dev));
        assert_eq!(config.drain.as_deref(), Some("development"));
        assert_eq!(config.drain_class, DrainClass::Dev);
        assert_eq!(config.run_id.as_deref(), Some("run-123"));
        assert_eq!(config.generation, Some(7));
    }

    #[test]
    fn test_denial_audit_record_has_event_type_denial() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"))
            .with_tool_filter(ToolFilter::Allowlist(vec!["allowed_tool".to_string()]));
        let sink = InMemoryAuditSink::new(10);
        let ctx = EnforcementContext::new(&config, "blocked_tool", &sink);

        let decision = ctx.check();
        assert!(!decision.is_allowed());

        let records = sink.records();
        assert_eq!(records.len(), 1);
        let record = &records[0];
        assert_eq!(
            record.metadata.event_type,
            crate::dispatch::audit::AuditEventType::Denial,
            "denial audit must have event_type = Denial"
        );
        assert!(
            record.metadata.details.is_some(),
            "denial audit must have details"
        );
    }

    #[test]
    fn test_denial_audit_record_has_correlation_keys() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"))
            .with_tool_filter(ToolFilter::Allowlist(vec!["allowed_tool".to_string()]))
            .with_policy_mode(PolicyMode::Commit)
            .with_drain("commit".to_string())
            .with_run_id("run-456".to_string())
            .with_generation(3);
        let sink = InMemoryAuditSink::new(10);
        let ctx = EnforcementContext::new(&config, "blocked_tool", &sink);

        ctx.check();

        let records = sink.records();
        assert_eq!(records.len(), 1);
        let record = &records[0];
        let corr = &record.metadata.correlation;
        assert_eq!(
            corr.run_id.as_deref(),
            Some("run-456"),
            "run_id must be present"
        );
        assert_eq!(corr.generation, Some(3), "generation must be present");
        assert_eq!(
            corr.drain.as_deref(),
            Some("commit"),
            "drain must be present"
        );
        assert_eq!(
            corr.policy_mode,
            Some(PolicyMode::Commit),
            "policy_mode must be present"
        );
    }

    #[test]
    fn test_allow_audit_record_has_event_type_tool() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"));
        let sink = InMemoryAuditSink::new(10);
        let ctx = EnforcementContext::new(&config, "some_tool", &sink);

        ctx.check();

        let records = sink.records();
        assert_eq!(records.len(), 1);
        let record = &records[0];
        assert_eq!(
            record.metadata.event_type,
            crate::dispatch::audit::AuditEventType::Tool,
            "allow audit must have event_type = Tool"
        );
    }

    #[test]
    fn test_allow_audit_record_has_correlation_keys_when_configured() {
        let config = McpServerConfig::new(PathBuf::from("/workspace"))
            .with_policy_mode(PolicyMode::Dev)
            .with_drain("development".to_string())
            .with_run_id("run-789".to_string())
            .with_generation(12);
        let sink = InMemoryAuditSink::new(10);
        let ctx = EnforcementContext::new(&config, "some_tool", &sink);

        ctx.check();

        let records = sink.records();
        assert_eq!(records.len(), 1);
        let record = &records[0];
        let corr = &record.metadata.correlation;
        assert_eq!(
            corr.run_id.as_deref(),
            Some("run-789"),
            "run_id must be present"
        );
        assert_eq!(corr.generation, Some(12), "generation must be present");
        assert_eq!(
            corr.drain.as_deref(),
            Some("development"),
            "drain must be present"
        );
        assert_eq!(
            corr.policy_mode,
            Some(PolicyMode::Dev),
            "policy_mode must be present"
        );
    }
}
