//! Integration tests for rebase conflict prompt replay determinism.
//!
//! RFC-007 architectural correction #2: all prompt-history keys must use typed
//! `PromptScopeKey` constructors. These tests verify that:
//!
//! 1. `PromptScopeKey::for_conflict_resolution` produces Display strings that are
//!    byte-identical to the legacy `format!("{}_conflict_resolution", phase)` pattern.
//! 2. `get_stored_or_generate_prompt` with a conflict-resolution scope key correctly
//!    replays a stored prompt from history and returns `was_replayed = true`.
//! 3. When no stored prompt exists, `get_stored_or_generate_prompt` generates a fresh
//!    prompt and returns `was_replayed = false`.

use ralph_workflow::prompts::{get_stored_or_generate_prompt, PromptHistoryEntry, PromptScopeKey};

use crate::test_timeout::with_default_timeout;

// ============================================================================
// Key format backward-compatibility tests
// ============================================================================

/// Verify that `for_conflict_resolution` produces the same Display string as the
/// legacy `format!("{}_conflict_resolution", phase.to_lowercase())` pattern it replaces.
///
/// This is the critical backward-compatibility invariant: existing checkpoints that
/// stored conflict resolution prompts under the old raw-string key must continue to
/// be replayed correctly after the migration.
#[test]
fn rebase_conflict_scope_key_display_matches_legacy_format() {
    with_default_timeout(|| {
        // Legacy pattern: format!("{}_conflict_resolution", "planning".to_lowercase())
        let legacy_key = format!("{}_conflict_resolution", "planning".to_lowercase());
        let typed_key = PromptScopeKey::for_conflict_resolution("planning", 0).to_string();
        assert_eq!(
            typed_key, legacy_key,
            "PromptScopeKey::for_conflict_resolution Display must be byte-identical \
             to the legacy format!() string for checkpoint backward-compatibility"
        );
    });
}

#[test]
fn rebase_conflict_scope_key_display_matches_legacy_for_rebase_only_phase() {
    with_default_timeout(|| {
        // Verifies the "RebaseOnly" phase used in --rebase-only mode
        let legacy_key = format!("{}_conflict_resolution", "RebaseOnly".to_lowercase());
        let typed_key = PromptScopeKey::for_conflict_resolution("RebaseOnly", 0).to_string();
        assert_eq!(typed_key, legacy_key);
    });
}

// ============================================================================
// Prompt replay determinism tests
// ============================================================================

/// When `prompt_history` contains a stored conflict resolution prompt, resuming
/// from checkpoint must replay the stored prompt (`was_replayed = true`) rather
/// than regenerating it.
///
/// This is the core RFC-007 invariant for rebase conflict prompts: deterministic
/// resume must not invoke the prompt generator when a stored entry is available.
#[test]
fn rebase_conflict_prompt_replay_is_deterministic() {
    with_default_timeout(|| {
        let scope_key = PromptScopeKey::for_conflict_resolution("planning", 0);

        let mut prompt_history = std::collections::HashMap::new();
        prompt_history.insert(
            scope_key.to_string(),
            PromptHistoryEntry::from_string(
                "STORED CONFLICT RESOLUTION PROMPT FOR REPLAY".to_string(),
            ),
        );

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &prompt_history, None, || {
                "FRESHLY GENERATED PROMPT — must not be returned".to_string()
            });

        assert!(
            was_replayed,
            "Conflict resolution prompt must be replayed from checkpoint history \
             when a stored entry exists (was_replayed must be true)"
        );
        assert_eq!(
            prompt, "STORED CONFLICT RESOLUTION PROMPT FOR REPLAY",
            "Replayed prompt must match the stored checkpoint entry"
        );
    });
}

/// When no stored prompt exists in `prompt_history`, `get_stored_or_generate_prompt`
/// must generate a fresh prompt and return `was_replayed = false`.
#[test]
fn rebase_conflict_prompt_generates_fresh_when_no_history() {
    with_default_timeout(|| {
        let scope_key = PromptScopeKey::for_conflict_resolution("development", 0);
        let prompt_history: std::collections::HashMap<String, PromptHistoryEntry> =
            std::collections::HashMap::new();

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &prompt_history, None, || {
                "FRESHLY GENERATED CONFLICT PROMPT".to_string()
            });

        assert!(
            !was_replayed,
            "was_replayed must be false when no stored prompt exists in history"
        );
        assert_eq!(prompt, "FRESHLY GENERATED CONFLICT PROMPT");
    });
}

/// Verifies the `UIEvent::PromptReplayHit` observability invariant: the key used
/// for a conflict resolution prompt replay is the typed scope key's Display string,
/// which equals the legacy format!() key format.
///
/// This test uses `get_stored_or_generate_prompt` directly, which is the same
/// function called internally after the RFC-007 migration. The `was_replayed`
/// return value is the source of truth for `UIEvent::PromptReplayHit`.
#[test]
fn rebase_conflict_prompt_replay_hit_fires_with_was_replayed_true() {
    with_default_timeout(|| {
        let scope_key = PromptScopeKey::for_conflict_resolution("planning", 0);
        let expected_key = "planning_conflict_resolution";

        // Verify the key format matches what would be emitted in UIEvent::PromptReplayHit
        assert_eq!(
            scope_key.to_string(),
            expected_key,
            "The PromptScopeKey Display string must match the expected checkpoint key \
             for UIEvent::PromptReplayHit key field"
        );

        let mut prompt_history = std::collections::HashMap::new();
        prompt_history.insert(
            expected_key.to_string(),
            PromptHistoryEntry::from_string("Stored conflict prompt text".to_string()),
        );

        let (_prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &prompt_history, None, || {
                "generated".to_string()
            });

        assert!(
            was_replayed,
            "get_stored_or_generate_prompt must return was_replayed=true, \
             which drives UIEvent::PromptReplayHit {{ was_replayed: true }}"
        );
    });
}

/// Legacy checkpoints stored conflict resolution prompts as bare strings (v0 format).
/// The `PromptHistoryEntry` custom deserializer must handle both v0 and v1 formats.
/// This test verifies that a v0-format conflict resolution entry is replayed correctly
/// by the typed scope key lookup.
#[test]
fn rebase_conflict_prompt_replays_from_legacy_v0_checkpoint_entry() {
    with_default_timeout(|| {
        let scope_key = PromptScopeKey::for_conflict_resolution("planning", 0);

        // Simulate a v0 legacy checkpoint entry (bare string, no content_id)
        let legacy_entry: PromptHistoryEntry =
            serde_json::from_str(r#""Legacy stored conflict prompt from v0 checkpoint""#)
                .expect("v0 bare-string deserialization must succeed");

        let mut prompt_history = std::collections::HashMap::new();
        prompt_history.insert(scope_key.to_string(), legacy_entry);

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &prompt_history, None, || {
                "generated".to_string()
            });

        assert!(was_replayed, "Legacy v0 conflict entry must be replayed");
        assert_eq!(prompt, "Legacy stored conflict prompt from v0 checkpoint");
    });
}

/// `recovery_epoch` must NOT affect the Display string used as the `prompt_history`
/// lookup key. Two scope keys for the same conflict phase but different epochs must
/// look up the same entry, preserving checkpoint backward-compatibility.
#[test]
fn rebase_conflict_recovery_epoch_does_not_affect_replay_key() {
    with_default_timeout(|| {
        let scope_key_epoch0 = PromptScopeKey::for_conflict_resolution("planning", 0);
        let scope_key_epoch1 = PromptScopeKey::for_conflict_resolution("planning", 1);

        assert_eq!(
            scope_key_epoch0.to_string(),
            scope_key_epoch1.to_string(),
            "recovery_epoch must not affect the Display string for conflict resolution keys"
        );

        let mut prompt_history = std::collections::HashMap::new();
        prompt_history.insert(
            scope_key_epoch0.to_string(),
            PromptHistoryEntry::from_string("stored".to_string()),
        );

        // epoch1 key must find the entry stored under epoch0's Display string
        let (_prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key_epoch1, &prompt_history, None, || {
                "new".to_string()
            });
        assert!(
            was_replayed,
            "Epoch change alone must not bust the history lookup key for conflict resolution prompts"
        );
    });
}
