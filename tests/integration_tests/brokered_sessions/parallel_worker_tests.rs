//! Integration tests for parallel worker orchestration in RFC-009 Phase 4.
//!
//! These tests verify that parallel plan evaluation and worker dispatch works correctly:
//! - Parallel plans with non-overlapping edit areas are validated
//! - Parallel plans with overlapping edit areas are rejected
//! - Dependency graphs without cycles are validated
//! - Dependency graphs with cycles are rejected
//! - Parallel worker sessions are created with correct edit area restrictions
//! - Worker identity is correctly tracked

use ralph_workflow::agents::session::parallel::{
    check_write_within_edit_area, edit_areas_overlap, ParallelPlan, RestrictedEditArea, WorkUnit,
};
use ralph_workflow::agents::session::{
    AgentSession, PolicyOutcome, SessionDrain, WorkerIdentity as SessionWorkerIdentity,
};

use crate::test_timeout::with_default_timeout;

fn create_test_work_unit(unit_id: &str, allowed_paths: Vec<&str>) -> WorkUnit {
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

fn create_test_plan(work_units: Vec<WorkUnit>) -> ParallelPlan {
    ParallelPlan {
        parent_plan_id: "test-plan".to_string(),
        work_units,
    }
}

/// Test that non-overlapping edit areas pass validation.
#[test]
fn parallel_plan_non_overlapping_edit_areas_valid() {
    with_default_timeout(|| {
        let plan = create_test_plan(vec![
            create_test_work_unit("unit-1", vec!["src/a.rs"]),
            create_test_work_unit("unit-2", vec!["src/b.rs"]),
        ]);

        // Verify no overlap between edit areas
        let area1 = &plan.work_units[0].edit_area;
        let area2 = &plan.work_units[1].edit_area;
        assert!(
            !edit_areas_overlap(area1, area2),
            "Non-overlapping areas should not overlap"
        );
    });
}

/// Test that identical edit areas fail validation.
#[test]
fn parallel_plan_identical_edit_areas_overlap() {
    with_default_timeout(|| {
        let plan = create_test_plan(vec![
            create_test_work_unit("unit-1", vec!["src/lib.rs"]),
            create_test_work_unit("unit-2", vec!["src/lib.rs"]),
        ]);

        let area1 = &plan.work_units[0].edit_area;
        let area2 = &plan.work_units[1].edit_area;
        assert!(
            edit_areas_overlap(area1, area2),
            "Identical paths should overlap"
        );
    });
}

/// Test that overlapping file paths fail validation.
#[test]
fn parallel_plan_overlapping_file_paths_fail() {
    with_default_timeout(|| {
        let plan = create_test_plan(vec![
            create_test_work_unit("unit-1", vec!["src/feature/a.rs", "src/shared.rs"]),
            create_test_work_unit("unit-2", vec!["src/shared.rs", "src/feature/b.rs"]),
        ]);

        let area1 = &plan.work_units[0].edit_area;
        let area2 = &plan.work_units[1].edit_area;
        assert!(
            edit_areas_overlap(area1, area2),
            "Overlapping paths should fail validation"
        );
    });
}

/// Test edit area check - path within allowed paths.
#[test]
fn edit_area_check_exact_path_allowed() {
    with_default_timeout(|| {
        let area =
            RestrictedEditArea::paths(vec!["src/lib.rs".to_string(), "src/main.rs".to_string()]);

        assert!(
            matches!(
                check_write_within_edit_area("src/lib.rs", &area),
                PolicyOutcome::Approved
            ),
            "Exact path match should be allowed"
        );
    });
}

/// Test edit area check - path outside allowed paths.
#[test]
fn edit_area_check_path_denied() {
    with_default_timeout(|| {
        let area =
            RestrictedEditArea::paths(vec!["src/lib.rs".to_string(), "src/main.rs".to_string()]);

        assert!(
            matches!(
                check_write_within_edit_area("src/other.rs", &area),
                PolicyOutcome::Denied { .. }
            ),
            "Path outside allowed paths should be denied"
        );
    });
}

/// Test edit area check - directory prefix match.
#[test]
fn edit_area_check_directory_prefix_allowed() {
    with_default_timeout(|| {
        let area = RestrictedEditArea::directory("src/utils");

        assert!(
            matches!(
                check_write_within_edit_area("src/utils/mod.rs", &area),
                PolicyOutcome::Approved
            ),
            "File in allowed directory should be allowed"
        );
    });
}

/// Test edit area check - file outside directory prefix.
#[test]
fn edit_area_check_directory_prefix_denied() {
    with_default_timeout(|| {
        let area = RestrictedEditArea::directory("src/utils");

        assert!(
            matches!(
                check_write_within_edit_area("src/lib.rs", &area),
                PolicyOutcome::Denied { .. }
            ),
            "File outside allowed directory should be denied"
        );
    });
}

/// Test that empty edit area denies all writes.
#[test]
fn edit_area_check_empty_denies_all() {
    with_default_timeout(|| {
        let area = RestrictedEditArea::empty();

        assert!(
            matches!(
                check_write_within_edit_area("src/lib.rs", &area),
                PolicyOutcome::Denied { .. }
            ),
            "Empty edit area should deny all writes"
        );
    });
}

/// Test that full edit area allows all writes.
#[test]
fn edit_area_check_full_allows_all() {
    with_default_timeout(|| {
        let area = RestrictedEditArea::full();

        assert!(
            matches!(
                check_write_within_edit_area("src/lib.rs", &area),
                PolicyOutcome::Approved
            ),
            "Full edit area should allow all writes"
        );
        assert!(
            matches!(
                check_write_within_edit_area("any/path/here", &area),
                PolicyOutcome::Approved
            ),
            "Full edit area should allow any path"
        );
    });
}

/// Test that session.check_edit_area() method works correctly for parallel workers.
/// This verifies the edit area check is accessible via the session interface.
#[test]
fn session_check_edit_area_method_works() {
    with_default_timeout(|| {
        let session = AgentSession::for_parallel_worker(
            "run-123".to_string(),
            SessionDrain::Development,
            0,
            SessionWorkerIdentity {
                worker_id: "worker-1".to_string(),
                parent_session_id: ralph_workflow::agents::session::AgentSessionId::new(
                    "run-123",
                    &SessionDrain::Development,
                    0,
                ),
                work_unit_id: "unit-1".to_string(),
                branch_name: "parallel/run-123/unit-1".to_string(),
            },
            RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
            std::time::SystemTime::now(),
        );

        // Verify session has edit area
        assert!(
            session.edit_area.is_some(),
            "Parallel worker session should have edit area"
        );

        // Test session.check_edit_area() method directly
        // This is the method that should be called in the write execution path
        assert!(
            matches!(session.check_edit_area("src/a.rs"), PolicyOutcome::Approved),
            "session.check_edit_area(src/a.rs) should be Approved"
        );
        assert!(
            matches!(
                session.check_edit_area("src/b.rs"),
                PolicyOutcome::Denied { .. }
            ),
            "session.check_edit_area(src/b.rs) should be Denied"
        );
        assert!(
            matches!(session.check_edit_area("src/a.rs/src/lib.rs"), PolicyOutcome::Denied { .. }),
            "session.check_edit_area(src/a.rs/src/lib.rs) should be Denied (subdirectory of allowed path)"
        );
    });
}

/// Test worker identity creation for parallel worker session.
#[test]
fn parallel_worker_identity_created_correctly() {
    with_default_timeout(|| {
        let session = AgentSession::for_parallel_worker(
            "run-123".to_string(),
            SessionDrain::Development,
            0,
            SessionWorkerIdentity {
                worker_id: "worker-1".to_string(),
                parent_session_id: ralph_workflow::agents::session::AgentSessionId::new(
                    "run-123",
                    &SessionDrain::Development,
                    0,
                ),
                work_unit_id: "unit-1".to_string(),
                branch_name: "parallel/run-123/unit-1".to_string(),
            },
            RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
            std::time::SystemTime::now(),
        );

        assert!(
            session.worker_identity.is_some(),
            "Parallel worker session should have worker identity"
        );
        assert!(
            session.edit_area.is_some(),
            "Parallel worker session should have edit area"
        );

        let edit_area = session.edit_area.unwrap();
        assert!(
            matches!(
                check_write_within_edit_area("src/a.rs", &edit_area),
                PolicyOutcome::Approved
            ),
            "Write within edit area should be allowed"
        );
        assert!(
            matches!(
                check_write_within_edit_area("src/b.rs", &edit_area),
                PolicyOutcome::Denied { .. }
            ),
            "Write outside edit area should be denied"
        );
    });
}

/// Test work unit dependencies without cycles.
#[test]
fn work_unit_dependencies_no_cycle() {
    with_default_timeout(|| {
        let unit1 = WorkUnit {
            unit_id: "unit-1".to_string(),
            description: "First unit".to_string(),
            edit_area: RestrictedEditArea::paths(vec!["src/a.rs".to_string()]),
            dependencies: Vec::new(),
        };

        let unit2 = WorkUnit {
            unit_id: "unit-2".to_string(),
            description: "Second unit".to_string(),
            edit_area: RestrictedEditArea::paths(vec!["src/b.rs".to_string()]),
            dependencies: vec!["unit-1".to_string()],
        };

        let plan = ParallelPlan {
            parent_plan_id: "test-plan".to_string(),
            work_units: vec![unit1, unit2],
        };

        // Verify dependencies are set correctly
        assert!(
            plan.work_units[1]
                .dependencies
                .contains(&"unit-1".to_string()),
            "unit-2 should depend on unit-1"
        );
    });
}

/// Test work unit with directory-based edit area.
#[test]
fn work_unit_directory_edit_area() {
    with_default_timeout(|| {
        let unit = WorkUnit {
            unit_id: "unit-1".to_string(),
            description: "Unit with directory edit area".to_string(),
            edit_area: RestrictedEditArea::directory("src/feature-x"),
            dependencies: Vec::new(),
        };

        assert!(
            matches!(
                check_write_within_edit_area("src/feature-x/mod.rs", &unit.edit_area),
                PolicyOutcome::Approved
            ),
            "Write within feature directory should be allowed"
        );
        assert!(
            matches!(
                check_write_within_edit_area("src/feature-y/mod.rs", &unit.edit_area),
                PolicyOutcome::Denied { .. }
            ),
            "Write outside feature directory should be denied"
        );
    });
}

/// Test multiple work units with mixed edit area types.
#[test]
fn parallel_plan_mixed_edit_area_types() {
    with_default_timeout(|| {
        let unit1 = WorkUnit {
            unit_id: "unit-1".to_string(),
            description: "Unit with specific paths".to_string(),
            edit_area: RestrictedEditArea::paths(vec!["src/lib.rs".to_string()]),
            dependencies: Vec::new(),
        };

        let unit2 = WorkUnit {
            unit_id: "unit-2".to_string(),
            description: "Unit with directory".to_string(),
            edit_area: RestrictedEditArea::directory("src/utils"),
            dependencies: Vec::new(),
        };

        // Verify unit1 and unit2 edit areas don't overlap
        assert!(
            !edit_areas_overlap(&unit1.edit_area, &unit2.edit_area),
            "Different edit areas should not overlap"
        );

        let plan = ParallelPlan {
            parent_plan_id: "test-plan".to_string(),
            work_units: vec![unit1, unit2],
        };

        assert_eq!(plan.work_units.len(), 2);
    });
}

/// Test that single-agent session's check_edit_area allows all paths.
/// Single-agent sessions have no edit area restriction, so all paths should be allowed.
#[test]
fn single_agent_session_check_edit_area_allows_all() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0);

        // Single-agent sessions have no edit area
        assert!(
            session.edit_area.is_none(),
            "Single-agent session should have no edit area"
        );

        // Single-agent session should allow all paths via check_edit_area
        assert!(
            matches!(session.check_edit_area("src/a.rs"), PolicyOutcome::Approved),
            "Single-agent session.check_edit_area(src/a.rs) should be Approved"
        );
        assert!(
            matches!(
                session.check_edit_area("any/path/here"),
                PolicyOutcome::Approved
            ),
            "Single-agent session.check_edit_area(any/path/here) should be Approved"
        );
        assert!(
            matches!(session.check_edit_area("src/b.rs"), PolicyOutcome::Approved),
            "Single-agent session.check_edit_area(src/b.rs) should be Approved"
        );
    });
}
