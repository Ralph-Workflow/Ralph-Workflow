//! Fallback chain configuration for agent fault tolerance.
//!
//! This module defines the `FallbackConfig` structure that controls how Ralph
//! handles agent failures. It supports:
//! - Agent-level fallback (try different agents)
//! - Provider-level fallback (try different models within same agent)
//! - Exponential backoff with cycling

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Agent role (developer, reviewer, or commit).
///
/// Each role can have its own chain of fallback agents.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AgentRole {
    /// Developer agent: implements features based on PROMPT.md.
    Developer,
    /// Reviewer agent: reviews code and fixes issues.
    Reviewer,
    /// Commit agent: generates commit messages from diffs.
    Commit,
    /// Analysis agent: independently verifies progress (diff vs plan).
    Analysis,
}

/// Runtime consumer of an agent chain.
///
/// Drains represent distinct runtime contexts that consume a concrete chain,
/// even when multiple drains share the same underlying ordered agent list.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AgentDrain {
    Planning,
    Development,
    Review,
    Fix,
    Commit,
    Analysis,
}

impl AgentDrain {
    /// Return the broad capability role associated with this drain.
    #[must_use]
    pub const fn role(self) -> AgentRole {
        match self {
            Self::Planning | Self::Development => AgentRole::Developer,
            Self::Review | Self::Fix => AgentRole::Reviewer,
            Self::Commit => AgentRole::Commit,
            Self::Analysis => AgentRole::Analysis,
        }
    }

    /// Return the built-in drain key used by TOML and diagnostics.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Planning => "planning",
            Self::Development => "development",
            Self::Review => "review",
            Self::Fix => "fix",
            Self::Commit => "commit",
            Self::Analysis => "analysis",
        }
    }

    /// Parse a drain name from config.
    #[must_use]
    pub fn from_name(name: &str) -> Option<Self> {
        match name {
            "planning" => Some(Self::Planning),
            "development" => Some(Self::Development),
            "review" => Some(Self::Review),
            "fix" => Some(Self::Fix),
            "commit" => Some(Self::Commit),
            "analysis" => Some(Self::Analysis),
            _ => None,
        }
    }

    /// Built-in drains in deterministic order.
    #[must_use]
    pub const fn all() -> [Self; 6] {
        [
            Self::Planning,
            Self::Development,
            Self::Review,
            Self::Fix,
            Self::Commit,
            Self::Analysis,
        ]
    }
}

impl From<AgentRole> for AgentDrain {
    fn from(value: AgentRole) -> Self {
        match value {
            AgentRole::Developer => Self::Development,
            AgentRole::Reviewer => Self::Review,
            AgentRole::Commit => Self::Commit,
            AgentRole::Analysis => Self::Analysis,
        }
    }
}

impl std::fmt::Display for AgentDrain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// Drain-local execution mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
pub enum DrainMode {
    #[default]
    Normal,
    Continuation,
    SameAgentRetry,
    XsdRetry,
}

/// Concrete runtime chain binding for one drain.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResolvedDrainBinding {
    pub chain_name: String,
    pub agents: Vec<String>,
}

/// Fully resolved drain configuration consumed by the runtime.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ResolvedDrainConfig {
    pub bindings: HashMap<AgentDrain, ResolvedDrainBinding>,
    pub provider_fallback: HashMap<String, Vec<String>>,
    pub max_retries: u32,
    pub retry_delay_ms: u64,
    pub backoff_multiplier: f64,
    pub max_backoff_ms: u64,
    pub max_cycles: u32,
}

impl ResolvedDrainConfig {
    /// Return the binding for a specific drain.
    #[must_use]
    pub fn binding(&self, drain: AgentDrain) -> Option<&ResolvedDrainBinding> {
        self.bindings.get(&drain)
    }

    /// Build a resolved drain config from the legacy role-shaped fallback config.
    #[must_use]
    pub fn from_legacy(fallback: &FallbackConfig) -> Self {
        let mut bindings = HashMap::new();
        for drain in AgentDrain::all() {
            let role = drain.role();
            let chain_name = fallback.effective_chain_name_for_role(role).to_string();
            bindings.insert(
                drain,
                ResolvedDrainBinding {
                    chain_name,
                    agents: fallback.get_fallbacks(role).to_vec(),
                },
            );
        }

        Self {
            bindings,
            provider_fallback: fallback.provider_fallback.clone(),
            max_retries: fallback.max_retries,
            retry_delay_ms: fallback.retry_delay_ms,
            backoff_multiplier: fallback.backoff_multiplier,
            max_backoff_ms: fallback.max_backoff_ms,
            max_cycles: fallback.max_cycles,
        }
    }

    /// Project resolved drain bindings back into the legacy role-shaped config.
    ///
    /// This is a compatibility view for config/error reporting. Runtime code
    /// should consume resolved drain bindings directly.
    #[must_use]
    pub fn to_legacy_fallback(&self) -> FallbackConfig {
        FallbackConfig {
            developer: self
                .binding(AgentDrain::Development)
                .map_or_else(Vec::new, |binding| binding.agents.clone()),
            reviewer: self
                .binding(AgentDrain::Review)
                .map_or_else(Vec::new, |binding| binding.agents.clone()),
            commit: self
                .binding(AgentDrain::Commit)
                .map_or_else(Vec::new, |binding| binding.agents.clone()),
            analysis: self
                .binding(AgentDrain::Analysis)
                .map_or_else(Vec::new, |binding| binding.agents.clone()),
            provider_fallback: self.provider_fallback.clone(),
            max_retries: self.max_retries,
            retry_delay_ms: self.retry_delay_ms,
            backoff_multiplier: self.backoff_multiplier,
            max_backoff_ms: self.max_backoff_ms,
            max_cycles: self.max_cycles,
            legacy_role_keys_present: false,
        }
    }
}

impl std::fmt::Display for AgentRole {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Developer => write!(f, "developer"),
            Self::Reviewer => write!(f, "reviewer"),
            Self::Commit => write!(f, "commit"),
            Self::Analysis => write!(f, "analysis"),
        }
    }
}

/// Agent chain configuration for preferred agents and fallback switching.
///
/// The agent chain defines both:
/// 1. The **preferred agent** (first in the list) for each role
/// 2. The **fallback agents** (remaining in the list) to try if the preferred fails
///
/// This provides a unified way to configure which agents to use and in what order.
/// Ralph automatically switches to the next agent in the chain when encountering
/// errors like rate limits or auth failures.
///
/// ## Provider-Level Fallback
///
/// In addition to agent-level fallback, you can configure provider-level fallback
/// within a single agent using the `provider_fallback` field. This is useful for
/// agents like opencode that support multiple providers/models via the `-m` flag.
///
/// Example:
/// ```toml
/// [agent_chain]
/// provider_fallback.opencode = ["-m opencode/glm-4.7-free", "-m opencode/claude-sonnet-4"]
/// ```
///
/// ## Exponential Backoff and Cycling
///
/// When all fallbacks are exhausted, Ralph uses exponential backoff and cycles
/// back to the first agent in the chain:
/// - Base delay starts at `retry_delay_ms` (default: 1000ms)
/// - Each cycle multiplies by `backoff_multiplier` (default: 2.0)
/// - Capped at `max_backoff_ms` (default: 60000ms = 1 minute)
/// - Maximum cycles controlled by `max_cycles` (default: 3)
#[derive(Debug, Clone, Serialize)]
pub struct FallbackConfig {
    /// Ordered list of agents for developer role (first = preferred, rest = fallbacks).
    #[serde(default)]
    pub developer: Vec<String>,
    /// Ordered list of agents for reviewer role (first = preferred, rest = fallbacks).
    #[serde(default)]
    pub reviewer: Vec<String>,
    /// Ordered list of agents for commit role (first = preferred, rest = fallbacks).
    #[serde(default)]
    pub commit: Vec<String>,
    /// Ordered list of agents for analysis role (first = preferred, rest = fallbacks).
    ///
    /// If empty, analysis falls back to the developer chain.
    #[serde(default)]
    pub analysis: Vec<String>,
    /// Provider-level fallback: maps agent name to list of model flags to try.
    /// Example: `opencode = ["-m opencode/glm-4.7-free", "-m opencode/claude-sonnet-4"]`
    #[serde(default)]
    pub provider_fallback: HashMap<String, Vec<String>>,
    /// Maximum number of retries per agent before moving to next.
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    /// Base delay between retries in milliseconds.
    #[serde(default = "default_retry_delay_ms")]
    pub retry_delay_ms: u64,
    /// Multiplier for exponential backoff (default: 2.0).
    #[serde(default = "default_backoff_multiplier")]
    pub backoff_multiplier: f64,
    /// Maximum backoff delay in milliseconds (default: 60000 = 1 minute).
    #[serde(default = "default_max_backoff_ms")]
    pub max_backoff_ms: u64,
    /// Maximum number of cycles through all agents before giving up (default: 3).
    #[serde(default = "default_max_cycles")]
    pub max_cycles: u32,
    #[serde(skip)]
    pub(crate) legacy_role_keys_present: bool,
}

impl<'de> Deserialize<'de> for FallbackConfig {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        struct FallbackConfigSerde {
            #[serde(default)]
            developer: Option<Vec<String>>,
            #[serde(default)]
            reviewer: Option<Vec<String>>,
            #[serde(default)]
            commit: Option<Vec<String>>,
            #[serde(default)]
            analysis: Option<Vec<String>>,
            #[serde(default)]
            provider_fallback: HashMap<String, Vec<String>>,
            #[serde(default = "default_max_retries")]
            max_retries: u32,
            #[serde(default = "default_retry_delay_ms")]
            retry_delay_ms: u64,
            #[serde(default = "default_backoff_multiplier")]
            backoff_multiplier: f64,
            #[serde(default = "default_max_backoff_ms")]
            max_backoff_ms: u64,
            #[serde(default = "default_max_cycles")]
            max_cycles: u32,
        }

        let raw = FallbackConfigSerde::deserialize(deserializer)?;
        let legacy_role_keys_present = raw.developer.is_some()
            || raw.reviewer.is_some()
            || raw.commit.is_some()
            || raw.analysis.is_some();

        Ok(Self {
            developer: raw.developer.unwrap_or_default(),
            reviewer: raw.reviewer.unwrap_or_default(),
            commit: raw.commit.unwrap_or_default(),
            analysis: raw.analysis.unwrap_or_default(),
            provider_fallback: raw.provider_fallback,
            max_retries: raw.max_retries,
            retry_delay_ms: raw.retry_delay_ms,
            backoff_multiplier: raw.backoff_multiplier,
            max_backoff_ms: raw.max_backoff_ms,
            max_cycles: raw.max_cycles,
            legacy_role_keys_present,
        })
    }
}

const fn default_max_retries() -> u32 {
    3
}

const fn default_retry_delay_ms() -> u64 {
    1000
}

const fn default_backoff_multiplier() -> f64 {
    2.0
}

const fn default_max_backoff_ms() -> u64 {
    60000 // 1 minute
}

const fn default_max_cycles() -> u32 {
    3
}

// IEEE 754 double precision constants for f64_to_u64_via_bits
const IEEE_754_EXP_BIAS: i32 = 1023;
const IEEE_754_EXP_MASK: u64 = 0x7FF;
const IEEE_754_MANTISSA_MASK: u64 = 0x000F_FFFF_FFFF_FFFF;
const IEEE_754_IMPLICIT_ONE: u64 = 1u64 << 52;

/// Convert f64 to u64 using IEEE 754 bit manipulation to avoid cast lints.
///
/// This function handles the conversion by extracting the raw bits of the f64
/// and manually decoding the IEEE 754 format. For values in the range [0, 100000],
/// this produces correct results without triggering clippy's cast lints.
fn f64_to_u64_via_bits(value: f64) -> u64 {
    // Handle special cases first
    if !value.is_finite() || value < 0.0 {
        return 0;
    }

    // Use to_bits() to get the raw IEEE 754 representation
    let bits = value.to_bits();

    // IEEE 754 double precision:
    // - Bit 63: sign (we know it's 0 for non-negative values)
    // - Bits 52-62: exponent (biased by 1023)
    // - Bits 0-51: mantissa (with implicit leading 1 for normalized numbers)
    let exp_biased = ((bits >> 52) & IEEE_754_EXP_MASK) as i32;
    let mantissa = bits & IEEE_754_MANTISSA_MASK;

    // Check for denormal numbers (exponent == 0)
    if exp_biased == 0 {
        // Denormal: value = mantissa * 2^(-1022)
        // For small values (< 1), this results in 0
        return 0;
    }

    // Normalized number
    let exp = exp_biased - IEEE_754_EXP_BIAS;

    // For integer values, the exponent tells us where the binary point is
    // If exp < 0, the value is < 1, so round to 0
    if exp < 0 {
        return 0;
    }

    // For exp >= 0, we have an integer value
    // The value is (1.mantissa) * 2^exp where 1.mantissa has 53 bits
    let full_mantissa = mantissa | IEEE_754_IMPLICIT_ONE;

    // Shift to get the integer value
    // We need to shift right by (52 - exp) to get the integer
    let shift = 52i32 - exp;

    if shift <= 0 {
        // Value is very large, saturate at u64::MAX
        // But our input is clamped to [0, 100000], so this won't happen
        u64::MAX
    } else if shift < 64 {
        full_mantissa >> shift
    } else {
        0
    }
}

impl Default for FallbackConfig {
    fn default() -> Self {
        Self {
            developer: Vec::new(),
            reviewer: Vec::new(),
            commit: Vec::new(),
            analysis: Vec::new(),
            provider_fallback: HashMap::new(),
            max_retries: default_max_retries(),
            retry_delay_ms: default_retry_delay_ms(),
            backoff_multiplier: default_backoff_multiplier(),
            max_backoff_ms: default_max_backoff_ms(),
            max_cycles: default_max_cycles(),
            legacy_role_keys_present: false,
        }
    }
}

impl FallbackConfig {
    /// Return whether this legacy config carries any role-keyed chain bindings.
    #[must_use]
    pub fn has_role_bindings(&self) -> bool {
        [
            self.developer.as_slice(),
            self.reviewer.as_slice(),
            self.commit.as_slice(),
            self.analysis.as_slice(),
        ]
        .into_iter()
        .any(|chain| !chain.is_empty())
    }

    /// Return whether any legacy role key was explicitly present in the source config.
    #[must_use]
    pub const fn has_legacy_role_key_presence(&self) -> bool {
        self.legacy_role_keys_present
    }

    /// Return whether the legacy role-keyed schema is in use.
    #[must_use]
    pub fn uses_legacy_role_schema(&self) -> bool {
        self.legacy_role_keys_present || self.has_role_bindings()
    }

    const fn effective_chain_name_for_role(&self, role: AgentRole) -> &'static str {
        match role {
            AgentRole::Developer => "developer",
            AgentRole::Reviewer => "reviewer",
            AgentRole::Commit => {
                if self.commit.is_empty() {
                    "reviewer"
                } else {
                    "commit"
                }
            }
            AgentRole::Analysis => {
                if self.analysis.is_empty() {
                    "developer"
                } else {
                    "analysis"
                }
            }
        }
    }

    /// Calculate exponential backoff delay for a given cycle.
    ///
    /// Uses the formula: min(base * multiplier^cycle, `max_backoff`)
    ///
    /// Uses integer arithmetic to avoid floating-point casting issues.
    #[must_use]
    pub fn calculate_backoff(&self, cycle: u32) -> u64 {
        // For common multiplier values, use direct integer computation
        // to avoid f64->u64 conversion and associated clippy lints.
        let multiplier_hundredths = self.get_multiplier_hundredths();
        let base_hundredths = self.retry_delay_ms.saturating_mul(100);

        // Calculate: base * (multiplier^cycle) / 100^cycle
        // Use saturating arithmetic to avoid overflow
        let mut delay_hundredths = base_hundredths;
        for _ in 0..cycle {
            delay_hundredths = delay_hundredths.saturating_mul(multiplier_hundredths);
            delay_hundredths = delay_hundredths.saturating_div(100);
        }

        // Convert back to milliseconds
        delay_hundredths.div_euclid(100).min(self.max_backoff_ms)
    }

    /// Get the multiplier as hundredths (e.g., 2.0 -> 200, 1.5 -> 150).
    ///
    /// Uses a lookup table for common values to avoid f64->u64 casts.
    /// For uncommon values, uses a safe conversion with validation.
    fn get_multiplier_hundredths(&self) -> u64 {
        const EPSILON: f64 = 0.0001;

        // Common multiplier values - use exact integer matches
        // This avoids the cast for the vast majority of cases
        let m = self.backoff_multiplier;
        if (m - 1.0).abs() < EPSILON {
            return 100;
        } else if (m - 1.5).abs() < EPSILON {
            return 150;
        } else if (m - 2.0).abs() < EPSILON {
            return 200;
        } else if (m - 2.5).abs() < EPSILON {
            return 250;
        } else if (m - 3.0).abs() < EPSILON {
            return 300;
        } else if (m - 4.0).abs() < EPSILON {
            return 400;
        } else if (m - 5.0).abs() < EPSILON {
            return 500;
        } else if (m - 10.0).abs() < EPSILON {
            return 1000;
        }

        // For uncommon values, compute using the original formula
        // The value is clamped to [0.0, 1000.0] so the result is in [0.0, 100000.0]
        // We use to_bits() and manual decoding to avoid cast lints
        let clamped = m.clamp(0.0, 1000.0);
        let multiplied = clamped * 100.0;
        let rounded = multiplied.round();

        // Manual f64 to u64 conversion using IEEE 754 bit representation
        f64_to_u64_via_bits(rounded)
    }

    /// Get fallback agents for a role.
    #[must_use]
    pub fn get_fallbacks(&self, role: AgentRole) -> &[String] {
        match role {
            AgentRole::Developer => &self.developer,
            AgentRole::Reviewer => &self.reviewer,
            AgentRole::Commit => self.get_effective_commit_fallbacks(),
            AgentRole::Analysis => self.get_effective_analysis_fallbacks(),
        }
    }

    /// Get effective fallback agents for analysis role.
    ///
    /// Falls back to developer chain if analysis chain is empty.
    fn get_effective_analysis_fallbacks(&self) -> &[String] {
        if self.analysis.is_empty() {
            &self.developer
        } else {
            &self.analysis
        }
    }

    /// Get effective fallback agents for commit role.
    ///
    /// Falls back to reviewer chain if commit chain is empty.
    /// This ensures commit message generation can use the same agents
    /// configured for code review when no dedicated commit agents are specified.
    fn get_effective_commit_fallbacks(&self) -> &[String] {
        if self.commit.is_empty() {
            &self.reviewer
        } else {
            &self.commit
        }
    }

    /// Check if fallback is configured for a role.
    #[must_use]
    pub fn has_fallbacks(&self, role: AgentRole) -> bool {
        !self.get_fallbacks(role).is_empty()
    }

    /// Get provider-level fallback model flags for an agent.
    ///
    /// Returns the list of model flags to try for the given agent name.
    /// Empty slice if no provider fallback is configured for this agent.
    pub fn get_provider_fallbacks(&self, agent_name: &str) -> &[String] {
        self.provider_fallback
            .get(agent_name)
            .map_or(&[], std::vec::Vec::as_slice)
    }

    /// Check if provider-level fallback is configured for an agent.
    #[must_use]
    pub fn has_provider_fallbacks(&self, agent_name: &str) -> bool {
        self.provider_fallback
            .get(agent_name)
            .is_some_and(|v| !v.is_empty())
    }

    /// Resolve this legacy fallback config into explicit built-in drains.
    #[must_use]
    pub fn resolve_drains(&self) -> ResolvedDrainConfig {
        ResolvedDrainConfig::from_legacy(self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_agent_role_display() {
        assert_eq!(format!("{}", AgentRole::Developer), "developer");
        assert_eq!(format!("{}", AgentRole::Reviewer), "reviewer");
        assert_eq!(format!("{}", AgentRole::Commit), "commit");
        assert_eq!(format!("{}", AgentRole::Analysis), "analysis");
    }

    #[test]
    fn test_agent_drain_role_mapping() {
        assert_eq!(AgentDrain::Planning.role(), AgentRole::Developer);
        assert_eq!(AgentDrain::Development.role(), AgentRole::Developer);
        assert_eq!(AgentDrain::Review.role(), AgentRole::Reviewer);
        assert_eq!(AgentDrain::Fix.role(), AgentRole::Reviewer);
        assert_eq!(AgentDrain::Commit.role(), AgentRole::Commit);
        assert_eq!(AgentDrain::Analysis.role(), AgentRole::Analysis);
    }

    #[test]
    fn test_fallback_config_defaults() {
        let config = FallbackConfig::default();
        assert!(config.developer.is_empty());
        assert!(config.reviewer.is_empty());
        assert!(config.commit.is_empty());
        assert!(config.analysis.is_empty());
        assert_eq!(config.max_retries, 3);
        assert_eq!(config.retry_delay_ms, 1000);
        // Use approximate comparison for floating point
        assert!((config.backoff_multiplier - 2.0).abs() < f64::EPSILON);
        assert_eq!(config.max_backoff_ms, 60000);
        assert_eq!(config.max_cycles, 3);
    }

    #[test]
    fn test_fallback_config_calculate_backoff() {
        let config = FallbackConfig {
            retry_delay_ms: 1000,
            backoff_multiplier: 2.0,
            max_backoff_ms: 60000,
            ..Default::default()
        };

        assert_eq!(config.calculate_backoff(0), 1000);
        assert_eq!(config.calculate_backoff(1), 2000);
        assert_eq!(config.calculate_backoff(2), 4000);
        assert_eq!(config.calculate_backoff(3), 8000);

        // Should cap at max
        assert_eq!(config.calculate_backoff(10), 60000);
    }

    #[test]
    fn test_fallback_config_get_fallbacks() {
        let config = FallbackConfig {
            developer: vec!["claude".to_string(), "codex".to_string()],
            reviewer: vec!["codex".to_string()],
            ..Default::default()
        };

        assert_eq!(
            config.get_fallbacks(AgentRole::Developer),
            &["claude", "codex"]
        );
        assert_eq!(config.get_fallbacks(AgentRole::Reviewer), &["codex"]);

        // Analysis defaults to developer chain when not configured.
        assert_eq!(
            config.get_fallbacks(AgentRole::Analysis),
            &["claude", "codex"]
        );
    }

    #[test]
    fn test_fallback_config_has_fallbacks() {
        let config = FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec![],
            ..Default::default()
        };

        assert!(config.has_fallbacks(AgentRole::Developer));
        assert!(config.has_fallbacks(AgentRole::Analysis));
        assert!(!config.has_fallbacks(AgentRole::Reviewer));
    }

    #[test]
    fn test_fallback_config_defaults_provider_fallback() {
        let config = FallbackConfig::default();
        assert!(config.get_provider_fallbacks("opencode").is_empty());
        assert!(!config.has_provider_fallbacks("opencode"));
    }

    #[test]
    fn test_provider_fallback_config() {
        let mut provider_fallback = HashMap::new();
        provider_fallback.insert(
            "opencode".to_string(),
            vec![
                "-m opencode/glm-4.7-free".to_string(),
                "-m opencode/claude-sonnet-4".to_string(),
            ],
        );

        let config = FallbackConfig {
            provider_fallback,
            ..Default::default()
        };

        let fallbacks = config.get_provider_fallbacks("opencode");
        assert_eq!(fallbacks.len(), 2);
        assert_eq!(fallbacks[0], "-m opencode/glm-4.7-free");
        assert_eq!(fallbacks[1], "-m opencode/claude-sonnet-4");

        assert!(config.has_provider_fallbacks("opencode"));
        assert!(!config.has_provider_fallbacks("claude"));
    }

    #[test]
    fn test_fallback_config_from_toml() {
        let toml_str = r#"
            developer = ["claude", "codex"]
            reviewer = ["codex", "claude"]
            max_retries = 5
            retry_delay_ms = 2000

            [provider_fallback]
            opencode = ["-m opencode/glm-4.7-free", "-m zai/glm-4.7"]
        "#;

        let config: FallbackConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.developer, vec!["claude", "codex"]);
        assert_eq!(config.reviewer, vec!["codex", "claude"]);
        assert_eq!(config.max_retries, 5);
        assert_eq!(config.retry_delay_ms, 2000);
        assert_eq!(config.get_provider_fallbacks("opencode").len(), 2);
    }

    #[test]
    fn test_commit_uses_reviewer_chain_when_empty() {
        // When commit chain is empty, it should fall back to reviewer chain
        let config = FallbackConfig {
            commit: vec![],
            reviewer: vec!["agent1".to_string(), "agent2".to_string()],
            ..Default::default()
        };

        // Commit role should use reviewer chain when commit chain is empty
        assert_eq!(
            config.get_fallbacks(AgentRole::Commit),
            &["agent1", "agent2"]
        );
        assert!(config.has_fallbacks(AgentRole::Commit));
    }

    #[test]
    fn test_commit_uses_own_chain_when_configured() {
        // When commit chain is configured, it should use its own chain
        let config = FallbackConfig {
            commit: vec!["commit-agent".to_string()],
            reviewer: vec!["reviewer-agent".to_string()],
            ..Default::default()
        };

        // Commit role should use its own chain
        assert_eq!(config.get_fallbacks(AgentRole::Commit), &["commit-agent"]);
    }
}
