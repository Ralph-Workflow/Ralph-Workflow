use super::types::{AgentConfigToml, DEFAULT_AGENTS_TOML};
use crate::agents::ccs_env::CcsEnvVarsError;
use crate::agents::fallback::FallbackConfig;
use crate::agents::fallback::ResolvedDrainConfig;
use crate::workspace::{Workspace, WorkspaceFs};
use serde::Deserialize;
use std::collections::HashMap;
use std::io;
use std::path::{Path, PathBuf};

// Note: Legacy global config directory functions (global_config_dir, global_agents_config_path)
// have been removed. Use unified config path from the config module instead.

/// Root TOML configuration structure.
#[derive(Debug, Clone, Deserialize)]
pub struct AgentsConfigFile {
    /// Map of agent name to configuration.
    #[serde(default)]
    pub agents: HashMap<String, AgentConfigToml>,
    /// Named reusable agent chains.
    #[serde(default)]
    pub agent_chains: HashMap<String, Vec<String>>,
    /// Built-in drain bindings to named chains.
    #[serde(default)]
    pub agent_drains: HashMap<String, String>,
    /// Legacy agent chain configuration (preferred agents + fallbacks).
    #[serde(default, rename = "agent_chain")]
    pub fallback: Option<FallbackConfig>,
    #[serde(skip)]
    raw_toml: Option<String>,
}

/// Error type for agent configuration loading.
#[derive(Debug, thiserror::Error)]
pub enum AgentConfigError {
    #[error("Failed to read config file: {0}")]
    Io(#[from] io::Error),
    #[error("Failed to parse TOML: {0}")]
    Toml(#[from] toml::de::Error),
    #[error("Built-in agents.toml template is invalid TOML: {0}")]
    DefaultTemplateToml(toml::de::Error),
    #[error("Invalid agent drain configuration: {0}")]
    InvalidDrainConfig(String),
    #[error("{0}")]
    CcsEnvVars(#[from] CcsEnvVarsError),
}

/// Result of checking/initializing the agents config file.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfigInitResult {
    /// Config file already exists, no action taken.
    AlreadyExists,
    /// Config file was just created from template.
    Created,
}

impl AgentsConfigFile {
    /// Resolve the configured agent chains into explicit built-in drains.
    ///
    /// Returns `None` when the file defines no chain configuration at all.
    ///
    /// # Errors
    ///
    /// Returns error if the named chain/drain schema is internally inconsistent
    /// or mixed with the legacy `[agent_chain]` table.
    pub fn resolve_drains_checked(&self) -> Result<Option<ResolvedDrainConfig>, AgentConfigError> {
        if let Some(raw_toml) = &self.raw_toml {
            let parsed: crate::config::UnifiedConfig = toml::from_str(raw_toml)?;
            return crate::config::UnifiedConfig::default()
                .merge_with_content(raw_toml, &parsed)
                .resolve_agent_drains_checked()
                .map_err(AgentConfigError::InvalidDrainConfig);
        }

        crate::config::UnifiedConfig {
            agent_chains: self.agent_chains.clone(),
            agent_drains: self.agent_drains.clone(),
            agent_chain: self.fallback.clone(),
            ..crate::config::UnifiedConfig::default()
        }
        .resolve_agent_drains_checked()
        .map_err(AgentConfigError::InvalidDrainConfig)
    }

    /// Load agents config from a file, returning None if file doesn't exist.
    ///
    /// # Errors
    ///
    /// Returns error if:
    /// - File cannot be read
    /// - File contents are not valid TOML
    pub fn load_from_file<P: AsRef<Path>>(path: P) -> Result<Option<Self>, AgentConfigError> {
        let path = path.as_ref();
        let workspace = WorkspaceFs::new(PathBuf::from("."));

        if !workspace.exists(path) {
            return Ok(None);
        }

        let contents = workspace.read(path)?;
        let config: Self = toml::from_str(&contents)?;
        Ok(Some(Self {
            raw_toml: Some(contents),
            ..config
        }))
    }

    /// Load agents config from a file using workspace abstraction.
    ///
    /// This is the architecture-conformant version that uses the Workspace trait
    /// instead of direct filesystem access, allowing for proper testing with
    /// `MemoryWorkspace`.
    ///
    /// # Errors
    ///
    /// Returns error if:
    /// - File cannot be read from workspace
    /// - File contents are not valid TOML
    pub fn load_from_file_with_workspace(
        path: &Path,
        workspace: &dyn Workspace,
    ) -> Result<Option<Self>, AgentConfigError> {
        if !workspace.exists(path) {
            return Ok(None);
        }

        let contents = workspace
            .read(path)
            .map_err(|e| AgentConfigError::Io(io::Error::other(e)))?;
        let config: Self = toml::from_str(&contents)?;
        Ok(Some(Self {
            raw_toml: Some(contents),
            ..config
        }))
    }

    /// Ensure agents config file exists, creating it from template if needed.
    ///
    /// # Errors
    ///
    /// Returns error if:
    /// - Parent directories cannot be created
    /// - Default template cannot be written to file
    pub fn ensure_config_exists<P: AsRef<Path>>(path: P) -> io::Result<ConfigInitResult> {
        let path = path.as_ref();
        let workspace = WorkspaceFs::new(PathBuf::from("."));

        if workspace.exists(path) {
            return Ok(ConfigInitResult::AlreadyExists);
        }

        // Create parent directories if they don't exist
        if let Some(parent) = path.parent() {
            workspace.create_dir_all(parent)?;
        }

        // Write the default template
        workspace.write(path, DEFAULT_AGENTS_TOML)?;

        Ok(ConfigInitResult::Created)
    }

    /// Ensure agents config file exists using workspace abstraction.
    ///
    /// This is the architecture-conformant version that uses the Workspace trait
    /// instead of direct filesystem access, allowing for proper testing with
    /// `MemoryWorkspace`.
    ///
    /// # Errors
    ///
    /// Returns error if:
    /// - Parent directories cannot be created in workspace
    /// - Default template cannot be written to workspace
    pub fn ensure_config_exists_with_workspace(
        path: &Path,
        workspace: &dyn Workspace,
    ) -> io::Result<ConfigInitResult> {
        if workspace.exists(path) {
            return Ok(ConfigInitResult::AlreadyExists);
        }

        // Create parent directories if they don't exist
        if let Some(parent) = path.parent() {
            workspace.create_dir_all(parent)?;
        }

        // Write the default template
        workspace.write(path, DEFAULT_AGENTS_TOML)?;

        Ok(ConfigInitResult::Created)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn load_from_file_with_workspace_returns_none_when_missing() {
        let workspace = MemoryWorkspace::new_test();
        let path = Path::new(".agent/agents.toml");

        let Ok(result) = AgentsConfigFile::load_from_file_with_workspace(path, &workspace) else {
            panic!("load_from_file_with_workspace failed");
        };
        assert!(result.is_none());
    }

    #[test]
    fn load_from_file_with_workspace_parses_valid_config() {
        let workspace =
            MemoryWorkspace::new_test().with_file(".agent/agents.toml", DEFAULT_AGENTS_TOML);
        let path = Path::new(".agent/agents.toml");

        let Ok(Some(config)) = AgentsConfigFile::load_from_file_with_workspace(path, &workspace)
        else {
            panic!("load_from_file_with_workspace failed or returned None");
        };
        assert!(config.agents.contains_key("claude"));
    }

    #[test]
    fn ensure_config_exists_with_workspace_creates_file_when_missing() {
        let workspace = MemoryWorkspace::new_test();
        let path = Path::new(".agent/agents.toml");

        let Ok(result) = AgentsConfigFile::ensure_config_exists_with_workspace(path, &workspace)
        else {
            panic!("ensure_config_exists_with_workspace failed");
        };
        assert!(matches!(result, ConfigInitResult::Created));
        assert!(workspace.exists(path));
        let Ok(contents) = workspace.read(path) else {
            panic!("failed to read created file");
        };
        assert_eq!(contents, DEFAULT_AGENTS_TOML);
    }

    #[test]
    fn ensure_config_exists_with_workspace_does_not_overwrite_existing() {
        let workspace =
            MemoryWorkspace::new_test().with_file(".agent/agents.toml", "# custom config");
        let path = Path::new(".agent/agents.toml");

        let Ok(result) = AgentsConfigFile::ensure_config_exists_with_workspace(path, &workspace)
        else {
            panic!("ensure_config_exists_with_workspace failed");
        };
        assert!(matches!(result, ConfigInitResult::AlreadyExists));
        let Ok(contents) = workspace.read(path) else {
            panic!("failed to read file");
        };
        assert_eq!(contents, "# custom config");
    }

    #[test]
    fn default_template_uses_named_chain_and_drain_schema() {
        let uncommented_lines = DEFAULT_AGENTS_TOML
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .collect::<Vec<_>>();

        assert!(
            !uncommented_lines.contains(&"[agent_chain]"),
            "default template should no longer use legacy [agent_chain] as the primary schema"
        );
        assert!(
            uncommented_lines.contains(&"[agent_chains]"),
            "default template should define reusable named chains"
        );
        assert!(
            uncommented_lines.contains(&"[agent_drains]"),
            "default template should bind built-in drains to named chains"
        );
    }
}
