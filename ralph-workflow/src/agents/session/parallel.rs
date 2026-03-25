//! Parallel worker orchestration types for RFC-009 Phase 4.
//!
//! This module provides data types for parallel plan splitting, restricted edit
//! areas, worker identity, and reconciliation metadata.
//!
//! # Parallel Plan Model
//!
//! A `ParallelPlan` represents a task split into independent work units that can
//! be executed concurrently by separate workers. Each `WorkUnit` has:
//! - A restricted edit area defining which files/directories the worker may modify
//! - Dependencies on other work units (for sequencing within the parallel execution)
//!
//! # Edit Area Enforcement
//!
//! `RestrictedEditArea` defines the boundary for a parallel worker's writes.
//! The `check_write_within_edit_area()` function validates whether a given
//! path falls within the allowed area. This integrates with the capability gate
//! to prevent workers from writing outside their assigned scope.

use crate::agents::session::{AgentSessionId, PolicyOutcome};
use serde::{Deserialize, Serialize};
use std::path::Path;

/// A plan split into independent work units for parallel execution.
///
/// Produced by the planning agent when a task is suitable for parallelization.
/// The plan identifies independent work units with non-overlapping edit areas.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ParallelPlan {
    /// The parent plan ID this parallel plan was derived from.
    pub parent_plan_id: String,
    /// The work units to execute in parallel.
    pub work_units: Vec<WorkUnit>,
}

/// A single unit of work assignable to one parallel worker.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct WorkUnit {
    /// Unique identifier for this work unit.
    pub unit_id: String,
    /// Human-readable description of what this work unit does.
    pub description: String,
    /// The restricted area this worker is allowed to modify.
    pub edit_area: RestrictedEditArea,
    /// IDs of work units this depends on (must complete before this starts).
    pub dependencies: Vec<String>,
}

/// Defines the files and directories a parallel worker is allowed to modify.
///
/// Edit areas ensure workers cannot interfere with each other's changes.
/// A write to a path outside the edit area is denied by the capability gate.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RestrictedEditArea {
    /// Exact file paths the worker may write to.
    pub allowed_paths: Vec<String>,
    /// Directory prefixes the worker may write anywhere within.
    pub allowed_directories: Vec<String>,
}

impl RestrictedEditArea {
    /// Create an empty edit area (no writes allowed).
    pub fn empty() -> Self {
        Self {
            allowed_paths: Vec::new(),
            allowed_directories: Vec::new(),
        }
    }

    /// Create an edit area that allows writes to any path (full access).
    pub fn full() -> Self {
        Self {
            allowed_paths: Vec::new(),
            allowed_directories: vec!["/".to_string()],
        }
    }

    /// Create an edit area from a single directory prefix.
    pub fn directory(dir: impl Into<String>) -> Self {
        Self {
            allowed_paths: Vec::new(),
            allowed_directories: vec![dir.into()],
        }
    }

    /// Create an edit area from a list of specific file paths.
    pub fn paths(paths: impl Into<Vec<String>>) -> Self {
        Self {
            allowed_paths: paths.into(),
            allowed_directories: Vec::new(),
        }
    }
}

/// Identity for a parallel worker within a session.
///
/// Each worker has a unique identity tied to a specific work unit
/// and operates within its own git worktree branch.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct WorkerIdentity {
    /// Unique identifier for this worker.
    pub worker_id: String,
    /// The parent session this worker belongs to.
    pub parent_session_id: AgentSessionId,
    /// The work unit this worker is assigned to.
    pub work_unit_id: String,
    /// The git branch name for this worker's worktree.
    pub branch_name: String,
}

/// Reconciliation decision from the verifier agent.
///
/// After workers complete, the verifier reviews their outputs and decides
/// how to proceed: accept, rework, spawn new workers, or collapse to single-agent.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ReconciliationDecision {
    /// The work unit is complete and accepted.
    Accept { unit_id: String },
    /// The work unit needs revision with feedback.
    Rework { unit_id: String, feedback: String },
    /// Spawn new work units based on the output.
    SpawnNew { new_units: Vec<WorkUnit> },
    /// Collapse remaining work to a single agent.
    CollapseToSingle {
        remaining_units: Vec<String>,
        reason: String,
    },
}

/// Metadata tracked for each parallel worker's execution lifecycle.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct WorkerReconciliationMetadata {
    /// The worker's identity.
    pub worker_identity: WorkerIdentity,
    /// When the worker started (Unix timestamp).
    pub start_time: u64,
    /// When the worker finished (None if still running).
    pub end_time: Option<u64>,
    /// Files modified by this worker.
    pub files_modified: Vec<String>,
    /// The reconciliation decision for this worker.
    pub decision: Option<ReconciliationDecision>,
}

impl WorkerReconciliationMetadata {
    /// Create new metadata for a worker at the given start time.
    pub fn new(worker_identity: WorkerIdentity, start_time: u64) -> Self {
        Self {
            worker_identity,
            start_time,
            end_time: None,
            files_modified: Vec::new(),
            decision: None,
        }
    }

    /// Mark the worker as complete at the given time.
    pub fn complete(mut self, end_time: u64) -> Self {
        self.end_time = Some(end_time);
        self
    }

    /// Record files modified by this worker.
    pub fn with_files(mut self, files: Vec<String>) -> Self {
        self.files_modified = files;
        self
    }

    /// Record the reconciliation decision.
    pub fn with_decision(mut self, decision: ReconciliationDecision) -> Self {
        self.decision = Some(decision);
        self
    }
}

/// Check if a write target path falls within a restricted edit area.
///
/// This is a pure function that validates whether a workspace write operation
/// is permitted given the worker's restricted edit area. It handles:
/// - Exact path matches (file paths in `allowed_paths`)
/// - Directory prefix matches (paths under `allowed_directories`)
/// - Edge cases: trailing slashes, relative vs absolute paths
///
/// # Arguments
///
/// * `path` - The target path being written to
/// * `area` - The restricted edit area to validate against
///
/// # Returns
///
/// `PolicyOutcome::Approved` if the write is allowed, or
/// `PolicyOutcome::Denied` with a reason if the path is outside the edit area.
#[must_use]
pub fn check_write_within_edit_area(path: &str, area: &RestrictedEditArea) -> PolicyOutcome {
    let normalized = normalize_path(path);

    // Check exact path matches first (highest priority)
    for allowed in &area.allowed_paths {
        let allowed_normalized = normalize_path(allowed);
        if normalized == allowed_normalized {
            return PolicyOutcome::Approved;
        }
    }

    // Check directory prefix matches
    for dir in &area.allowed_directories {
        let dir_normalized = normalize_path(dir);
        // "/" as a directory matches all paths
        if dir_normalized == "/" || normalized.starts_with(&dir_normalized) {
            return PolicyOutcome::Approved;
        }
    }

    PolicyOutcome::Denied {
        reason: format!(
            "Write target '{}' is outside the restricted edit area. \
             Allowed paths: {:?}, Allowed directories: {:?}",
            path, area.allowed_paths, area.allowed_directories
        ),
    }
}

/// Normalize a path for comparison.
///
/// Handles:
/// - Removes redundant slashes
/// - Normalizes the representation for comparison
fn normalize_path(path: &str) -> String {
    let p = Path::new(path);

    // Handle root path specially - it matches everything
    if path == "/" {
        return "/".to_string();
    }

    // Get the components and rejoin them to remove redundant parts
    let components: Vec<String> = p
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect();
    let normalized = components.join("/");

    // Add trailing slash if this appears to be a directory
    if path.ends_with('/') {
        format!("{}/", normalized)
    } else {
        normalized
    }
}

/// Check if two edit areas overlap (have any common writable paths).
///
/// Used during parallel plan validation to ensure work units don't
/// have conflicting edit areas.
#[must_use]
pub fn edit_areas_overlap(a: &RestrictedEditArea, b: &RestrictedEditArea) -> bool {
    // Check if any of a's allowed paths are within b's allowed directories
    for path in &a.allowed_paths {
        if path_is_within_edit_area(path, b) {
            return true;
        }
    }

    // Check if any of b's allowed paths are within a's allowed directories
    for path in &b.allowed_paths {
        if path_is_within_edit_area(path, a) {
            return true;
        }
    }

    // Check directory prefix overlaps
    for dir_a in &a.allowed_directories {
        for dir_b in &b.allowed_directories {
            if dir_a.starts_with(dir_b) || dir_b.starts_with(dir_a) {
                return true;
            }
        }
    }

    false
}

/// Check if a single path falls within an edit area.
fn path_is_within_edit_area(path: &str, area: &RestrictedEditArea) -> bool {
    let normalized = normalize_path(path);

    for allowed in &area.allowed_paths {
        let allowed_normalized = normalize_path(allowed);
        if normalized == allowed_normalized {
            return true;
        }
    }

    for dir in &area.allowed_directories {
        let dir_normalized = normalize_path(dir);
        if normalized.starts_with(&dir_normalized) {
            return true;
        }
    }

    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::SessionDrain;

    #[test]
    fn restricted_edit_area_empty() {
        let area = RestrictedEditArea::empty();
        let outcome = check_write_within_edit_area("src/lib.rs", &area);
        assert!(matches!(outcome, PolicyOutcome::Denied { .. }));
    }

    #[test]
    fn restricted_edit_area_full() {
        let area = RestrictedEditArea::full();
        let outcome = check_write_within_edit_area("src/lib.rs", &area);
        assert!(matches!(outcome, PolicyOutcome::Approved));
        let outcome2 = check_write_within_edit_area("any/path/here", &area);
        assert!(matches!(outcome2, PolicyOutcome::Approved));
    }

    #[test]
    fn restricted_edit_area_exact_path() {
        let area =
            RestrictedEditArea::paths(vec!["src/lib.rs".to_string(), "src/main.rs".to_string()]);

        let outcome = check_write_within_edit_area("src/lib.rs", &area);
        assert!(matches!(outcome, PolicyOutcome::Approved));

        let outcome2 = check_write_within_edit_area("src/main.rs", &area);
        assert!(matches!(outcome2, PolicyOutcome::Approved));

        let outcome3 = check_write_within_edit_area("src/other.rs", &area);
        assert!(matches!(outcome3, PolicyOutcome::Denied { .. }));
    }

    #[test]
    fn restricted_edit_area_directory_prefix() {
        let area = RestrictedEditArea::directory("src/utils");

        let outcome = check_write_within_edit_area("src/utils/mod.rs", &area);
        assert!(matches!(outcome, PolicyOutcome::Approved));

        let outcome2 = check_write_within_edit_area("src/utils/deep/nested.rs", &area);
        assert!(matches!(outcome2, PolicyOutcome::Approved));

        let outcome3 = check_write_within_edit_area("src/lib.rs", &area);
        assert!(matches!(outcome3, PolicyOutcome::Denied { .. }));
    }

    #[test]
    fn restricted_edit_area_trailing_slash() {
        let area = RestrictedEditArea::directory("src/utils/");

        let outcome = check_write_within_edit_area("src/utils/mod.rs", &area);
        assert!(matches!(outcome, PolicyOutcome::Approved));
    }

    #[test]
    fn restricted_edit_area_relative_path() {
        let area = RestrictedEditArea::directory("src");

        // Relative paths are normalized relative to the workspace root
        let outcome = check_write_within_edit_area("src/lib.rs", &area);
        assert!(matches!(outcome, PolicyOutcome::Approved));
    }

    #[test]
    fn restricted_edit_area_different_files_same_dir() {
        let area = RestrictedEditArea::paths(vec!["src/a.rs".to_string(), "src/b.rs".to_string()]);

        let outcome = check_write_within_edit_area("src/a.rs", &area);
        assert!(matches!(outcome, PolicyOutcome::Approved));

        let outcome2 = check_write_within_edit_area("src/b.rs", &area);
        assert!(matches!(outcome2, PolicyOutcome::Approved));

        let outcome3 = check_write_within_edit_area("src/c.rs", &area);
        assert!(matches!(outcome3, PolicyOutcome::Denied { .. }));
    }

    #[test]
    fn edit_areas_overlap_none() {
        let area_a = RestrictedEditArea::paths(vec!["src/a.rs".to_string()]);
        let area_b = RestrictedEditArea::paths(vec!["src/b.rs".to_string()]);

        assert!(!edit_areas_overlap(&area_a, &area_b));
    }

    #[test]
    fn edit_areas_overlap_same_path() {
        let area_a = RestrictedEditArea::paths(vec!["src/lib.rs".to_string()]);
        let area_b = RestrictedEditArea::paths(vec!["src/lib.rs".to_string()]);

        assert!(edit_areas_overlap(&area_a, &area_b));
    }

    #[test]
    fn edit_areas_overlap_directory_intersection() {
        let area_a = RestrictedEditArea::paths(vec!["src/lib.rs".to_string()]);
        let area_b = RestrictedEditArea::directory("src");

        assert!(edit_areas_overlap(&area_a, &area_b));
    }

    #[test]
    fn worker_reconciliation_metadata_builder() {
        let identity = WorkerIdentity {
            worker_id: "worker-1".to_string(),
            parent_session_id: AgentSessionId::new("run-1", &SessionDrain::Development, 0),
            work_unit_id: "unit-1".to_string(),
            branch_name: "feature/worker-1".to_string(),
        };

        let metadata = WorkerReconciliationMetadata::new(identity, 1700000000)
            .complete(1700000100)
            .with_files(vec!["src/lib.rs".to_string()])
            .with_decision(ReconciliationDecision::Accept {
                unit_id: "unit-1".to_string(),
            });

        assert_eq!(metadata.worker_identity.worker_id, "worker-1");
        assert_eq!(metadata.start_time, 1700000000);
        assert_eq!(metadata.end_time, Some(1700000100));
        assert_eq!(metadata.files_modified, vec!["src/lib.rs"]);
        assert!(matches!(
            metadata.decision,
            Some(ReconciliationDecision::Accept { .. })
        ));
    }

    #[test]
    fn reconciliation_decision_variants() {
        let accept = ReconciliationDecision::Accept {
            unit_id: "unit-1".to_string(),
        };
        assert!(matches!(accept, ReconciliationDecision::Accept { .. }));

        let rework = ReconciliationDecision::Rework {
            unit_id: "unit-2".to_string(),
            feedback: "Fix the bug".to_string(),
        };
        assert!(matches!(rework, ReconciliationDecision::Rework { .. }));

        let spawn = ReconciliationDecision::SpawnNew {
            new_units: Vec::new(),
        };
        assert!(matches!(spawn, ReconciliationDecision::SpawnNew { .. }));

        let collapse = ReconciliationDecision::CollapseToSingle {
            remaining_units: vec!["unit-3".to_string()],
            reason: "Too many workers".to_string(),
        };
        assert!(matches!(
            collapse,
            ReconciliationDecision::CollapseToSingle { .. }
        ));
    }

    #[test]
    fn work_unit_serialization() {
        let unit = WorkUnit {
            unit_id: "unit-1".to_string(),
            description: "Implement feature X".to_string(),
            edit_area: RestrictedEditArea::directory("src/features/x"),
            dependencies: vec![],
        };

        let json = serde_json::to_string(&unit).expect("should serialize");
        let parsed: WorkUnit = serde_json::from_str(&json).expect("should parse");

        assert_eq!(parsed.unit_id, "unit-1");
        assert_eq!(parsed.edit_area.allowed_directories, vec!["src/features/x"]);
    }

    #[test]
    fn parallel_plan_serialization() {
        let plan = ParallelPlan {
            parent_plan_id: "plan-0".to_string(),
            work_units: vec![
                WorkUnit {
                    unit_id: "unit-1".to_string(),
                    description: "First task".to_string(),
                    edit_area: RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
                    dependencies: vec![],
                },
                WorkUnit {
                    unit_id: "unit-2".to_string(),
                    description: "Second task".to_string(),
                    edit_area: RestrictedEditArea::paths(vec!["src/b.rs".to_string()]),
                    dependencies: vec!["unit-1".to_string()],
                },
            ],
        };

        let json = serde_json::to_string(&plan).expect("should serialize");
        let parsed: ParallelPlan = serde_json::from_str(&json).expect("should parse");

        assert_eq!(parsed.work_units.len(), 2);
        assert_eq!(parsed.work_units[1].dependencies, vec!["unit-1"]);
    }
}
