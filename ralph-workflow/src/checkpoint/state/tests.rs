// Tests for checkpoint state module.
//
// Split into topic-specific test modules for maintainability.

use crate::checkpoint::EnvironmentSnapshot;

// =========================================================================
// Environment snapshot tests
// =========================================================================

#[test]
fn test_environment_snapshot_filters_sensitive_vars() {
    // Use from_env_vars to avoid touching the real process environment.
    let vars = vec![
        ("RALPH_SAFE_SETTING".to_string(), "ok".to_string()),
        ("RALPH_API_TOKEN".to_string(), "secret".to_string()),
        ("EDITOR".to_string(), "vim".to_string()),
    ];
    let snapshot = EnvironmentSnapshot::from_env_vars(vars);

    assert!(snapshot.ralph_vars.contains_key("RALPH_SAFE_SETTING"));
    assert!(
        !snapshot.ralph_vars.contains_key("RALPH_API_TOKEN"),
        "sensitive RALPH_API_TOKEN must be filtered out"
    );
    assert!(snapshot.other_vars.contains_key("EDITOR"));
}

// Workspace-based tests (feature-gated)
#[path = "tests/workspace_tests.rs"]
#[cfg(feature = "test-utils")]
mod workspace_tests;

// Checkpoint construction and serialization tests
#[path = "tests/checkpoint_construction.rs"]
mod checkpoint_construction;

// Checkpoint load error typed variant tests
#[path = "tests/checkpoint_load_error.rs"]
mod checkpoint_load_error;
