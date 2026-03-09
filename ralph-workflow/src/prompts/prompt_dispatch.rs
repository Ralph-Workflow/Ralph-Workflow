//! Prompt dispatch functions.
//!
//! Contains the main dispatcher functions for routing to appropriate prompt generators
//! based on role and action, as well as prompt replay functionality for checkpoint resume.

use super::prompt_config::PromptConfig;
use super::prompt_scope_key::PromptScopeKey;
use super::resume_note::generate_resume_note;
use super::types::{Action, Role};
use super::ContextLevel;
use super::TemplateContext;
use super::{
    prompt_developer_iteration_with_context, prompt_fix_with_context, prompt_plan_with_context,
};
use crate::prompts::PromptHistoryEntry;

/// Generate a prompt for any agent type.
///
/// This is the main dispatcher function that routes to the appropriate
/// prompt generator based on role and action.
///
/// The config parameter allows providing:
/// - Language-specific review guidance when the project stack has been detected
/// - PROMPT.md content for planning prompts
/// - PROMPT.md and PLAN.md content for developer iteration prompts
///
/// # Arguments
///
/// * `role` - The agent role (Developer, Reviewer, etc.)
/// * `action` - The action to perform (Plan, Iterate, Fix, etc.)
/// * `context` - The context level (minimal or normal)
/// * `template_context` - Template context for user template overrides
/// * `config` - Prompt configuration with content variables
/// * `workspace` - Workspace for resolving absolute paths
pub fn prompt_for_agent(
    role: Role,
    action: Action,
    context: ContextLevel,
    template_context: &TemplateContext,
    config: PromptConfig,
    workspace: &dyn crate::workspace::Workspace,
) -> String {
    let resume_note = if let Some(resume_ctx) = &config.resume_context {
        generate_resume_note(resume_ctx)
    } else if config.is_resume {
        // Fallback when no rich ResumeContext is available (uses simpler note)
        "\nNOTE: This session is resuming from a previous run. Previous progress is preserved in git history.\n\n".to_string()
    } else {
        String::new()
    };

    let base_prompt = match (role, action) {
        (_, Action::Plan) => prompt_plan_with_context(
            template_context,
            config.prompt_md_content.as_deref(),
            workspace,
        ),
        (Role::Developer | Role::Reviewer, Action::Iterate) => {
            let (prompt_content, plan_content) = config
                .prompt_and_plan
                .unwrap_or((String::new(), String::new()));
            prompt_developer_iteration_with_context(
                template_context,
                config.iteration.unwrap_or(1),
                config.total_iterations.unwrap_or(1),
                context,
                &prompt_content,
                &plan_content,
            )
        }
        (_, Action::Fix) => {
            let (prompt_content, plan_content, issues_content) = config
                .prompt_plan_and_issues
                .unwrap_or((String::new(), String::new(), String::new()));
            prompt_fix_with_context(
                template_context,
                &prompt_content,
                &plan_content,
                &issues_content,
                workspace,
            )
        }
    };

    // Prepend resume note if applicable
    if config.is_resume {
        format!("{resume_note}{base_prompt}")
    } else {
        base_prompt
    }
}

/// Get a stored prompt from history or generate a new one.
///
/// This function implements prompt replay for hardened resume functionality.
/// When resuming from a checkpoint, it checks if a prompt was already used
/// and returns the stored prompt for deterministic behavior. Otherwise, it
/// generates a new prompt using the provided generator function.
///
/// The lookup key is derived from `scope_key.to_string()`.
///
/// Backward-compatibility notes:
/// - Planning/Development/Review/Fix/ConflictResolution keys preserve the legacy
///   `format!()` shapes for existing checkpoint `prompt_history` maps.
/// - Commit keys intentionally include the iteration dimension (`..._iter{iter}_...`).
///   Older checkpoints that stored attempt-only commit keys (pre-RFC-007) will
///   regenerate commit prompts on resume rather than replay potentially-stale entries.
///
/// # Content-ID Validation (RFC-007)
///
/// When `current_content_id` is `Some` and the stored entry has a `content_id`
/// that differs, the entry is treated as a cache miss and a fresh prompt is
/// generated. This prevents stale-content replay when the materialized inputs
/// have changed since the prompt was generated.
///
/// If `current_content_id` is `None` or the stored entry has no `content_id`
/// (legacy entries), replay proceeds without content-id validation for backward
/// compatibility.
///
/// # Arguments
///
/// * `scope_key` - Typed prompt scope key. Its `Display` string is used for
///   the `HashMap` lookup.
/// * `prompt_history` - The reducer-owned prompt history from `PipelineState`
/// * `current_content_id` - Optional content-id of the current materialized inputs.
///   When `Some`, used to validate that stored entry matches current content.
/// * `generator` - Function to generate the prompt if not found in history
///
/// # Returns
///
/// A tuple of (prompt, `was_replayed`) where:
/// - `prompt` is the prompt string (either replayed or newly generated)
/// - `was_replayed` is true if the prompt came from history, false if newly generated
///
/// # Example
///
/// ```ignore
/// let scope_key = PromptScopeKey::for_development(iteration, None, RetryMode::Normal, recovery_epoch);
/// let (prompt, was_replayed) = get_stored_or_generate_prompt(
///     &scope_key,
///     &state.prompt_history,
///     None,
///     || prompt_for_agent(role, action, context, template_context, config),
/// );
/// if was_replayed {
///     logger.info("Using stored prompt from checkpoint for determinism");
/// }
/// ```
pub fn get_stored_or_generate_prompt<F, S: std::hash::BuildHasher>(
    scope_key: &PromptScopeKey,
    prompt_history: &std::collections::HashMap<String, PromptHistoryEntry, S>,
    current_content_id: Option<&str>,
    generator: F,
) -> (String, bool)
where
    F: FnOnce() -> String,
{
    let key = scope_key.to_string();
    if let Some(entry) = prompt_history.get(&key) {
        // Content-id validation: if both stored and current content-ids are Some
        // and differ, treat as cache miss to prevent stale-content replay.
        let content_id_mismatch = match (entry.content_id.as_deref(), current_content_id) {
            (Some(stored_id), Some(current_id)) => stored_id != current_id,
            // One or both content-ids absent → replay without validation (backward compat)
            _ => false,
        };

        if content_id_mismatch {
            // Content changed: generate fresh prompt, do not replay stale entry
            (generator(), false)
        } else {
            (entry.content.clone(), true)
        }
    } else {
        (generator(), false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::prompts::prompt_scope_key::RetryMode;

    #[test]
    fn test_get_stored_or_generate_prompt_replays_when_available() {
        let scope_key = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        let mut history = std::collections::HashMap::new();
        // Use the Display string as the key (matches legacy format!() output)
        history.insert(
            scope_key.to_string(),
            PromptHistoryEntry::from_string("stored prompt".to_string()),
        );

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, None, || {
                "generated prompt".to_string()
            });

        assert_eq!(prompt, "stored prompt");
        assert!(was_replayed, "Should have replayed the stored prompt");
    }

    #[test]
    fn test_get_stored_or_generate_prompt_generates_when_not_available() {
        let scope_key = PromptScopeKey::for_development(2, None, RetryMode::Normal, 0);
        let history = std::collections::HashMap::new();

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, None, || {
                "generated prompt".to_string()
            });

        assert_eq!(prompt, "generated prompt");
        assert!(!was_replayed, "Should have generated a new prompt");
    }

    #[test]
    fn test_get_stored_or_generate_prompt_with_empty_history() {
        let scope_key = PromptScopeKey::for_commit(1, 1, RetryMode::Normal, 0);
        let history = std::collections::HashMap::new();

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, None, || {
                "fresh prompt".to_string()
            });

        assert_eq!(prompt, "fresh prompt");
        assert!(
            !was_replayed,
            "Should have generated a new prompt for empty history"
        );
    }

    #[test]
    fn test_key_lookup_uses_display_string() {
        // Verify that the function uses the Display string for lookup,
        // maintaining backward-compat with legacy format!() checkpoint keys.
        let scope_key = PromptScopeKey::for_commit(2, 1, RetryMode::Xsd { count: 1 }, 0);
        let expected_key = "commit_message_attempt_iter2_1_xsd_retry_1";
        let mut history = std::collections::HashMap::new();
        history.insert(
            expected_key.to_string(),
            PromptHistoryEntry::from_string("stored xsd retry prompt".to_string()),
        );

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, None, || "new prompt".to_string());

        assert_eq!(prompt, "stored xsd retry prompt");
        assert!(
            was_replayed,
            "Should replay using Display string '{expected_key}' as key"
        );
    }

    #[test]
    fn test_recovery_epoch_does_not_affect_lookup_key() {
        // Two keys with different recovery_epoch but same other dims produce the same
        // Display string, so they look up the same entry in history.
        let scope_key_epoch0 = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        let scope_key_epoch1 = PromptScopeKey::for_planning(1, RetryMode::Normal, 1);
        let mut history = std::collections::HashMap::new();
        history.insert(
            scope_key_epoch0.to_string(),
            PromptHistoryEntry::from_string("stored".to_string()),
        );

        // epoch1 should still find the entry stored under epoch0's Display string
        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key_epoch1, &history, None, || "new".to_string());
        assert_eq!(prompt, "stored");
        assert!(
            was_replayed,
            "Epoch change alone must not bust the history lookup key"
        );
    }

    #[test]
    fn test_content_id_match_replays_prompt() {
        let scope_key = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        let mut history = std::collections::HashMap::new();
        history.insert(
            scope_key.to_string(),
            PromptHistoryEntry {
                content: "stored prompt".to_string(),
                content_id: Some("abc123".to_string()),
            },
        );

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, Some("abc123"), || {
                "generated".to_string()
            });

        assert_eq!(prompt, "stored prompt");
        assert!(was_replayed, "Should replay when content-ids match");
    }

    #[test]
    fn test_content_id_mismatch_generates_fresh_prompt() {
        let scope_key = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        let mut history = std::collections::HashMap::new();
        history.insert(
            scope_key.to_string(),
            PromptHistoryEntry {
                content: "stale prompt".to_string(),
                content_id: Some("old_hash".to_string()),
            },
        );

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, Some("new_hash"), || {
                "fresh prompt".to_string()
            });

        assert_eq!(prompt, "fresh prompt");
        assert!(
            !was_replayed,
            "Should generate fresh prompt when content-ids differ"
        );
    }

    #[test]
    fn test_no_content_id_in_stored_entry_replays_without_validation() {
        // Legacy entries (content_id: None) always replay without validation
        let scope_key = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        let mut history = std::collections::HashMap::new();
        history.insert(
            scope_key.to_string(),
            PromptHistoryEntry::from_string("legacy prompt".to_string()),
        );

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, Some("any_hash"), || {
                "generated".to_string()
            });

        assert_eq!(prompt, "legacy prompt");
        assert!(
            was_replayed,
            "Legacy entries with no content_id replay without content-id validation"
        );
    }

    #[test]
    fn test_no_current_content_id_replays_without_validation() {
        // When caller doesn't provide current_content_id, skip validation
        let scope_key = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        let mut history = std::collections::HashMap::new();
        history.insert(
            scope_key.to_string(),
            PromptHistoryEntry {
                content: "stored prompt".to_string(),
                content_id: Some("some_hash".to_string()),
            },
        );

        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &history, None, || "generated".to_string());

        assert_eq!(prompt, "stored prompt");
        assert!(
            was_replayed,
            "Should replay when current_content_id is None (caller does not validate)"
        );
    }

    /// SC-2: Iteration 2 development prompt is not replayed from iteration 1's history entry.
    ///
    /// Verifies that `get_stored_or_generate_prompt` generates a fresh prompt for iter2
    /// even when iter1's development prompt exists in history (same Normal retry mode).
    #[test]
    fn iteration_2_development_does_not_replay_iteration_1_prompt() {
        // Arrange: history contains the iter1 development prompt
        let iter1_key = PromptScopeKey::for_development(1, None, RetryMode::Normal, 0);
        let mut history = std::collections::HashMap::new();
        history.insert(
            iter1_key.to_string(),
            PromptHistoryEntry::from_string("iter 1 development prompt".to_string()),
        );

        // Act: request iter2 development prompt
        let iter2_key = PromptScopeKey::for_development(2, None, RetryMode::Normal, 0);
        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&iter2_key, &history, None, || {
                "iter 2 fresh development prompt".to_string()
            });

        // Assert: iter2 must generate a fresh prompt, not replay iter1's prompt
        assert!(
            !was_replayed,
            "iter2 development must NOT replay iter1 development prompt"
        );
        assert_eq!(
            prompt, "iter 2 fresh development prompt",
            "iter2 development must receive a freshly generated prompt"
        );
    }

    /// Regression test for RFC-007 root cause: commit prompt stale replay across iterations.
    ///
    /// Before the fix, commit keys were `commit_message_attempt_{attempt}` with no iteration
    /// dimension. Attempt resets to 1 on each new commit cycle, so iter2/attempt1 would
    /// collide with iter1/attempt1 and replay the stale first-cycle prompt.
    ///
    /// This test verifies that when iter1/attempt1 exists in history, requesting
    /// iter2/attempt1 does NOT replay it — the iteration dimension produces a different key.
    #[test]
    fn test_iteration_2_commit_does_not_replay_iteration_1_prompt() {
        // Arrange: history contains the iter1/attempt1 commit prompt (from a completed cycle)
        let iter1_key = PromptScopeKey::for_commit(1, 1, RetryMode::Normal, 0);
        let mut history = std::collections::HashMap::new();
        history.insert(
            iter1_key.to_string(),
            PromptHistoryEntry::from_string("iter 1 commit prompt".to_string()),
        );

        // Act: request iter2/attempt1 (attempt resets to 1 on the new cycle — the bug scenario)
        let iter2_key = PromptScopeKey::for_commit(2, 1, RetryMode::Normal, 0);
        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&iter2_key, &history, None, || {
                "iter 2 fresh commit prompt".to_string()
            });

        // Assert: iter2 must generate a fresh prompt, not replay iter1's stale prompt
        assert!(
            !was_replayed,
            "iter2/attempt1 must NOT replay iter1/attempt1"
        );
        assert_eq!(
            prompt, "iter 2 fresh commit prompt",
            "iter2 must receive a freshly generated prompt, not iter1's stale content"
        );
    }
}
