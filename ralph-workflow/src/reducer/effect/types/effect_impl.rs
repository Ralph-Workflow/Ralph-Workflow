//! Method implementations and tests for [`Effect`].

use super::effect_enum::Effect;
use crate::reducer::state::PromptMode;

impl Effect {
    /// Check whether this effect is a same-agent retry prompt (any phase).
    ///
    /// Returns `true` for any `Prepare*Prompt` variant whose `prompt_mode` is
    /// [`PromptMode::SameAgentRetry`]. This is used by tests and diagnostics to
    /// verify that transient failures produce the correct retry behavior.
    #[must_use]
    pub const fn is_same_agent_retry(&self) -> bool {
        matches!(
            self,
            Self::PreparePlanningPrompt {
                prompt_mode: PromptMode::SameAgentRetry,
                ..
            } | Self::PrepareDevelopmentPrompt {
                prompt_mode: PromptMode::SameAgentRetry,
                ..
            } | Self::PrepareReviewPrompt {
                prompt_mode: PromptMode::SameAgentRetry,
                ..
            } | Self::PrepareFixPrompt {
                prompt_mode: PromptMode::SameAgentRetry,
                ..
            } | Self::PrepareCommitPrompt {
                prompt_mode: PromptMode::SameAgentRetry,
                ..
            }
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_same_agent_retry_returns_true_for_same_agent_retry_variants() {
        let retry_mode = PromptMode::SameAgentRetry;

        let effects = vec![
            Effect::PreparePlanningPrompt {
                iteration: 0,
                prompt_mode: retry_mode,
            },
            Effect::PrepareDevelopmentPrompt {
                iteration: 1,
                prompt_mode: retry_mode,
            },
            Effect::PrepareReviewPrompt {
                pass: 0,
                prompt_mode: retry_mode,
            },
            Effect::PrepareFixPrompt {
                pass: 1,
                prompt_mode: retry_mode,
            },
            Effect::PrepareCommitPrompt {
                prompt_mode: retry_mode,
            },
        ];

        for effect in &effects {
            assert!(
                effect.is_same_agent_retry(),
                "Expected is_same_agent_retry() == true for {effect:?}"
            );
        }
    }

    #[test]
    fn test_is_same_agent_retry_returns_false_for_other_prompt_modes() {
        let effects = vec![
            Effect::PreparePlanningPrompt {
                iteration: 0,
                prompt_mode: PromptMode::Normal,
            },
            Effect::PrepareDevelopmentPrompt {
                iteration: 0,
                prompt_mode: PromptMode::XsdRetry,
            },
            Effect::PrepareReviewPrompt {
                pass: 0,
                prompt_mode: PromptMode::Continuation,
            },
            Effect::PrepareFixPrompt {
                pass: 0,
                prompt_mode: PromptMode::Normal,
            },
            Effect::PrepareCommitPrompt {
                prompt_mode: PromptMode::Normal,
            },
        ];

        for effect in &effects {
            assert!(
                !effect.is_same_agent_retry(),
                "Expected is_same_agent_retry() == false for {effect:?}"
            );
        }
    }

    #[test]
    fn test_is_same_agent_retry_returns_false_for_non_prompt_effects() {
        let effects: Vec<Effect> = vec![
            Effect::CleanupContext,
            Effect::ValidateFinalState,
            Effect::CheckCommitDiff,
            Effect::EnsureGitignoreEntries,
        ];

        for effect in &effects {
            assert!(
                !effect.is_same_agent_retry(),
                "Expected is_same_agent_retry() == false for {effect:?}"
            );
        }
    }
}
