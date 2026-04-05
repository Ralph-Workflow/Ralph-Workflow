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
    /// Last failure reason from the most recent agent failure.
    ///
    /// Used to provide context in CLI output when a fallback agent is invoked.
    /// Cleared on InvocationSucceeded or ChainInitialized.
    #[serde(default)]
    pub last_failure_reason: Option<String>,
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
            #[serde(default)]
            last_failure_reason: Option<String>,
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
            last_failure_reason: raw.last_failure_reason,
        })
    }
}

impl Default for AgentChainState {
    fn default() -> Self {
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
            last_failure_reason: None,
        }
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
            last_failure_reason: None,
        }
    }

    #[must_use]
    pub fn matches_runtime_drain(&self, runtime_drain: AgentDrain) -> bool {
        self.current_drain == runtime_drain
    }

    #[must_use]
    pub fn with_agents(
        self,
        agents: Vec<String>,
        models_per_agent: Vec<Vec<String>>,
        role: AgentRole,
    ) -> Self {
        let current_drain = match role {
            AgentRole::Developer => AgentDrain::Development,
            AgentRole::Reviewer => AgentDrain::Review,
            AgentRole::Commit => AgentDrain::Commit,
            AgentRole::Analysis => AgentDrain::Analysis,
        };
        Self {
            agents: Arc::from(agents),
            models_per_agent: Arc::from(models_per_agent),
            current_role: role,
            current_drain,
            current_mode: DrainMode::Normal,
            ..self
        }
    }

    #[must_use]
    pub fn with_drain(self, drain: AgentDrain) -> Self {
        Self {
            current_drain: drain,
            current_role: drain.role(),
            ..self
        }
    }

    #[must_use]
    pub fn with_mode(self, mode: DrainMode) -> Self {
        Self {
            current_mode: mode,
            ..self
        }
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
    pub fn with_max_cycles(self, max_cycles: u32) -> Self {
        Self { max_cycles, ..self }
    }

    #[must_use]
    pub fn with_backoff_policy(
        self,
        retry_delay_ms: u64,
        backoff_multiplier: f64,
        max_backoff_ms: u64,
    ) -> Self {
        Self {
            retry_delay_ms,
            backoff_multiplier,
            max_backoff_ms,
            ..self
        }
    }

    #[must_use]
    pub fn with_retry_cycle(self, retry_cycle: u32) -> Self {
        Self {
            retry_cycle,
            ..self
        }
    }

    #[must_use]
    pub fn with_current_agent_index(self, current_agent_index: usize) -> Self {
        Self {
            current_agent_index,
            ..self
        }
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
        use itertools::Itertools;

        let sorted_pairs: Vec<(String, Vec<String>)> = self
            .agents
            .iter()
            .enumerate()
            .map(|(idx, agent)| {
                let models: Vec<String> = self
                    .models_per_agent
                    .get(idx)
                    .map_or_else(Vec::new, |m| m.clone());
                (agent.clone(), models)
            })
            .sorted_by_key(|(agent, models)| (agent.clone(), models.clone()))
            .collect();

        let update_chain: Vec<Vec<u8>> = sorted_pairs
            .iter()
            .map(|(agent, models)| {
                let models_bytes: Vec<u8> = models
                    .iter()
                    .map(|m| m.as_bytes())
                    .collect::<Vec<_>>()
                    .join(&b',');
                let line: Vec<u8> = std::iter::empty()
                    .chain(agent.as_bytes().iter().copied())
                    .chain([b'|'])
                    .chain(models_bytes.iter().copied())
                    .chain([b'\n'])
                    .collect();
                line
            })
            .collect();

        let hasher = update_chain.iter().fold(
            Digest::chain_update(Sha256::new(), agent_drain_signature_tag(self.current_drain)),
            |h, chunk| Digest::chain_update(h, chunk.as_slice()),
        );
        let digest = hasher.finalize();
        digest
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    }

    #[cfg(test)]
    fn legacy_consumer_signature_sha256_for_test(&self) -> String {
        use itertools::Itertools;

        let rendered: Vec<String> = self
            .agents
            .iter()
            .enumerate()
            .map(|(idx, agent)| {
                let models = self
                    .models_per_agent
                    .get(idx)
                    .map_or([].as_slice(), std::vec::Vec::as_slice);
                format!(
                    "{}|{}",
                    agent,
                    models
                        .iter()
                        .map(|s| s.as_str())
                        .collect::<Vec<_>>()
                        .join(",")
                )
            })
            .sorted()
            .collect();

        let update_chain: Vec<&[u8]> = rendered
            .iter()
            .flat_map(|line| [line.as_bytes(), b"\n"])
            .collect();

        let hasher = update_chain.iter().fold(
            Digest::chain_update(Sha256::new(), agent_drain_signature_tag(self.current_drain)),
            |h, chunk| Digest::chain_update(h, *chunk),
        );
        let digest = hasher.finalize();
        digest
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
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
        let state = AgentChainState::initial()
            .with_agents(
                vec!["agent-a".to_string()],
                vec![vec!["m1".to_string(), "m2".to_string()]],
                AgentRole::Reviewer,
            )
            .with_drain(AgentDrain::Fix);

        let data = b"fix\nagent-a|m1,m2\n".to_vec();
        let expected = Sha256::digest(&data)
            .iter()
            .fold(String::new(), |mut acc, b| {
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

    #[test]
    fn test_legacy_checkpoint_infers_drain_from_structured_continuation_prompt() {
        // Checkpoints that have no `current_drain` field but carry a structured
        // continuation prompt with an explicit drain must derive `current_drain`
        // from the prompt's drain field (second branch of infer_legacy_current_drain).
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "rate_limit_continuation_prompt": {
                "role": "Reviewer",
                "drain": "Fix",
                "prompt": "continue here"
            }
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize legacy checkpoint");

        assert_eq!(
            decoded.current_drain,
            AgentDrain::Fix,
            "drain must be inferred from the structured continuation prompt's drain field"
        );
        assert_eq!(decoded.current_role, AgentRole::Reviewer);
        let prompt = decoded
            .rate_limit_continuation_prompt
            .expect("continuation prompt must survive deserialization");
        assert_eq!(prompt.drain, AgentDrain::Fix);
        assert_eq!(prompt.prompt, "continue here");
    }

    #[test]
    fn test_explicit_current_drain_in_checkpoint_used_directly() {
        // When current_drain is present in the JSON, it must be used directly.
        // The current_role and current_mode fields must not override it.
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "current_drain": "Fix",
            "current_role": "Developer",
            "current_mode": "Normal"
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize checkpoint with explicit drain");

        assert_eq!(
            decoded.current_drain,
            AgentDrain::Fix,
            "explicit current_drain must be used directly, ignoring current_role"
        );
        assert_eq!(
            decoded.current_role,
            AgentRole::Reviewer,
            "current_role must be derived from current_drain (Fix -> Reviewer)"
        );
    }

    #[test]
    fn test_legacy_checkpoint_developer_continuation_infers_development_drain() {
        // (Developer, Continuation) must map to Development via role+mode inference.
        // This mirrors the Reviewer case but for the developer side of the chain.
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "current_role": "Developer",
            "current_mode": "Continuation"
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize developer continuation checkpoint");

        assert_eq!(
            decoded.current_drain,
            AgentDrain::Development,
            "(Developer, Continuation) must map to Development via role+mode inference"
        );
        assert_eq!(decoded.current_role, AgentRole::Developer);
    }

    #[test]
    fn test_legacy_checkpoint_developer_normal_mode_infers_development_drain_via_from() {
        // When mode is Normal and role is Developer, drain is inferred via AgentDrain::from(role).
        // AgentDrain::from(Developer) = Development.
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "current_role": "Developer",
            "current_mode": "Normal"
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize developer normal mode checkpoint");

        assert_eq!(
            decoded.current_drain,
            AgentDrain::Development,
            "(Developer, Normal) must infer Development via AgentDrain::from(Developer)"
        );
        assert_eq!(decoded.current_role, AgentRole::Developer);
    }

    #[test]
    fn test_legacy_checkpoint_both_absent_uses_default_planning_drain() {
        // When neither current_drain nor current_role is present, the default drain
        // (AgentDrain::Planning) must be used.
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize checkpoint with no drain or role");

        assert_eq!(
            decoded.current_drain,
            AgentDrain::Planning,
            "when both current_drain and current_role are absent, Planning drain is the default"
        );
        assert_eq!(decoded.current_role, AgentRole::Developer);
    }

    #[test]
    fn test_structured_prompt_without_drain_falls_back_to_current_drain() {
        // A structured continuation prompt with no explicit drain field must use the
        // resolved current_drain as the prompt drain.
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "current_role": "Reviewer",
            "current_mode": "Normal",
            "rate_limit_continuation_prompt": {
                "role": "Reviewer",
                "prompt": "continue the review"
            }
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize structured prompt without drain");

        // current_role=Reviewer + mode=Normal → AgentDrain::from(Reviewer) = Review
        assert_eq!(
            decoded.current_drain,
            AgentDrain::Review,
            "Reviewer+Normal must resolve to Review via AgentDrain::from"
        );
        let prompt = decoded
            .rate_limit_continuation_prompt
            .expect("structured prompt must survive deserialization");
        assert_eq!(
            prompt.drain,
            AgentDrain::Review,
            "prompt drain must fall back to resolved current_drain when absent from JSON"
        );
        assert_eq!(prompt.role, AgentRole::Reviewer);
    }

    #[test]
    fn test_structured_prompt_drain_takes_priority_over_role_mode_inference() {
        // When both current_role+mode and a structured prompt drain are present (but no
        // explicit current_drain), the prompt drain wins over the role+mode mapping.
        // This is the second branch of infer_legacy_current_drain.
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "current_role": "Developer",
            "current_mode": "Continuation",
            "rate_limit_continuation_prompt": {
                "role": "Reviewer",
                "drain": "Fix",
                "prompt": "fix the issues"
            }
        });

        let decoded: AgentChainState = serde_json::from_value(json)
            .expect("deserialize checkpoint with prompt drain vs role+mode conflict");

        // Without prompt drain: (Developer, Continuation) → Development
        // With prompt drain Fix: Fix wins
        assert_eq!(
            decoded.current_drain,
            AgentDrain::Fix,
            "structured prompt drain (Fix) must take priority over role+mode inference (Development)"
        );
        let prompt = decoded
            .rate_limit_continuation_prompt
            .expect("continuation prompt must survive deserialization");
        assert_eq!(prompt.drain, AgentDrain::Fix);
        assert_eq!(prompt.prompt, "fix the issues");
    }

    #[test]
    fn test_legacy_checkpoint_reviewer_normal_mode_infers_review_drain() {
        // (Reviewer, Normal) must map to AgentDrain::Review via AgentDrain::from(Reviewer).
        // This is the case where a reviewer is in Normal mode (not Continuation/Fix).
        // Importantly, no rate_limit_continuation_prompt is present, so the only
        // inference path is the role+mode fallback branch.
        let json = serde_json::json!({
            "agents": ["codex"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "current_role": "Reviewer",
            "current_mode": "Normal"
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize reviewer normal mode checkpoint");

        assert_eq!(
            decoded.current_drain,
            AgentDrain::Review,
            "(Reviewer, Normal) without explicit drain must infer Review via AgentDrain::from(Reviewer)"
        );
        assert_eq!(decoded.current_role, AgentRole::Reviewer);
        assert!(
            decoded.rate_limit_continuation_prompt.is_none(),
            "no continuation prompt expected for normal-mode reviewer checkpoint"
        );
    }

    #[test]
    fn test_legacy_checkpoint_infers_drain_from_role_and_mode_when_no_drain_or_structured_prompt() {
        // Checkpoints with no `current_drain`, a bare-string continuation prompt, and
        // current_role=Reviewer + current_mode=Continuation must resolve current_drain
        // to Fix via the role+mode mapping (third branch of infer_legacy_current_drain).
        let json = serde_json::json!({
            "agents": ["a"],
            "current_agent_index": 0,
            "models_per_agent": [[]],
            "current_model_index": 0,
            "retry_cycle": 0,
            "max_cycles": 3,
            "current_role": "Reviewer",
            "current_mode": "Continuation",
            "rate_limit_continuation_prompt": "legacy fix prompt"
        });

        let decoded: AgentChainState =
            serde_json::from_value(json).expect("deserialize legacy checkpoint");

        assert_eq!(
            decoded.current_drain,
            AgentDrain::Fix,
            "(Reviewer, Continuation) must map to Fix via role+mode inference"
        );
        assert_eq!(decoded.current_role, AgentRole::Reviewer);
        let prompt = decoded
            .rate_limit_continuation_prompt
            .expect("legacy string prompt must survive deserialization");
        assert_eq!(prompt.drain, AgentDrain::Fix);
        assert_eq!(prompt.role, AgentRole::Reviewer);
        assert_eq!(prompt.prompt, "legacy fix prompt");
    }
}
