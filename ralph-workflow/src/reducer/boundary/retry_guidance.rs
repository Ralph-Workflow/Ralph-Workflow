use crate::reducer::state::{ContinuationState, SameAgentRetryReason};

const RETRY_NOTE_HEADER_PREFIX: &str = "## Retry Note (attempt ";
const RETRY_NOTE_END_SENTINEL: &str =
    "- Always produce valid XML output that matches the schema.\n";

pub fn is_same_agent_retry_prompt(prompt: &str) -> bool {
    prompt.starts_with(RETRY_NOTE_HEADER_PREFIX)
}

pub fn strip_existing_same_agent_retry_preamble(prompt: &str) -> &str {
    if !prompt.starts_with(RETRY_NOTE_HEADER_PREFIX) {
        return prompt;
    }

    let Some(idx) = prompt.find(RETRY_NOTE_END_SENTINEL) else {
        return prompt;
    };

    let after_sentinel = &prompt[idx + RETRY_NOTE_END_SENTINEL.len()..];
    after_sentinel.trim_start_matches('\n')
}

pub fn same_agent_retry_preamble(continuation: &ContinuationState) -> String {
    let attempt = continuation.same_agent_retry_count;
    let reason = continuation.same_agent_retry_reason;

    let reason_line = match reason {
        Some(SameAgentRetryReason::Timeout) => "Previous attempt timed out.".to_string(),
        Some(SameAgentRetryReason::TimeoutWithContext) => {
            // If a context file was written (session-less agent), include the path
            continuation.timeout_context_file_path.as_ref().map_or_else(
                || {
                    "Previous attempt timed out with partial progress. Your context has been preserved via session continuation.".to_string()
                },
                |context_path| {
                    format!(
                        "Previous attempt timed out with partial progress.\n\
                         Your prior context has been preserved at: {context_path}\n\
                         Read that file first to continue from where you left off."
                    )
                },
            )
        }
        Some(SameAgentRetryReason::InternalError) => {
            "Previous attempt failed with an internal/unknown error.".to_string()
        }
        Some(SameAgentRetryReason::Other) => {
            "Previous attempt failed with a non-retriable error (non-auth, non-rate-limit)."
                .to_string()
        }
        None => "Retrying after a transient invocation failure.".to_string(),
    };

    format!(
        "## Retry Note (attempt {attempt})\n\
{reason_line}\n\
\n\
Please retry with these constraints:\n\
- Reduce scope; do the smallest safe change.\n\
- Break work into small, verifiable steps; avoid long-running commands.\n\
- Prefer targeted tests and quick checks; only broaden if needed.\n\
- If output is large, summarize and write artifacts to the required files.\n\
- Always produce valid XML output that matches the schema.\n"
    )
}

#[cfg(test)]
mod tests_retry_preamble {
    use super::*;

    #[test]
    fn test_strip_existing_retry_preamble_removes_timeout_reason() {
        // AC-7: Verify retry prompts never accumulate "Previous attempt timed out." sentences
        let continuation = ContinuationState {
            same_agent_retry_count: 2,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::default()
        };

        let preamble = same_agent_retry_preamble(&continuation);
        assert!(
            preamble.contains("Previous attempt timed out."),
            "Preamble should contain timeout message"
        );

        // Simulate a retry prompt that already has a preamble
        let original_prompt = "Original task instructions here";
        let first_retry = format!("{preamble}\n\n{original_prompt}");

        // Strip should remove the first preamble
        let stripped = strip_existing_same_agent_retry_preamble(&first_retry);
        assert!(
            !stripped.starts_with(RETRY_NOTE_HEADER_PREFIX),
            "Stripped prompt should not start with retry header"
        );
        assert!(
            stripped.starts_with("Original task"),
            "Stripped prompt should start with original task"
        );

        // Second retry should still have exactly one preamble
        let continuation2 = ContinuationState {
            same_agent_retry_count: 3,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::default()
        };
        let second_preamble = same_agent_retry_preamble(&continuation2);
        let second_retry = format!("{second_preamble}\n\n{stripped}");

        // Count occurrences of timeout message - should be exactly 1
        let timeout_count = second_retry.matches("Previous attempt timed out.").count();
        assert_eq!(
            timeout_count, 1,
            "Retry prompt should contain exactly one timeout message, found {timeout_count}",
        );
    }

    #[test]
    fn test_strip_existing_retry_preamble_preserves_prompts_without_preamble() {
        let prompt = "Regular task without any retry preamble";
        let stripped = strip_existing_same_agent_retry_preamble(prompt);
        assert_eq!(
            stripped, prompt,
            "Prompts without preamble should be unchanged"
        );
    }

    #[test]
    fn test_strip_existing_retry_preamble_handles_internal_error() {
        let continuation = ContinuationState {
            same_agent_retry_count: 2,
            same_agent_retry_reason: Some(SameAgentRetryReason::InternalError),
            ..ContinuationState::default()
        };

        let preamble = same_agent_retry_preamble(&continuation);
        let original_prompt = "Task instructions";
        let retry_prompt = format!("{preamble}\n\n{original_prompt}");

        let stripped = strip_existing_same_agent_retry_preamble(&retry_prompt);
        assert!(
            !stripped.contains("internal/unknown error"),
            "Stripped prompt should not contain internal error message"
        );
        assert!(
            stripped.starts_with("Task instructions"),
            "Stripped prompt should start with original task"
        );
    }

    #[test]
    fn test_timeout_with_context_preamble_indicates_preserved_context() {
        // AC-1: TimeoutWithContext should indicate context preservation
        let continuation = ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::TimeoutWithContext),
            ..ContinuationState::default()
        };

        let preamble = same_agent_retry_preamble(&continuation);
        assert!(
            preamble.contains("partial progress"),
            "TimeoutWithContext preamble should mention partial progress"
        );
        assert!(
            preamble.contains("context has been preserved"),
            "TimeoutWithContext preamble should indicate context preservation"
        );
        // Should NOT contain the plain timeout message
        assert!(
            !preamble.contains("Previous attempt timed out.\n"),
            "TimeoutWithContext preamble should not use plain timeout message"
        );
    }

    #[test]
    fn test_strip_existing_retry_preamble_handles_timeout_with_context() {
        let continuation = ContinuationState {
            same_agent_retry_count: 2,
            same_agent_retry_reason: Some(SameAgentRetryReason::TimeoutWithContext),
            ..ContinuationState::default()
        };

        let preamble = same_agent_retry_preamble(&continuation);
        let original_prompt = "Task instructions";
        let retry_prompt = format!("{preamble}\n\n{original_prompt}");

        let stripped = strip_existing_same_agent_retry_preamble(&retry_prompt);
        assert!(
            !stripped.contains("partial progress"),
            "Stripped prompt should not contain TimeoutWithContext message"
        );
        assert!(
            stripped.starts_with("Task instructions"),
            "Stripped prompt should start with original task"
        );
    }
}
