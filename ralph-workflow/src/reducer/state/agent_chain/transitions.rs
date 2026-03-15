// State transition methods for AgentChainState.
//
// These methods implement the fallback chain progression: advancing models,
// switching agents, and starting retry cycles with backoff.

use std::sync::Arc;

use super::backoff::calculate_backoff_delay_ms;
use super::{AgentChainState, AgentDrain, AgentRole, DrainMode, RateLimitContinuationPrompt};

impl AgentChainState {
    #[must_use]
    pub fn advance_to_next_model(&self) -> Self {
        let start_agent_index = self.current_agent_index;

        // When models are configured, we try each model for the current agent once.
        // If the models list is exhausted, advance to the next agent/retry cycle
        // instead of looping models indefinitely.
        let mut next = match self.models_per_agent.get(self.current_agent_index) {
            Some(models) if !models.is_empty() => {
                if self.current_model_index + 1 < models.len() {
                    // Simple model advance - only increment model index
                    Self {
                        agents: Arc::clone(&self.agents),
                        current_agent_index: self.current_agent_index,
                        models_per_agent: Arc::clone(&self.models_per_agent),
                        current_model_index: self.current_model_index + 1,
                        retry_cycle: self.retry_cycle,
                        max_cycles: self.max_cycles,
                        retry_delay_ms: self.retry_delay_ms,
                        backoff_multiplier: self.backoff_multiplier,
                        max_backoff_ms: self.max_backoff_ms,
                        backoff_pending_ms: self.backoff_pending_ms,
                        current_role: self.current_role,
                        current_drain: self.current_drain,
                        current_mode: self.current_mode,
                        rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
                        last_session_id: self.last_session_id.clone(),
                        last_failure_reason: self.last_failure_reason.clone(),
                    }
                } else {
                    self.switch_to_next_agent()
                }
            }
            _ => self.switch_to_next_agent(),
        };

        if next.current_agent_index != start_agent_index {
            next.last_session_id = None;
        }

        next
    }

    #[must_use]
    pub fn switch_to_next_agent(&self) -> Self {
        if self.current_agent_index + 1 < self.agents.len() {
            // Advance to next agent
            Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: self.current_agent_index + 1,
                models_per_agent: Arc::clone(&self.models_per_agent),
                current_model_index: 0,
                retry_cycle: self.retry_cycle,
                max_cycles: self.max_cycles,
                retry_delay_ms: self.retry_delay_ms,
                backoff_multiplier: self.backoff_multiplier,
                max_backoff_ms: self.max_backoff_ms,
                backoff_pending_ms: None,
                current_role: self.current_role,
                current_drain: self.current_drain,
                current_mode: self.current_mode,
                rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
                last_session_id: self.last_session_id.clone(),
                last_failure_reason: self.last_failure_reason.clone(),
            }
        } else {
            // Wrap around to first agent and increment retry cycle
            let new_retry_cycle = self.retry_cycle + 1;
            let new_backoff_pending_ms = if new_retry_cycle >= self.max_cycles {
                None
            } else {
                // Create temporary state to calculate backoff
                let temp = Self {
                    agents: Arc::clone(&self.agents),
                    current_agent_index: 0,
                    models_per_agent: Arc::clone(&self.models_per_agent),
                    current_model_index: 0,
                    retry_cycle: new_retry_cycle,
                    max_cycles: self.max_cycles,
                    retry_delay_ms: self.retry_delay_ms,
                    backoff_multiplier: self.backoff_multiplier,
                    max_backoff_ms: self.max_backoff_ms,
                    backoff_pending_ms: None,
                    current_role: self.current_role,
                    current_drain: self.current_drain,
                    current_mode: self.current_mode,
                    rate_limit_continuation_prompt: None,
                    last_session_id: None,
                    last_failure_reason: None,
                };
                Some(temp.calculate_backoff_delay_ms_for_retry_cycle())
            };

            Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: 0,
                models_per_agent: Arc::clone(&self.models_per_agent),
                current_model_index: 0,
                retry_cycle: new_retry_cycle,
                max_cycles: self.max_cycles,
                retry_delay_ms: self.retry_delay_ms,
                backoff_multiplier: self.backoff_multiplier,
                max_backoff_ms: self.max_backoff_ms,
                backoff_pending_ms: new_backoff_pending_ms,
                current_role: self.current_role,
                current_drain: self.current_drain,
                current_mode: self.current_mode,
                rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
                last_session_id: self.last_session_id.clone(),
                last_failure_reason: self.last_failure_reason.clone(),
            }
        }
    }

    /// Switch to a specific agent by name.
    ///
    /// If `to_agent` is unknown, falls back to `switch_to_next_agent()` to keep the
    /// reducer deterministic.
    #[must_use]
    pub fn switch_to_agent_named(&self, to_agent: &str) -> Self {
        let Some(target_index) = self.agents.iter().position(|a| a == to_agent) else {
            return self.switch_to_next_agent();
        };

        if target_index == self.current_agent_index {
            // Same agent - just reset model index
            return Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: self.current_agent_index,
                models_per_agent: Arc::clone(&self.models_per_agent),
                current_model_index: 0,
                retry_cycle: self.retry_cycle,
                max_cycles: self.max_cycles,
                retry_delay_ms: self.retry_delay_ms,
                backoff_multiplier: self.backoff_multiplier,
                max_backoff_ms: self.max_backoff_ms,
                backoff_pending_ms: None,
                current_role: self.current_role,
                current_drain: self.current_drain,
                current_mode: self.current_mode,
                rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
                last_session_id: self.last_session_id.clone(),
                last_failure_reason: self.last_failure_reason.clone(),
            };
        }

        if target_index <= self.current_agent_index {
            // Treat switching to an earlier agent as starting a new retry cycle.
            let new_retry_cycle = self.retry_cycle + 1;
            let new_backoff_pending_ms = if new_retry_cycle >= self.max_cycles && target_index == 0
            {
                None
            } else {
                // Create temporary state to calculate backoff
                let temp = Self {
                    agents: Arc::clone(&self.agents),
                    current_agent_index: target_index,
                    models_per_agent: Arc::clone(&self.models_per_agent),
                    current_model_index: 0,
                    retry_cycle: new_retry_cycle,
                    max_cycles: self.max_cycles,
                    retry_delay_ms: self.retry_delay_ms,
                    backoff_multiplier: self.backoff_multiplier,
                    max_backoff_ms: self.max_backoff_ms,
                    backoff_pending_ms: None,
                    current_role: self.current_role,
                    current_drain: self.current_drain,
                    current_mode: self.current_mode,
                    rate_limit_continuation_prompt: None,
                    last_session_id: None,
                    last_failure_reason: None,
                };
                Some(temp.calculate_backoff_delay_ms_for_retry_cycle())
            };

            Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: target_index,
                models_per_agent: Arc::clone(&self.models_per_agent),
                current_model_index: 0,
                retry_cycle: new_retry_cycle,
                max_cycles: self.max_cycles,
                retry_delay_ms: self.retry_delay_ms,
                backoff_multiplier: self.backoff_multiplier,
                max_backoff_ms: self.max_backoff_ms,
                backoff_pending_ms: new_backoff_pending_ms,
                current_role: self.current_role,
                current_drain: self.current_drain,
                current_mode: self.current_mode,
                rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
                last_session_id: self.last_session_id.clone(),
                last_failure_reason: self.last_failure_reason.clone(),
            }
        } else {
            // Advancing to later agent
            Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: target_index,
                models_per_agent: Arc::clone(&self.models_per_agent),
                current_model_index: 0,
                retry_cycle: self.retry_cycle,
                max_cycles: self.max_cycles,
                retry_delay_ms: self.retry_delay_ms,
                backoff_multiplier: self.backoff_multiplier,
                max_backoff_ms: self.max_backoff_ms,
                backoff_pending_ms: None,
                current_role: self.current_role,
                current_drain: self.current_drain,
                current_mode: self.current_mode,
                rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
                last_session_id: self.last_session_id.clone(),
                last_failure_reason: self.last_failure_reason.clone(),
            }
        }
    }

    /// Switch to next agent after rate limit, preserving prompt for continuation.
    ///
    /// This is used when an agent hits a 429 rate limit error. Instead of
    /// retrying with the same agent (which would likely hit rate limits again),
    /// we switch to the next agent and preserve the prompt so the new agent
    /// can continue the same work.
    #[must_use]
    pub fn switch_to_next_agent_with_prompt(&self, prompt: Option<String>) -> Self {
        let base = self.switch_to_next_agent();
        // Back-compat: older callers didn't track role. Preserve prompt only.
        Self {
            agents: base.agents,
            current_agent_index: base.current_agent_index,
            models_per_agent: base.models_per_agent,
            current_model_index: base.current_model_index,
            retry_cycle: base.retry_cycle,
            max_cycles: base.max_cycles,
            retry_delay_ms: base.retry_delay_ms,
            backoff_multiplier: base.backoff_multiplier,
            max_backoff_ms: base.max_backoff_ms,
            backoff_pending_ms: base.backoff_pending_ms,
            current_role: base.current_role,
            current_drain: base.current_drain,
            current_mode: base.current_mode,
            rate_limit_continuation_prompt: prompt.map(|p| RateLimitContinuationPrompt {
                drain: base.current_drain,
                role: base.current_role,
                prompt: p,
            }),
            last_session_id: base.last_session_id,
            last_failure_reason: base.last_failure_reason.clone(),
        }
    }

    /// Switch to next agent after rate limit, preserving prompt for continuation (role-scoped).
    #[must_use]
    pub fn switch_to_next_agent_with_prompt_for_role(
        &self,
        role: AgentRole,
        prompt: Option<String>,
    ) -> Self {
        let base = self.switch_to_next_agent();
        Self {
            agents: base.agents,
            current_agent_index: base.current_agent_index,
            models_per_agent: base.models_per_agent,
            current_model_index: base.current_model_index,
            retry_cycle: base.retry_cycle,
            max_cycles: base.max_cycles,
            retry_delay_ms: base.retry_delay_ms,
            backoff_multiplier: base.backoff_multiplier,
            max_backoff_ms: base.max_backoff_ms,
            backoff_pending_ms: base.backoff_pending_ms,
            current_role: base.current_role,
            current_drain: base.current_drain,
            current_mode: base.current_mode,
            rate_limit_continuation_prompt: prompt.map(|p| RateLimitContinuationPrompt {
                drain: base.current_drain,
                role,
                prompt: p,
            }),
            last_session_id: base.last_session_id,
            last_failure_reason: base.last_failure_reason.clone(),
        }
    }

    /// Clear continuation prompt after successful execution.
    ///
    /// Called when an agent successfully completes its task, clearing any
    /// saved prompt context from previous rate-limited agents.
    #[must_use]
    pub fn clear_continuation_prompt(&self) -> Self {
        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: self.current_agent_index,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: self.current_model_index,
            retry_cycle: self.retry_cycle,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: self.backoff_pending_ms,
            current_role: self.current_role,
            current_drain: self.current_drain,
            current_mode: self.current_mode,
            rate_limit_continuation_prompt: None,
            last_session_id: self.last_session_id.clone(),
            last_failure_reason: None,
        }
    }

    #[must_use]
    pub fn reset_for_drain(&self, drain: AgentDrain) -> Self {
        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: 0,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: 0,
            retry_cycle: 0,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: None,
            current_role: drain.role(),
            current_drain: drain,
            current_mode: DrainMode::Normal,
            rate_limit_continuation_prompt: None,
            last_session_id: None,
            last_failure_reason: None,
        }
    }

    #[must_use]
    pub fn reset_for_role(&self, role: AgentRole) -> Self {
        self.reset_for_drain(match role {
            AgentRole::Developer => AgentDrain::Development,
            AgentRole::Reviewer => AgentDrain::Review,
            AgentRole::Commit => AgentDrain::Commit,
            AgentRole::Analysis => AgentDrain::Analysis,
        })
    }

    #[must_use]
    pub fn reset(&self) -> Self {
        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: 0,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: 0,
            retry_cycle: self.retry_cycle,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: None,
            current_role: self.current_role,
            current_drain: self.current_drain,
            current_mode: DrainMode::Normal,
            rate_limit_continuation_prompt: None,
            last_session_id: None,
            last_failure_reason: None,
        }
    }

    /// Store session ID from agent response for potential reuse.
    #[must_use]
    pub fn with_session_id(&self, session_id: Option<String>) -> Self {
        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: self.current_agent_index,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: self.current_model_index,
            retry_cycle: self.retry_cycle,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: self.backoff_pending_ms,
            current_role: self.current_role,
            current_drain: self.current_drain,
            current_mode: self.current_mode,
            rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
            last_session_id: session_id,
            last_failure_reason: self.last_failure_reason.clone(),
        }
    }

    /// Store last failure reason for CLI output context.
    #[must_use]
    pub fn with_failure_reason(&self, reason: Option<String>) -> Self {
        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: self.current_agent_index,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: self.current_model_index,
            retry_cycle: self.retry_cycle,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: self.backoff_pending_ms,
            current_role: self.current_role,
            current_drain: self.current_drain,
            current_mode: self.current_mode,
            rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
            last_session_id: self.last_session_id.clone(),
            last_failure_reason: reason,
        }
    }

    /// Clear session ID (e.g., when switching agents or starting new work).
    #[must_use]
    pub fn clear_session_id(&self) -> Self {
        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: self.current_agent_index,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: self.current_model_index,
            retry_cycle: self.retry_cycle,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: self.backoff_pending_ms,
            current_role: self.current_role,
            current_drain: self.current_drain,
            current_mode: self.current_mode,
            rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
            last_session_id: None,
            last_failure_reason: self.last_failure_reason.clone(),
        }
    }

    #[must_use]
    pub fn start_retry_cycle(&self) -> Self {
        let new_retry_cycle = self.retry_cycle + 1;
        let new_backoff_pending_ms = if new_retry_cycle >= self.max_cycles {
            None
        } else {
            // Create temporary state to calculate backoff
            let temp = Self {
                agents: Arc::clone(&self.agents),
                current_agent_index: 0,
                models_per_agent: Arc::clone(&self.models_per_agent),
                current_model_index: 0,
                retry_cycle: new_retry_cycle,
                max_cycles: self.max_cycles,
                retry_delay_ms: self.retry_delay_ms,
                backoff_multiplier: self.backoff_multiplier,
                max_backoff_ms: self.max_backoff_ms,
                backoff_pending_ms: None,
                current_role: self.current_role,
                current_drain: self.current_drain,
                current_mode: self.current_mode,
                rate_limit_continuation_prompt: None,
                last_session_id: None,
                last_failure_reason: None,
            };
            Some(temp.calculate_backoff_delay_ms_for_retry_cycle())
        };

        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: 0,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: 0,
            retry_cycle: new_retry_cycle,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: new_backoff_pending_ms,
            current_role: self.current_role,
            current_drain: self.current_drain,
            current_mode: self.current_mode,
            rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
            last_session_id: self.last_session_id.clone(),
            last_failure_reason: self.last_failure_reason.clone(),
        }
    }

    #[must_use]
    pub fn clear_backoff_pending(&self) -> Self {
        Self {
            agents: Arc::clone(&self.agents),
            current_agent_index: self.current_agent_index,
            models_per_agent: Arc::clone(&self.models_per_agent),
            current_model_index: self.current_model_index,
            retry_cycle: self.retry_cycle,
            max_cycles: self.max_cycles,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            backoff_pending_ms: None,
            current_role: self.current_role,
            current_drain: self.current_drain,
            current_mode: self.current_mode,
            rate_limit_continuation_prompt: self.rate_limit_continuation_prompt.clone(),
            last_session_id: self.last_session_id.clone(),
            last_failure_reason: self.last_failure_reason.clone(),
        }
    }

    pub(super) fn calculate_backoff_delay_ms_for_retry_cycle(&self) -> u64 {
        // The first retry cycle should use the base delay.
        let cycle_index = self.retry_cycle.saturating_sub(1);
        calculate_backoff_delay_ms(
            self.retry_delay_ms,
            self.backoff_multiplier,
            self.max_backoff_ms,
            cycle_index,
        )
    }
}

#[cfg(test)]
mod backoff_semantics_tests {
    use super::*;

    #[test]
    fn test_switch_to_agent_named_preserves_backoff_when_retry_cycle_hits_max_but_state_is_not_exhausted(
    ) {
        let mut state = AgentChainState::initial().with_agents(
            vec!["a".to_string(), "b".to_string(), "c".to_string()],
            vec![vec![], vec![], vec![]],
            AgentRole::Developer,
        );
        state.max_cycles = 2;
        state.retry_cycle = 1;
        state.current_agent_index = 2;

        let next = state.switch_to_agent_named("b");

        assert_eq!(next.current_agent_index, 1);
        assert_eq!(next.retry_cycle, 2);
        assert!(
            next.backoff_pending_ms.is_some(),
            "backoff should remain pending unless the state is fully exhausted"
        );
    }
}
