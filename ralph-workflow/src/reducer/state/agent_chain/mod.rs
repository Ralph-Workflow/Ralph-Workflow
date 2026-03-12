// Agent fallback chain state.
//
// Contains AgentChainState and backoff computation helpers.
//
// # Performance Optimization
//
// AgentChainState uses Arc<[T]> for immutable collections (agents, models_per_agent)
// to enable cheap state copying during state transitions. This eliminates O(n) deep
// copy overhead and makes state transitions O(1) for collection fields.
//
// The reducer creates new state instances on every event, so this optimization
// significantly reduces memory allocations and improves performance.

use std::sync::Arc;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub use crate::agents::{AgentDrain, AgentRole, DrainMode};

mod backoff;
mod transitions;

/// Agent fallback chain state (explicit, not loop indices).
///
/// Tracks position in the multi-level fallback chain:
/// - Agent level (primary → fallback1 → fallback2)
/// - Model level (within each agent, try different models)
/// - Retry cycle (exhaust all agents, start over with exponential backoff)
///
/// # Memory Optimization
///
/// Uses Arc<[T]> for `agents` and `models_per_agent` collections to enable
/// cheap cloning during state transitions. Since these collections are immutable
/// after construction, `Arc::clone` only increments a reference count instead of
/// deep copying the entire collection.
#[derive(Clone, Serialize, Debug)]
pub struct AgentChainState {
    /// Agent names in fallback order. Arc<[String]> enables cheap cloning
    /// via reference counting instead of deep copying the collection.
    pub agents: Arc<[String]>,
    pub current_agent_index: usize,
    /// Models per agent. Arc for immutable outer collection with cheap cloning.
    /// Inner Vec<String> is kept for runtime indexing during model selection.
    pub models_per_agent: Arc<[Vec<String>]>,
    pub current_model_index: usize,
    pub retry_cycle: u32,
    pub max_cycles: u32,
    /// Base delay between retry cycles in milliseconds.
    #[serde(default = "default_retry_delay_ms")]
    pub retry_delay_ms: u64,
    /// Multiplier for exponential backoff.
    #[serde(default = "default_backoff_multiplier")]
    pub backoff_multiplier: f64,
    /// Maximum backoff delay in milliseconds.
    #[serde(default = "default_max_backoff_ms")]
    pub max_backoff_ms: u64,
    /// Pending backoff delay (milliseconds) that must be waited before continuing.
    #[serde(default)]
    pub backoff_pending_ms: Option<u64>,
    /// Compatibility copy of the broad capability role.
    ///
    /// Runtime code should treat `current_drain` as authoritative and derive the
    /// active role from it. This field is retained for checkpoint compatibility
    /// and diagnostics only.
    pub current_role: AgentRole,
    #[serde(default = "default_current_drain")]
    pub current_drain: AgentDrain,
    #[serde(default)]
    pub current_mode: DrainMode,
    /// Prompt context preserved from a rate-limited agent for continuation.
    ///
    /// When an agent hits 429, we save the prompt here so the next agent can
    /// continue the SAME role/task instead of starting from scratch.
    ///
    /// IMPORTANT: This must be role-scoped to prevent cross-task contamination
    /// (e.g., a developer continuation prompt overriding an analysis prompt).
    #[serde(default)]
    pub rate_limit_continuation_prompt: Option<RateLimitContinuationPrompt>,
    /// Session ID from the last agent response.
    ///
    /// Used for XSD retry to continue with the same session when possible.
    /// Agents that support sessions (e.g., Claude Code) emit session IDs
    /// that can be passed back for continuation.
    #[serde(default)]
    pub last_session_id: Option<String>,
}

/// Role-scoped continuation prompt captured from a rate limit (429).
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub struct RateLimitContinuationPrompt {
    pub drain: AgentDrain,
    pub role: AgentRole,
    pub prompt: String,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum RateLimitContinuationPromptRepr {
    LegacyString(String),
    Structured {
        #[serde(rename = "role")]
        _role: AgentRole,
        #[serde(default)]
        drain: Option<AgentDrain>,
        prompt: String,
    },
}

fn infer_legacy_current_drain(
    current_drain: Option<AgentDrain>,
    current_role: Option<AgentRole>,
    current_mode: DrainMode,
    continuation_prompt: Option<&RateLimitContinuationPromptRepr>,
) -> AgentDrain {
    if let Some(current_drain) = current_drain {
        return current_drain;
    }

    if let Some(prompt_drain) = continuation_prompt.and_then(|prompt| match prompt {
        RateLimitContinuationPromptRepr::LegacyString(_) => None,
        RateLimitContinuationPromptRepr::Structured { drain, .. } => *drain,
    }) {
        return prompt_drain;
    }

    match (current_role, current_mode) {
        (Some(AgentRole::Reviewer), DrainMode::Continuation) => AgentDrain::Fix,
        (Some(AgentRole::Developer), DrainMode::Continuation) => AgentDrain::Development,
        (Some(current_role), _) => AgentDrain::from(current_role),
        (None, _) => default_current_drain(),
    }
}

impl<'de> Deserialize<'de> for AgentChainState {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        struct AgentChainStateSerde {
            agents: Arc<[String]>,
            current_agent_index: usize,
            models_per_agent: Arc<[Vec<String>]>,
            current_model_index: usize,
            retry_cycle: u32,
            max_cycles: u32,
            #[serde(default = "default_retry_delay_ms")]
            retry_delay_ms: u64,
            #[serde(default = "default_backoff_multiplier")]
            backoff_multiplier: f64,
            #[serde(default = "default_max_backoff_ms")]
            max_backoff_ms: u64,
            #[serde(default)]
            backoff_pending_ms: Option<u64>,
            #[serde(default)]
            current_drain: Option<AgentDrain>,
            #[serde(default)]
            current_role: Option<AgentRole>,
            #[serde(default)]
            current_mode: DrainMode,
            #[serde(default)]
            rate_limit_continuation_prompt: Option<RateLimitContinuationPromptRepr>,
            #[serde(default)]
            last_session_id: Option<String>,
        }

        let raw = AgentChainStateSerde::deserialize(deserializer)?;
        let current_drain = infer_legacy_current_drain(
            raw.current_drain,
            raw.current_role,
            raw.current_mode,
            raw.rate_limit_continuation_prompt.as_ref(),
        );
        let current_role = current_drain.role();

        let rate_limit_continuation_prompt = raw.rate_limit_continuation_prompt.map(|repr| {
            match repr {
                RateLimitContinuationPromptRepr::LegacyString(prompt) => {
                    // Legacy checkpoints stored only the prompt string. Scope it to the
                    // resolved drain role so resume can't cross-contaminate drains.
                    RateLimitContinuationPrompt {
                        drain: current_drain,
                        role: current_role,
                        prompt,
                    }
                }
                RateLimitContinuationPromptRepr::Structured {
                    _role: _,
                    drain,
                    prompt,
                } => {
                    let prompt_drain = drain.unwrap_or(current_drain);
                    RateLimitContinuationPrompt {
                        drain: prompt_drain,
                        role: prompt_drain.role(),
                        prompt,
                    }
                }
            }
        });

        Ok(Self {
            agents: raw.agents,
            current_agent_index: raw.current_agent_index,
            models_per_agent: raw.models_per_agent,
            current_model_index: raw.current_model_index,
            retry_cycle: raw.retry_cycle,
            max_cycles: raw.max_cycles,
            retry_delay_ms: raw.retry_delay_ms,
            backoff_multiplier: raw.backoff_multiplier,
            max_backoff_ms: raw.max_backoff_ms,
            backoff_pending_ms: raw.backoff_pending_ms,
            current_role,
            current_drain,
            current_mode: raw.current_mode,
            rate_limit_continuation_prompt,
            last_session_id: raw.last_session_id,
        })
    }
}

const fn default_retry_delay_ms() -> u64 {
    1000
}

const fn default_backoff_multiplier() -> f64 {
    2.0
}

const fn default_max_backoff_ms() -> u64 {
    60000
}

const fn default_current_drain() -> AgentDrain {
    AgentDrain::Planning
}

const fn agent_drain_signature_tag(drain: AgentDrain) -> &'static [u8] {
    match drain {
        AgentDrain::Planning => b"planning\n",
        AgentDrain::Development => b"development\n",
        AgentDrain::Review => b"review\n",
        AgentDrain::Fix => b"fix\n",
        AgentDrain::Commit => b"commit\n",
        AgentDrain::Analysis => b"analysis\n",
    }
}

impl AgentChainState {
    #[must_use]
    pub fn initial() -> Self {
        Self {
            agents: Arc::from(vec![]),
            current_agent_index: 0,
            models_per_agent: Arc::from(vec![]),
            current_model_index: 0,
            retry_cycle: 0,
            max_cycles: 3,
            retry_delay_ms: default_retry_delay_ms(),
            backoff_multiplier: default_backoff_multiplier(),
            max_backoff_ms: default_max_backoff_ms(),
            backoff_pending_ms: None,
            current_role: AgentRole::Developer,
            current_drain: default_current_drain(),
            current_mode: DrainMode::Normal,
            rate_limit_continuation_prompt: None,
            last_session_id: None,
        }
    }

    #[must_use]
    pub fn matches_runtime_drain(&self, runtime_drain: AgentDrain) -> bool {
        self.current_drain == runtime_drain
    }

    #[must_use]
    pub fn with_agents(
        mut self,
        agents: Vec<String>,
        models_per_agent: Vec<Vec<String>>,
        role: AgentRole,
    ) -> Self {
        self.agents = Arc::from(agents);
        self.models_per_agent = Arc::from(models_per_agent);
        self.current_role = role;
        self.current_drain = match role {
            AgentRole::Developer => AgentDrain::Development,
            AgentRole::Reviewer => AgentDrain::Review,
            AgentRole::Commit => AgentDrain::Commit,
            AgentRole::Analysis => AgentDrain::Analysis,
        };
        self.current_mode = DrainMode::Normal;
        self
    }

    #[must_use]
    pub const fn with_drain(mut self, drain: AgentDrain) -> Self {
        self.current_drain = drain;
        self.current_role = drain.role();
        self
    }

    #[must_use]
    pub const fn with_mode(mut self, mode: DrainMode) -> Self {
        self.current_mode = mode;
        self
    }

    #[must_use]
    pub const fn active_role(&self) -> AgentRole {
        self.current_drain.role()
    }

    /// Builder method to set the maximum number of retry cycles.
    ///
    /// A retry cycle is when all agents have been exhausted and we start
    /// over with exponential backoff.
    #[must_use]
    pub const fn with_max_cycles(mut self, max_cycles: u32) -> Self {
        self.max_cycles = max_cycles;
        self
    }

    #[must_use]
    pub const fn with_backoff_policy(
        mut self,
        retry_delay_ms: u64,
        backoff_multiplier: f64,
        max_backoff_ms: u64,
    ) -> Self {
        self.retry_delay_ms = retry_delay_ms;
        self.backoff_multiplier = backoff_multiplier;
        self.max_backoff_ms = max_backoff_ms;
        self
    }

    #[must_use]
    pub fn current_agent(&self) -> Option<&String> {
        self.agents.get(self.current_agent_index)
    }

    /// Stable signature of the current consumer set (agents + configured models + drain).
    ///
    /// This is used to dedupe oversize materialization decisions across reducer retries.
    /// The signature is stable under:
    /// - switching the current agent/model index
    /// - retry cycles
    ///
    /// It changes only when the configured consumer set changes.
    #[must_use]
    pub fn consumer_signature_sha256(&self) -> String {
        let mut pairs: Vec<(&str, &[String])> = self
            .agents
            .iter()
            .enumerate()
            .map(|(idx, agent)| {
                let models: &[String] = self
                    .models_per_agent
                    .get(idx)
                    .map_or([].as_slice(), std::vec::Vec::as_slice);
                (agent.as_str(), models)
            })
            .collect();

        // Sort so the signature is stable even if callers reorder the configured
        // consumer set.
        pairs.sort_by(|(agent_a, models_a), (agent_b, models_b)| {
            use std::cmp::Ordering;

            let agent_ord = agent_a.cmp(agent_b);
            if agent_ord != Ordering::Equal {
                return agent_ord;
            }

            for (a, b) in models_a.iter().zip(models_b.iter()) {
                let ord = a.cmp(b);
                if ord != Ordering::Equal {
                    return ord;
                }
            }

            models_a.len().cmp(&models_b.len())
        });

        let mut hasher = Sha256::new();
        hasher.update(agent_drain_signature_tag(self.current_drain));
        for (agent, models) in pairs {
            hasher.update(agent.as_bytes());
            hasher.update(b"|");
            for (idx, model) in models.iter().enumerate() {
                if idx > 0 {
                    hasher.update(b",");
                }
                hasher.update(model.as_bytes());
            }
            hasher.update(b"\n");
        }
        let digest = hasher.finalize();
        digest.iter().fold(String::new(), |mut s, b| {
            use std::fmt::Write;
            write!(&mut s, "{b:02x}").unwrap();
            s
        })
    }

    #[cfg(test)]
    fn legacy_consumer_signature_sha256_for_test(&self) -> String {
        let mut rendered: Vec<String> = self
            .agents
            .iter()
            .enumerate()
            .map(|(idx, agent)| {
                let models = self
                    .models_per_agent
                    .get(idx)
                    .map_or([].as_slice(), std::vec::Vec::as_slice);
                format!("{}|{}", agent, models.join(","))
            })
            .collect();

        rendered.sort();

        let mut hasher = Sha256::new();
        hasher.update(agent_drain_signature_tag(self.current_drain));
        for line in rendered {
            hasher.update(line.as_bytes());
            hasher.update(b"\n");
        }
        let digest = hasher.finalize();
        digest.iter().fold(String::new(), |mut s, b| {
            use std::fmt::Write;
            write!(&mut s, "{b:02x}").unwrap();
            s
        })
    }

    /// Get the currently selected model for the current agent.
    ///
    /// Returns `None` if:
    /// - No models are configured
    /// - The current agent index is out of bounds
    /// - The current model index is out of bounds
    #[must_use]
    pub fn current_model(&self) -> Option<&String> {
        self.models_per_agent
            .get(self.current_agent_index)
            .and_then(|models| models.get(self.current_model_index))
    }

    #[must_use]
    pub const fn is_exhausted(&self) -> bool {
        self.retry_cycle >= self.max_cycles
            && self.current_agent_index == 0
            && self.current_model_index == 0
    }
}

#[cfg(test)]
mod consumer_signature_tests {
    use super::*;

    #[test]
    fn test_consumer_signature_sorting_matches_legacy_rendered_pair_ordering() {
        // This regression test locks in the pre-optimization signature ordering:
        // sort by the lexicographic ordering of the rendered `agent|models_csv` strings.
        //
        // A length-first models compare changes ordering when the first model differs.
        // Example: "a,z" must sort before "b" even though it is longer.
        let state = AgentChainState::initial().with_agents(
            vec!["agent".to_string(), "agent".to_string()],
            vec![
                vec!["b".to_string()],
                vec!["a".to_string(), "z".to_string()],
            ],
            AgentRole::Developer,
        );

        assert_eq!(
            state.consumer_signature_sha256(),
            state.legacy_consumer_signature_sha256_for_test(),
            "consumer signature ordering must remain stable for the same configured consumers"
        );
    }

    #[test]
    fn test_consumer_signature_uses_stable_drain_encoding() {
        // The consumer signature is persisted in reducer state and used for dedupe.
        // It must not depend on Debug formatting (variant renames would change the hash).
        // Instead, it should use a stable, explicit role tag.
        let state = AgentChainState::initial()
            .with_agents(
                vec!["agent-a".to_string()],
                vec![vec!["m1".to_string(), "m2".to_string()]],
                AgentRole::Reviewer,
            )
            .with_drain(AgentDrain::Fix);

        let mut hasher = Sha256::new();
        hasher.update(b"fix\n");
        hasher.update(b"agent-a");
        hasher.update(b"|");
        hasher.update(b"m1");
        hasher.update(b",");
        hasher.update(b"m2");
        hasher.update(b"\n");
        let expected = hasher.finalize().iter().fold(String::new(), |mut acc, b| {
            use std::fmt::Write;
            write!(acc, "{b:02x}").unwrap();
            acc
        });

        assert_eq!(
            state.consumer_signature_sha256(),
            expected,
            "role encoding must be stable and explicit"
        );
    }
}

#[cfg(test)]
mod legacy_rate_limit_prompt_tests {
    use super::*;

    #[test]
    fn test_legacy_rate_limit_continuation_prompt_uses_current_role_on_deserialize() {
        // Legacy checkpoints stored `rate_limit_continuation_prompt` as a bare string.
        // When resuming, we must scope that prompt to the chain's `current_role`
        // (the role the checkpoint was executing) instead of defaulting to Developer.
        let state = AgentChainState::initial().with_agents(
            vec!["a".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        );

        let mut v = serde_json::to_value(&state).expect("serialize AgentChainState");
        v["rate_limit_continuation_prompt"] = serde_json::Value::String("legacy prompt".into());

        let json = serde_json::to_string(&v).expect("serialize JSON value");
        let decoded: AgentChainState =
            serde_json::from_str(&json).expect("deserialize AgentChainState");

        let prompt = decoded
            .rate_limit_continuation_prompt
            .expect("expected legacy prompt to deserialize");
        assert_eq!(prompt.drain, AgentDrain::Review);
        assert_eq!(prompt.role, AgentRole::Reviewer);
        assert_eq!(prompt.prompt, "legacy prompt");
    }
}
