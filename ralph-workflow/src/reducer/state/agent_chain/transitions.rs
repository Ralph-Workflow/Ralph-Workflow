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
        // When models are configured, we try each model for the current agent once.
        // If the models list is exhausted, advance to the next agent/retry cycle
        // instead of looping models indefinitely.
        //
        // Session ID handling: preserved when staying on the same agent (model advance),
        // cleared via switch_to_next_agent when switching agents or wrapping to a new cycle.
        match self.models_per_agent.get(self.current_agent_index) {
            Some(models) if !models.is_empty() => {
                if self.current_model_index + 1 < models.len() {
                    // Simple model advance - only increment model index, preserve session
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
                    // Models exhausted for current agent: switch to next agent (clears session).
                    // When at the last agent, switch_to_next_agent wraps to agent 0 and
                    // increments the retry cycle, signaling chain exhaustion.
                    self.switch_to_next_agent()
                }
            }
            // No models configured: treat as single-model agent, switch immediately.
            _ => self.switch_to_next_agent(),
        }
    }

    /// Switch to the next agent in the fallback chain.
    ///
    /// Sessions are agent-scoped: `last_session_id` is always cleared when switching agents.
    /// Callers do not need to call `clear_session_id()` afterward.
    #[must_use]
    pub fn switch_to_next_agent(&self) -> Self {
        if self.current_agent_index + 1 < self.agents.len() {
            // Advance to next agent. Session is agent-scoped and must not carry over.
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
                last_session_id: None,
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

            // Wrapping to a new retry cycle: session is stale and must be cleared.
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
                last_session_id: None,
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
                // Sessions are agent-scoped. Switching to a different (earlier) agent clears it.
                last_session_id: None,
                last_failure_reason: self.last_failure_reason.clone(),
            }
        } else {
            // Advancing to later agent. Sessions are agent-scoped; must not carry over.
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
                // Sessions are agent-scoped. Switching to a different (later) agent clears it.
                last_session_id: None,
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
            // Session IDs are agent-scoped. Starting a new retry cycle means all agents
            // were exhausted; any session from a previous cycle is stale.
            last_session_id: None,
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
mod advance_to_next_model_tests {
    use super::*;

    #[test]
    fn test_advance_to_next_model_increments_model_index_within_agent() {
        // When models remain for the current agent, model index advances and session is preserved.
        let state = AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec!["m1".to_string(), "m2".to_string()]],
                AgentRole::Developer,
            )
            .with_session_id(Some("sess".to_string()));

        let next = state.advance_to_next_model();

        assert_eq!(next.current_model_index, 1);
        assert_eq!(next.current_agent_index, 0);
        assert_eq!(
            next.last_session_id,
            Some("sess".to_string()),
            "session must be preserved when staying on the same agent"
        );
    }

    #[test]
    fn test_advance_to_next_model_switches_agent_when_models_exhausted() {
        // When the current agent has no remaining models, advance switches to next agent
        // and clears the session ID.
        let state = AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string(), "codex".to_string()],
                vec![vec!["m1".to_string()], vec!["m2".to_string()]],
                AgentRole::Developer,
            )
            .with_session_id(Some("sess".to_string()));

        let next = state.advance_to_next_model();

        assert_eq!(next.current_agent_index, 1);
        assert_eq!(next.current_model_index, 0);
        assert_eq!(
            next.last_session_id, None,
            "session must be cleared when switching to a different agent"
        );
    }

    #[test]
    fn test_advance_to_next_model_wraps_to_retry_cycle_when_all_agents_exhausted() {
        // When the last agent's last model is exhausted, the chain wraps to agent 0
        // and increments the retry cycle with a backoff delay.
        // Session is cleared because switch_to_next_agent is called, which is agent-scoped.
        let state = AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec!["m1".to_string()]],
                AgentRole::Developer,
            )
            .with_session_id(Some("sess".to_string()));

        let next = state.advance_to_next_model();

        assert_eq!(
            next.retry_cycle, 1,
            "retry cycle must increment when all agents wrap around"
        );
        assert_eq!(next.current_agent_index, 0);
        assert_eq!(next.current_model_index, 0);
        assert!(
            next.backoff_pending_ms.is_some(),
            "backoff must be set when a retry cycle begins"
        );
        // Session is cleared: switch_to_next_agent clears session at the transition level.
        assert_eq!(
            next.last_session_id, None,
            "session must be cleared when the chain wraps to a new retry cycle"
        );
    }
}

#[cfg(test)]
mod session_id_lifecycle_tests {
    use super::*;

    fn state_with_session() -> AgentChainState {
        AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string(), "codex".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("test-session".to_string()))
    }

    #[test]
    fn test_with_session_id_sets_session() {
        let state = AgentChainState::initial().with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        );
        assert_eq!(state.last_session_id, None);

        let state = state.with_session_id(Some("new-session".to_string()));
        assert_eq!(state.last_session_id, Some("new-session".to_string()));
    }

    #[test]
    fn test_with_session_id_can_clear_session() {
        let state = state_with_session();
        assert_eq!(state.last_session_id, Some("test-session".to_string()));

        let state = state.with_session_id(None);
        assert_eq!(state.last_session_id, None);
    }

    #[test]
    fn test_clear_continuation_prompt_preserves_session_id() {
        // clear_continuation_prompt must not affect last_session_id.
        let state = state_with_session();

        let next = state.clear_continuation_prompt();

        assert_eq!(
            next.last_session_id,
            Some("test-session".to_string()),
            "clear_continuation_prompt must preserve last_session_id"
        );
    }

    #[test]
    fn test_switch_to_next_agent_clears_session_at_transition_level() {
        // Sessions are agent-scoped. switch_to_next_agent always clears last_session_id
        // at the transition level — callers do not need to call clear_session_id() afterward.
        let state = state_with_session();

        let next = state.switch_to_next_agent();

        assert_eq!(next.current_agent_index, 1);
        assert_eq!(
            next.last_session_id, None,
            "switch_to_next_agent must clear last_session_id: sessions are agent-scoped"
        );
    }

    #[test]
    fn test_switch_to_next_agent_with_prompt_clears_session_at_transition_level() {
        // switch_to_next_agent_with_prompt_for_role delegates to switch_to_next_agent,
        // which clears the session. The session must be None after the transition.
        let state = state_with_session();

        let next = state.switch_to_next_agent_with_prompt_for_role(
            AgentRole::Developer,
            Some("continue here".to_string()),
        );

        assert_eq!(next.current_agent_index, 1);
        assert_eq!(
            next.last_session_id,
            None,
            "switch_to_next_agent_with_prompt clears session via the underlying switch_to_next_agent"
        );
    }

    #[test]
    fn test_start_retry_cycle_clears_session_id() {
        // start_retry_cycle signals that ALL agents were exhausted. The session from any
        // previous cycle is stale and must not be reused in the new cycle.
        let state = state_with_session();

        let next = state.start_retry_cycle();

        assert_eq!(next.current_agent_index, 0);
        assert_eq!(next.retry_cycle, 1);
        assert_eq!(
            next.last_session_id, None,
            "start_retry_cycle must clear last_session_id: sessions are agent-scoped \
             and any session from a previous cycle is stale"
        );
    }

    #[test]
    fn test_reset_for_drain_clears_session_id() {
        // reset_for_drain is a full drain reset; last_session_id must be cleared.
        let state = state_with_session();

        let next = state.reset_for_drain(AgentDrain::Review);

        assert_eq!(
            next.last_session_id, None,
            "reset_for_drain must clear last_session_id"
        );
        assert_eq!(next.current_drain, AgentDrain::Review);
    }

    #[test]
    fn test_reset_clears_session_id() {
        // reset() resets indices but preserves drain; session must still be cleared.
        let state = state_with_session();

        let next = state.reset();

        assert_eq!(
            next.last_session_id, None,
            "reset() must clear last_session_id"
        );
    }

    #[test]
    fn test_switch_to_agent_named_backward_clears_session() {
        // Jumping backward to an earlier agent — session must be cleared (agent-scoped).
        let chain = AgentChainState::initial()
            .with_agents(
                vec!["agent0".to_string(), "agent1".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_current_agent_index(1)
            .with_session_id(Some("session-abc".to_string()));

        let next = chain.switch_to_agent_named("agent0");
        assert_eq!(next.current_agent_index, 0, "should switch to agent0");
        assert_eq!(
            next.last_session_id, None,
            "session must be cleared when switching to a different (earlier) agent"
        );
    }

    #[test]
    fn test_switch_to_agent_named_forward_clears_session() {
        // Jumping forward to a later agent — session must be cleared (agent-scoped).
        let chain = AgentChainState::initial()
            .with_agents(
                vec![
                    "agent0".to_string(),
                    "agent1".to_string(),
                    "agent2".to_string(),
                ],
                vec![vec![], vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("session-xyz".to_string()));

        let next = chain.switch_to_agent_named("agent2");
        assert_eq!(next.current_agent_index, 2, "should switch to agent2");
        assert_eq!(
            next.last_session_id, None,
            "session must be cleared when switching to a different (later) agent"
        );
    }

    #[test]
    fn test_switch_to_agent_named_same_agent_preserves_session() {
        // Switching to the same agent (no-op) — session must be preserved.
        let chain = AgentChainState::initial()
            .with_agents(
                vec!["agent0".to_string(), "agent1".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            )
            .with_session_id(Some("session-keep".to_string()));

        let next = chain.switch_to_agent_named("agent0");
        assert_eq!(next.current_agent_index, 0);
        assert_eq!(
            next.last_session_id,
            Some("session-keep".to_string()),
            "session must be preserved when switching to the same agent"
        );
    }

    #[test]
    fn test_switch_to_agent_named_same_agent_resets_model_index_and_clears_backoff() {
        // switch_to_agent_named on the current agent must reset current_model_index to 0
        // and clear backoff_pending_ms, while preserving last_session_id.
        //
        // Setup: build a state where model_index > 0 and backoff_pending_ms is Some.
        // - start_retry_cycle() produces backoff_pending_ms = Some(1000) at model 0.
        // - advance_to_next_model() advances model_index to 1, preserving backoff and session.
        let chain = AgentChainState::initial()
            .with_agents(
                vec!["agent0".to_string()],
                vec![vec!["m1".to_string(), "m2".to_string()]],
                AgentRole::Developer,
            )
            .with_max_cycles(5)
            .with_backoff_policy(1000, 2.0, 60_000);

        // start_retry_cycle sets backoff_pending_ms = Some(1000) and clears session.
        let chain = chain.start_retry_cycle();
        // Restore session to verify it is preserved across the same-agent switch.
        let chain = chain.with_session_id(Some("session-keep".to_string()));
        // advance_to_next_model: model 0 → 1; backoff and session both preserved.
        let chain = chain.advance_to_next_model();

        // Verify the test setup is correct before calling switch_to_agent_named.
        assert_eq!(chain.current_agent_index, 0, "setup: must be on agent 0");
        assert_eq!(chain.current_model_index, 1, "setup: model index must be 1");
        assert!(
            chain.backoff_pending_ms.is_some(),
            "setup: backoff_pending_ms must be Some"
        );
        assert_eq!(
            chain.last_session_id,
            Some("session-keep".to_string()),
            "setup: session must be set"
        );

        // Act: switch to the same agent.
        let next = chain.switch_to_agent_named("agent0");

        assert_eq!(next.current_agent_index, 0, "must stay on agent 0");
        assert_eq!(
            next.current_model_index, 0,
            "model index must reset to 0 on same-agent switch"
        );
        assert_eq!(
            next.backoff_pending_ms, None,
            "backoff_pending_ms must be cleared on same-agent switch"
        );
        assert_eq!(
            next.last_session_id,
            Some("session-keep".to_string()),
            "session must be preserved when switching to the same agent"
        );
    }
}

#[cfg(test)]
#[path = "transitions_model_fallback_cycling_tests.rs"]
mod model_fallback_cycling_tests;

#[cfg(test)]
mod backoff_semantics_tests {
    use super::*;

    #[test]
    fn test_switch_to_agent_named_preserves_backoff_when_retry_cycle_hits_max_but_state_is_not_exhausted(
    ) {
        let state = AgentChainState::initial()
            .with_agents(
                vec!["a".to_string(), "b".to_string(), "c".to_string()],
                vec![vec![], vec![], vec![]],
                AgentRole::Developer,
            )
            .with_max_cycles(2)
            .with_retry_cycle(1)
            .with_current_agent_index(2);

        let next = state.switch_to_agent_named("b");

        assert_eq!(next.current_agent_index, 1);
        assert_eq!(next.retry_cycle, 2);
        assert!(
            next.backoff_pending_ms.is_some(),
            "backoff should remain pending unless the state is fully exhausted"
        );
    }
}
