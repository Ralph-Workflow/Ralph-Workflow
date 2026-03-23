use crate::agents::ccs_env::load_ccs_env_vars;
use crate::agents::parser::JsonParserType;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

fn is_stream_json_output(output_flag: &str) -> bool {
    let output_flag = output_flag.trim();
    if output_flag.is_empty() {
        return false;
    }

    // Common patterns:
    // - "--output-format=stream-json" (Claude/CCS)
    // - "--output-format stream-json" (space-separated)
    // - "--output-format=stream-json" embedded in a longer string
    if output_flag.contains("stream-json") {
        return true;
    }

    // Fallback for other potential spellings: allow "stream_json".
    output_flag.contains("stream_json")
}

/// Default agents.toml template embedded at compile time.
pub const DEFAULT_AGENTS_TOML: &str = include_str!("../../../examples/agents.toml");

/// Config source for tracking where config was loaded from.
#[derive(Debug, Clone)]
pub struct ConfigSource {
    pub path: PathBuf,
    pub agents_loaded: usize,
}

/// Agent capabilities.
#[derive(Debug, Clone)]
pub struct AgentConfig {
    /// Base command to run the agent.
    pub cmd: String,
    /// Output-format flag (JSON streaming, text mode, etc.).
    pub output_flag: String,
    /// Flag for autonomous mode (no prompts).
    pub yolo_flag: String,
    /// Flag for verbose output.
    pub verbose_flag: String,
    /// Whether the agent can run git commit.
    pub can_commit: bool,
    /// Which JSON parser to use for this agent's output.
    pub json_parser: JsonParserType,
    /// Model/provider flag for agents that support model selection.
    pub model_flag: Option<String>,
    /// Print/non-interactive mode flag (e.g., "-p" or "--print" for Claude).
    pub print_flag: String,
    /// Include partial messages flag for streaming in print mode (e.g., "--include-partial-messages").
    /// Required for Claude output streaming when using `--output-format=stream-json` with print mode.
    pub streaming_flag: String,
    /// Session continuation flag template (e.g., "--session {}" for `OpenCode`).
    /// The `{}` placeholder is replaced with the session ID at runtime.
    /// If empty, session continuation is not supported for this agent.
    pub session_flag: String,
    /// Environment variables to set when running this agent.
    /// Used for providers that need env vars (e.g., loaded from CCS settings).
    pub env_vars: HashMap<String, String>,
    /// Display name for UI/logging (e.g., "ccs-glm" instead of raw agent name).
    /// If None, the agent name from the registry is used.
    pub display_name: Option<String>,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            cmd: String::new(),
            output_flag: String::new(),
            yolo_flag: String::new(),
            verbose_flag: String::new(),
            can_commit: true,
            json_parser: JsonParserType::Generic,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: String::new(),
            session_flag: String::new(),
            env_vars: HashMap::new(),
            display_name: None,
        }
    }
}

/// Warning that can occur during CCS environment variable loading.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CcsEnvWarning {
    /// Failed to load environment variables for the specified CCS profile.
    LoadFailed { profile: String, error: String },
}

impl AgentConfig {
    /// Create a new `AgentConfig` builder.
    #[must_use]
    pub fn builder() -> AgentConfigBuilder {
        AgentConfigBuilder::default()
    }

    /// Build full command string with specified flags.
    #[must_use]
    pub fn build_cmd(&self, output: bool, yolo: bool, verbose: bool) -> String {
        self.build_cmd_with_model(output, yolo, verbose, None)
    }

    /// Build full command string with specified flags and optional model override.
    #[must_use]
    pub fn build_cmd_with_model(
        &self,
        output: bool,
        yolo: bool,
        verbose: bool,
        model_override: Option<&str>,
    ) -> String {
        // Build parts using functional style - filter_map for conditional inclusion
        let parts: Vec<String> = [
            Some(self.cmd.clone()),
            (!self.print_flag.is_empty()).then_some(self.print_flag.clone()),
            (output && !self.output_flag.is_empty()).then_some(self.output_flag.clone()),
            (output
                && is_stream_json_output(&self.output_flag)
                && !self.print_flag.is_empty()
                && !self.streaming_flag.is_empty())
            .then_some(self.streaming_flag.clone()),
            (yolo && !self.yolo_flag.is_empty()).then_some(self.yolo_flag.clone()),
            (verbose || self.requires_verbose_for_json(output) && !self.verbose_flag.is_empty())
                .then_some(self.verbose_flag.clone()),
            model_override
                .or(self.model_flag.as_deref())
                .filter(|m| !m.is_empty())
                .map(|m| m.to_string()),
        ]
        .into_iter()
        .flatten()
        .collect();

        parts.join(" ")
    }

    /// Build full command string with session continuation.
    ///
    /// This is used for XSD retries where we want to continue an existing session
    /// so the AI retains memory of its previous reasoning.
    #[must_use]
    pub fn build_cmd_with_session(
        &self,
        output: bool,
        yolo: bool,
        verbose: bool,
        model_override: Option<&str>,
        session_id: Option<&str>,
    ) -> String {
        let base_cmd = self.build_cmd_with_model(output, yolo, verbose, model_override);

        // Add session continuation flag if we have a session ID and the agent supports it
        session_id
            .filter(|_| !self.session_flag.is_empty())
            .map(|sid| {
                let session_arg = self.session_flag.replace("{}", sid);
                format!("{base_cmd} {session_arg}")
            })
            .unwrap_or(base_cmd)
    }

    /// Check if this agent supports session continuation.
    #[must_use]
    pub const fn supports_session_continuation(&self) -> bool {
        !self.session_flag.is_empty()
    }

    fn requires_verbose_for_json(&self, json_enabled: bool) -> bool {
        if !json_enabled || !is_stream_json_output(&self.output_flag) {
            return false;
        }

        // Both `claude` and CCS (`ccs ...`) require verbose mode when using stream-json output.
        // CCS is a wrapper around the Claude CLI and inherits its stream-json quirks.
        let base = self.cmd.split_whitespace().next().unwrap_or("");
        // Extract just the file name from the path to handle cases like "/usr/local/bin/claude"
        let exe_name = Path::new(base)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(base);
        matches!(exe_name, "claude" | "ccs")
    }

    /// Convert from `AgentConfigToml` with CCS environment variable loading.
    ///
    /// Returns the constructed config along with any warnings encountered
    /// during CCS env var loading. The caller should emit warnings at the I/O boundary.
    ///
    /// # Errors
    ///
    /// Returns error if CCS env var loading fails.
    pub fn try_from_ccs(toml: AgentConfigToml) -> (Self, Vec<CcsEnvWarning>) {
        let (ccs_env_vars, ccs_warnings) = match toml.ccs_profile.as_deref() {
            Some(profile) => match load_ccs_env_vars(profile) {
                Ok(vars) => (vars, Vec::new()),
                Err(err) => (
                    HashMap::new(),
                    vec![CcsEnvWarning::LoadFailed {
                        profile: profile.to_string(),
                        error: err.to_string(),
                    }],
                ),
            },
            None => (HashMap::new(), Vec::new()),
        };

        let warnings = if ccs_env_vars.is_empty() {
            if let Some(ref profile) = toml.ccs_profile {
                vec![CcsEnvWarning::LoadFailed {
                    profile: profile.clone(),
                    error: "no environment variables loaded".to_string(),
                }]
            } else {
                Vec::new()
            }
        } else {
            Vec::new()
        };

        let merged_env_vars: HashMap<_, _> =
            toml.env_vars.into_iter().chain(ccs_env_vars).collect();

        let all_warnings: Vec<_> = ccs_warnings.into_iter().chain(warnings).collect();

        (
            Self {
                cmd: toml.cmd,
                output_flag: toml.output_flag,
                yolo_flag: toml.yolo_flag,
                verbose_flag: toml.verbose_flag,
                can_commit: toml.can_commit,
                json_parser: JsonParserType::parse(&toml.json_parser),
                model_flag: toml.model_flag,
                print_flag: toml.print_flag,
                streaming_flag: toml.streaming_flag,
                session_flag: toml.session_flag,
                env_vars: merged_env_vars,
                display_name: toml.display_name,
            },
            all_warnings,
        )
    }
}

/// Builder for `AgentConfig`.
///
/// Provides a fluent API for constructing `AgentConfig` instances
/// without needing to specify all fields.
#[derive(Default, Debug, Clone)]
pub struct AgentConfigBuilder {
    cmd: Option<String>,
    output_flag: Option<String>,
    yolo_flag: Option<String>,
    verbose_flag: Option<String>,
    can_commit: Option<bool>,
    json_parser: Option<JsonParserType>,
    model_flag: Option<String>,
    print_flag: Option<String>,
    streaming_flag: Option<String>,
    session_flag: Option<String>,
    env_vars: Option<HashMap<String, String>>,
    display_name: Option<String>,
}

impl AgentConfigBuilder {
    /// Set the base command to run the agent.
    #[must_use]
    pub fn cmd(self, cmd: impl Into<String>) -> Self {
        Self {
            cmd: Some(cmd.into()),
            ..self
        }
    }

    /// Set the output-format flag.
    #[must_use]
    pub fn output_flag(self, flag: impl Into<String>) -> Self {
        Self {
            output_flag: Some(flag.into()),
            ..self
        }
    }

    /// Set the autonomous mode flag.
    #[must_use]
    pub fn yolo_flag(self, flag: impl Into<String>) -> Self {
        Self {
            yolo_flag: Some(flag.into()),
            ..self
        }
    }

    /// Set the verbose output flag.
    #[must_use]
    pub fn verbose_flag(self, flag: impl Into<String>) -> Self {
        Self {
            verbose_flag: Some(flag.into()),
            ..self
        }
    }

    /// Set whether the agent can run git commit.
    #[must_use]
    pub fn can_commit(self, can_commit: bool) -> Self {
        Self {
            can_commit: Some(can_commit),
            cmd: self.cmd,
            output_flag: self.output_flag,
            yolo_flag: self.yolo_flag,
            verbose_flag: self.verbose_flag,
            json_parser: self.json_parser,
            model_flag: self.model_flag,
            print_flag: self.print_flag,
            streaming_flag: self.streaming_flag,
            session_flag: self.session_flag,
            env_vars: self.env_vars,
            display_name: self.display_name,
        }
    }

    /// Set the JSON parser type.
    #[must_use]
    pub fn json_parser(self, parser: JsonParserType) -> Self {
        Self {
            json_parser: Some(parser),
            cmd: self.cmd,
            output_flag: self.output_flag,
            yolo_flag: self.yolo_flag,
            verbose_flag: self.verbose_flag,
            can_commit: self.can_commit,
            model_flag: self.model_flag,
            print_flag: self.print_flag,
            streaming_flag: self.streaming_flag,
            session_flag: self.session_flag,
            env_vars: self.env_vars,
            display_name: self.display_name,
        }
    }

    /// Set the model/provider flag.
    #[must_use]
    pub fn model_flag(self, flag: impl Into<String>) -> Self {
        Self {
            model_flag: Some(flag.into()),
            ..self
        }
    }

    /// Set the print/non-interactive mode flag.
    #[must_use]
    pub fn print_flag(self, flag: impl Into<String>) -> Self {
        Self {
            print_flag: Some(flag.into()),
            ..self
        }
    }

    /// Set the streaming flag.
    #[must_use]
    pub fn streaming_flag(self, flag: impl Into<String>) -> Self {
        Self {
            streaming_flag: Some(flag.into()),
            ..self
        }
    }

    /// Set the session continuation flag template.
    #[must_use]
    pub fn session_flag(self, flag: impl Into<String>) -> Self {
        Self {
            session_flag: Some(flag.into()),
            ..self
        }
    }

    /// Set environment variables.
    #[must_use]
    pub fn env_vars(self, env_vars: HashMap<String, String>) -> Self {
        Self {
            env_vars: Some(env_vars),
            ..self
        }
    }

    /// Set the display name.
    #[must_use]
    pub fn display_name(self, name: impl Into<String>) -> Self {
        Self {
            display_name: Some(name.into()),
            ..self
        }
    }

    /// Build the `AgentConfig`.
    #[must_use]
    pub fn build(self) -> AgentConfig {
        AgentConfig {
            cmd: self.cmd.unwrap_or_default(),
            output_flag: self.output_flag.unwrap_or_default(),
            yolo_flag: self.yolo_flag.unwrap_or_default(),
            verbose_flag: self.verbose_flag.unwrap_or_default(),
            can_commit: self.can_commit.unwrap_or(true),
            json_parser: self.json_parser.unwrap_or(JsonParserType::Generic),
            model_flag: self.model_flag,
            print_flag: self.print_flag.unwrap_or_default(),
            streaming_flag: self.streaming_flag.unwrap_or_default(),
            session_flag: self.session_flag.unwrap_or_default(),
            env_vars: self.env_vars.unwrap_or_default(),
            display_name: self.display_name,
        }
    }
}

/// TOML configuration for an agent (for deserialization).
#[derive(Debug, Clone, Deserialize)]
pub struct AgentConfigToml {
    /// Base command to run the agent.
    pub cmd: String,
    /// Output-format flag (optional, defaults to empty).
    #[serde(default)]
    pub output_flag: String,
    /// Flag for autonomous mode (optional, defaults to empty).
    #[serde(default)]
    pub yolo_flag: String,
    /// Flag for verbose output (optional, defaults to empty).
    #[serde(default)]
    pub verbose_flag: String,
    /// Whether the agent can run git commit (optional, defaults to true).
    #[serde(default = "default_can_commit")]
    pub can_commit: bool,
    /// Which JSON parser to use (optional, defaults to "generic").
    #[serde(default)]
    pub json_parser: String,
    /// Model/provider flag for model selection.
    #[serde(default)]
    pub model_flag: Option<String>,
    /// Print/non-interactive mode flag (optional, defaults to empty).
    #[serde(default)]
    pub print_flag: String,
    /// Include partial messages flag for streaming with -p.
    #[serde(default = "default_streaming_flag")]
    pub streaming_flag: String,
    /// Session continuation flag template (optional, defaults to empty).
    /// The `{}` placeholder is replaced with the session ID at runtime.
    #[serde(default)]
    pub session_flag: String,
    /// CCS profile name (optional). If provided, loads env vars from CCS config.
    /// These env vars override any manually specified `env_vars`.
    #[serde(default)]
    pub ccs_profile: Option<String>,
    /// Additional environment variables (optional).
    #[serde(default)]
    pub env_vars: HashMap<String, String>,
    /// Display name for UI/logging (optional). If provided, overrides agent name.
    #[serde(default)]
    pub display_name: Option<String>,
}

const fn default_can_commit() -> bool {
    true
}

fn default_streaming_flag() -> String {
    "--include-partial-messages".to_string()
}

impl From<AgentConfigToml> for AgentConfig {
    fn from(toml: AgentConfigToml) -> Self {
        let (config, _) = AgentConfig::try_from_ccs(toml);
        config
    }
}

#[cfg(test)]
mod tests {
    include!("types/tests.rs");
}
