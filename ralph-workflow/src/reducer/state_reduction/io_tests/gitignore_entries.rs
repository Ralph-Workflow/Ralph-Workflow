// Gitignore entries ensured tests.
//
// Tests for the GitignoreEntriesEnsured event that ensures .gitignore
// contains required agent artifact entries.

use super::*;

#[test]
fn test_gitignore_entries_ensured_sets_flag() {
    let state = PipelineState {
        gitignore_entries_ensured: false,
        ..create_test_state()
    };

    let event = PipelineEvent::gitignore_entries_ensured(
        vec!["/PROMPT*".to_string(), ".agent/".to_string()],
        vec![],
        true,
    );

    let new_state = reduce(state, event);

    assert!(new_state.gitignore_entries_ensured);
}

#[test]
fn test_gitignore_entries_already_present() {
    let state = PipelineState {
        gitignore_entries_ensured: false,
        ..create_test_state()
    };

    let event = PipelineEvent::gitignore_entries_ensured(
        vec![],
        vec!["/PROMPT*".to_string(), ".agent/".to_string()],
        false,
    );

    let new_state = reduce(state, event);

    assert!(new_state.gitignore_entries_ensured);
}

#[test]
fn test_gitignore_entries_partial_update() {
    let state = PipelineState {
        gitignore_entries_ensured: false,
        ..create_test_state()
    };

    let event = PipelineEvent::gitignore_entries_ensured(
        vec![".agent/".to_string()],
        vec!["/PROMPT*".to_string()],
        false,
    );

    let new_state = reduce(state, event);

    assert!(new_state.gitignore_entries_ensured);
}

#[test]
fn test_gitignore_entries_ensured_idempotent() {
    // If the event is emitted again, the flag should remain true
    let state = PipelineState {
        gitignore_entries_ensured: true,
        ..create_test_state()
    };

    let event = PipelineEvent::gitignore_entries_ensured(vec![], vec![], false);

    let new_state = reduce(state, event);

    assert!(new_state.gitignore_entries_ensured);
}
