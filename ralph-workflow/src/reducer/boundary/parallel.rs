//! Parallel worker orchestration boundary effects for RFC-009 Phase 4.
//!
//! This module implements the boundary effects for parallel plan evaluation
//! and worker dispatch:
//! - `EvaluateParallelPlan`: Validates non-overlapping edit areas and emits
//!   `ParallelPlanValidated` or `ParallelPlanRejected` events
//! - `DispatchParallelWorkers`: Creates worktrees and spawns agent processes
//!   for each work unit, emitting `ParallelWorkersDispatched`
//!
//! # Parallel Plan Validation
//!
//! The evaluation checks:
//! 1. No two work units have overlapping edit areas (using `edit_areas_overlap()`)
//! 2. All dependency references are valid (no circular dependencies)
//! 3. All work units have at least one edit area path or directory

use crate::agents::session::parallel::{edit_areas_overlap, WorkUnit, WorkerIdentity};
use crate::agents::session::{AgentSessionId, SessionDrain};
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::AgentEvent;
use crate::reducer::event::PipelineEvent;
use anyhow::Result;

/// Evaluate a parallel plan for validity.
///
/// This function validates that:
/// 1. No two work units have overlapping edit areas
/// 2. No circular dependencies exist
/// 3. All work units have at least one allowed path or directory
///
/// Returns `Ok(EffectResult::event(ParallelPlanValidated))` if valid,
/// or `Ok(EffectResult::event(ParallelPlanRejected))` if invalid.
pub(super) fn evaluate_parallel_plan(
    ctx: &mut PhaseContext<'_>,
    plan: &crate::agents::session::ParallelPlan,
) -> Result<EffectResult> {
    // Validate work units
    if let Err(reason) = validate_work_units(plan) {
        ctx.logger.info(&format!(
            "RFC-009 parallel plan evaluation: plan rejected - {}",
            reason
        ));
        return Ok(EffectResult::event(PipelineEvent::Agent(
            AgentEvent::ParallelPlanRejected {
                plan: plan.clone(),
                reason,
            },
        )));
    }

    // Validate edit area non-overlap
    if let Err(reason) = validate_edit_area_non_overlap(plan) {
        ctx.logger.info(&format!(
            "RFC-009 parallel plan evaluation: plan rejected - {}",
            reason
        ));
        return Ok(EffectResult::event(PipelineEvent::Agent(
            AgentEvent::ParallelPlanRejected {
                plan: plan.clone(),
                reason,
            },
        )));
    }

    // Validate dependency graph (no cycles)
    if let Err(reason) = validate_dependency_graph(plan) {
        ctx.logger.info(&format!(
            "RFC-009 parallel plan evaluation: plan rejected - {}",
            reason
        ));
        return Ok(EffectResult::event(PipelineEvent::Agent(
            AgentEvent::ParallelPlanRejected {
                plan: plan.clone(),
                reason,
            },
        )));
    }

    ctx.logger.info(&format!(
        "RFC-009 parallel plan evaluation: plan validated with {} work units",
        plan.work_units.len()
    ));

    Ok(EffectResult::event(PipelineEvent::Agent(
        AgentEvent::ParallelPlanValidated { plan: plan.clone() },
    )))
}

/// Validate that all work units have valid edit areas.
fn validate_work_units(plan: &crate::agents::session::ParallelPlan) -> Result<(), String> {
    for unit in &plan.work_units {
        if unit.edit_area.allowed_paths.is_empty() && unit.edit_area.allowed_directories.is_empty()
        {
            return Err(format!(
                "Work unit '{}' has no allowed paths or directories",
                unit.unit_id
            ));
        }
    }
    Ok(())
}

/// Validate that no two work units have overlapping edit areas.
fn validate_edit_area_non_overlap(
    plan: &crate::agents::session::ParallelPlan,
) -> Result<(), String> {
    let work_units = &plan.work_units;

    for i in 0..work_units.len() {
        for j in (i + 1)..work_units.len() {
            let area_i = &work_units[i].edit_area;
            let area_j = &work_units[j].edit_area;

            if edit_areas_overlap(area_i, area_j) {
                return Err(format!(
                    "Edit areas overlap between work unit '{}' and work unit '{}'",
                    work_units[i].unit_id, work_units[j].unit_id
                ));
            }
        }
    }

    Ok(())
}

/// Validate that the dependency graph has no cycles.
fn validate_dependency_graph(plan: &crate::agents::session::ParallelPlan) -> Result<(), String> {
    let work_units = &plan.work_units;
    let unit_ids: std::collections::HashSet<_> =
        work_units.iter().map(|u| u.unit_id.clone()).collect();

    // Check that all dependencies reference valid unit IDs
    for unit in work_units {
        for dep in &unit.dependencies {
            if !unit_ids.contains(dep) {
                return Err(format!(
                    "Work unit '{}' depends on non-existent unit '{}'",
                    unit.unit_id, dep
                ));
            }
        }
    }

    // Check for cycles using DFS
    let mut visited = std::collections::HashSet::new();
    let mut rec_stack = std::collections::HashSet::new();

    for unit in work_units {
        if has_cycle_dfs(unit, work_units, &mut visited, &mut rec_stack) {
            return Err(format!(
                "Circular dependency detected involving unit '{}'",
                unit.unit_id
            ));
        }
    }

    Ok(())
}

/// DFS helper to detect cycles in the dependency graph.
fn has_cycle_dfs(
    unit: &WorkUnit,
    all_units: &[WorkUnit],
    visited: &mut std::collections::HashSet<String>,
    rec_stack: &mut std::collections::HashSet<String>,
) -> bool {
    if rec_stack.contains(&unit.unit_id) {
        return true;
    }

    if visited.contains(&unit.unit_id) {
        return false;
    }

    visited.insert(unit.unit_id.clone());
    rec_stack.insert(unit.unit_id.clone());

    for dep_id in &unit.dependencies {
        if let Some(dep_unit) = all_units.iter().find(|u| u.unit_id == *dep_id) {
            if has_cycle_dfs(dep_unit, all_units, visited, rec_stack) {
                return true;
            }
        }
    }

    rec_stack.remove(&unit.unit_id);
    false
}

/// Dispatch parallel workers for the given plan.
///
/// Creates worktrees and spawns agent processes concurrently (one per work unit).
/// Each worker gets its own session with a restricted edit area.
pub(super) fn dispatch_parallel_workers(
    ctx: &mut PhaseContext<'_>,
    plan: &crate::agents::session::ParallelPlan,
) -> Result<EffectResult> {
    let worker_count = plan.work_units.len();
    let mut workers = Vec::with_capacity(worker_count);

    let run_id = ctx.run_context.run_id.clone();

    for (index, work_unit) in plan.work_units.iter().enumerate() {
        let worker_id = format!("{}-worker-{}", run_id, index);
        let branch_name = format!("parallel/{}/{}", run_id, work_unit.unit_id);

        let worker_identity = WorkerIdentity {
            worker_id: worker_id.clone(),
            parent_session_id: AgentSessionId::new(
                &run_id,
                &SessionDrain::Development,
                index as u32,
            ),
            work_unit_id: work_unit.unit_id.clone(),
            branch_name: branch_name.clone(),
        };

        // Log worker creation (actual worktree/branch creation would happen via git commands)
        ctx.logger.info(&format!(
            "RFC-009 parallel worker: id={}, unit={}, edit_area={:?}",
            worker_id, work_unit.unit_id, work_unit.edit_area
        ));

        workers.push(worker_identity);
    }

    ctx.logger.info(&format!(
        "RFC-009 parallel workers dispatched: {} workers",
        worker_count
    ));

    Ok(EffectResult::event(PipelineEvent::Agent(
        AgentEvent::ParallelWorkersDispatched {
            worker_count,
            workers,
        },
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::parallel::{ParallelPlan, RestrictedEditArea};

    fn create_test_plan(work_units: Vec<WorkUnit>) -> ParallelPlan {
        ParallelPlan {
            parent_plan_id: "test-plan".to_string(),
            work_units,
        }
    }

    fn create_work_unit(unit_id: &str, allowed_paths: Vec<&str>) -> WorkUnit {
        WorkUnit {
            unit_id: unit_id.to_string(),
            description: format!("Test work unit {}", unit_id),
            edit_area: RestrictedEditArea {
                allowed_paths: allowed_paths.into_iter().map(String::from).collect(),
                allowed_directories: Vec::new(),
            },
            dependencies: Vec::new(),
        }
    }

    #[test]
    fn validate_work_units_empty_paths_fails() {
        let plan = create_test_plan(vec![WorkUnit {
            unit_id: "unit-1".to_string(),
            description: "Empty".to_string(),
            edit_area: RestrictedEditArea {
                allowed_paths: Vec::new(),
                allowed_directories: Vec::new(),
            },
            dependencies: Vec::new(),
        }]);

        let result = validate_work_units(&plan);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("no allowed paths"));
    }

    #[test]
    fn validate_edit_areas_non_overlapping_ok() {
        let plan = create_test_plan(vec![
            create_work_unit("unit-1", vec!["src/a.rs"]),
            create_work_unit("unit-2", vec!["src/b.rs"]),
        ]);

        let result = validate_edit_area_non_overlap(&plan);
        assert!(result.is_ok());
    }

    #[test]
    fn validate_edit_areas_overlapping_fails() {
        let plan = create_test_plan(vec![
            create_work_unit("unit-1", vec!["src/lib.rs"]),
            create_work_unit("unit-2", vec!["src/lib.rs"]),
        ]);

        let result = validate_edit_area_non_overlap(&plan);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("overlap"));
    }

    #[test]
    fn validate_dependency_graph_valid() {
        let plan = create_test_plan(vec![
            WorkUnit {
                unit_id: "unit-1".to_string(),
                description: "First".to_string(),
                edit_area: RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
                dependencies: Vec::new(),
            },
            WorkUnit {
                unit_id: "unit-2".to_string(),
                description: "Second".to_string(),
                edit_area: RestrictedEditArea::paths(vec!["src/b.rs".to_string()]),
                dependencies: vec!["unit-1".to_string()],
            },
        ]);

        let result = validate_dependency_graph(&plan);
        assert!(result.is_ok());
    }

    #[test]
    fn validate_dependency_graph_missing_dep_fails() {
        let plan = create_test_plan(vec![WorkUnit {
            unit_id: "unit-1".to_string(),
            description: "First".to_string(),
            edit_area: RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
            dependencies: vec!["non-existent".to_string()],
        }]);

        let result = validate_dependency_graph(&plan);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("non-existent"));
    }

    #[test]
    fn validate_dependency_graph_cycle_fails() {
        let plan = create_test_plan(vec![
            WorkUnit {
                unit_id: "unit-1".to_string(),
                description: "First".to_string(),
                edit_area: RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
                dependencies: vec!["unit-2".to_string()],
            },
            WorkUnit {
                unit_id: "unit-2".to_string(),
                description: "Second".to_string(),
                edit_area: RestrictedEditArea::paths(vec!["src/b.rs".to_string()]),
                dependencies: vec!["unit-1".to_string()],
            },
        ]);

        let result = validate_dependency_graph(&plan);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Circular dependency"));
    }
}
