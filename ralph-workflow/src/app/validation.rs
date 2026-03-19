//! Agent validation and chain validation.
//!
//! This module handles validation of agents and agent chains:
//! - Resolving required agent names from config
//! - Validating that agent commands exist in the registry
//! - Enforcing workflow-capable agents (`can_commit=true`)
//! - Validating agent chain configuration

use crate::agents::AgentRegistry;
use crate::app::config_init::AgentResolutionSources;
use crate::config::Config;
use crate::logger::Logger;
use std::path::Path;

/// Result of agent validation containing the resolved agent names.
#[derive(Debug)]
pub struct ValidatedAgents {
    /// The validated developer agent name.
    pub developer_agent: String,
    /// The validated reviewer agent name.
    pub reviewer_agent: String,
}

/// Resolves and validates the required agent names from configuration.
///
/// Both developer and reviewer agents must be configured at this point,
/// either via CLI args, environment variables, or the resolved built-in drains
/// from `[agent_chains]` / `[agent_drains]` (with legacy `[agent_chain]`
/// accepted as a compatibility input).
///
/// # Arguments
///
/// * `config` - The pipeline configuration
/// * `sources` - Description of config sources consulted for resolution
///
/// # Returns
///
/// Returns the validated agent names or an error if agents are not configured.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn resolve_required_agents(
    config: &Config,
    sources: &AgentResolutionSources,
) -> anyhow::Result<ValidatedAgents> {
    let searched = sources.describe_searched_sources();

    let developer_agent = config.developer_agent.clone().ok_or_else(|| {
        anyhow::anyhow!(
            "No developer agent configured. Searched: {searched}.\n\
            Set via --developer-agent, RALPH_DEVELOPER_AGENT env, or [agent_chains]/[agent_drains] in config.\n\
            Legacy [agent_chain] input is still accepted for compatibility."
        )
    })?;
    let reviewer_agent = config.reviewer_agent.clone().ok_or_else(|| {
        anyhow::anyhow!(
            "No reviewer agent configured. Searched: {searched}.\n\
            Set via --reviewer-agent, RALPH_REVIEWER_AGENT env, or [agent_chains]/[agent_drains] in config.\n\
            Legacy [agent_chain] input is still accepted for compatibility."
        )
    })?;

    Ok(ValidatedAgents {
        developer_agent,
        reviewer_agent,
    })
}

/// Validates that agent commands exist in the registry.
///
/// Checks that both developer and reviewer agents have valid commands
/// defined either in the config or the registry.
///
/// # Arguments
///
/// * `config` - The pipeline configuration
/// * `registry` - The agent registry
/// * `developer_agent` - Name of the developer agent
/// * `reviewer_agent` - Name of the reviewer agent
/// * `config_path` - Path to the unified config file for error messages
///
/// # Returns
///
/// Returns `Ok(())` if validation passes, or an error with details.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_agent_commands(
    config: &Config,
    registry: &AgentRegistry,
    developer_agent: &str,
    reviewer_agent: &str,
    config_path: &Path,
) -> anyhow::Result<()> {
    // Validate developer command exists
    if config.developer_cmd.is_none() {
        let resolved_developer = registry.resolve_fuzzy(developer_agent);
        let dev_agent_ref = resolved_developer.as_deref().unwrap_or(developer_agent);
        registry.developer_cmd(dev_agent_ref).ok_or_else(|| {
            let suggestion = resolved_developer
                .as_ref()
                .filter(|n| n != &developer_agent)
                .map(|correct| format!(" Did you mean '{correct}'?"))
                .unwrap_or_default();
            anyhow::anyhow!(
                "Unknown developer agent '{}'.{}. Use --list-agents or define it in {} under [agents].",
                developer_agent,
                suggestion,
                config_path.display()
            )
        })?;
    }

    // Validate reviewer command exists
    if config.reviewer_cmd.is_none() {
        let resolved_reviewer = registry.resolve_fuzzy(reviewer_agent);
        let rev_agent_ref = resolved_reviewer.as_deref().unwrap_or(reviewer_agent);
        registry.reviewer_cmd(rev_agent_ref).ok_or_else(|| {
            let suggestion = resolved_reviewer
                .as_ref()
                .filter(|n| n != &reviewer_agent)
                .map(|correct| format!(" Did you mean '{correct}'?"))
                .unwrap_or_default();
            anyhow::anyhow!(
                "Unknown reviewer agent '{}'.{}. Use --list-agents or define it in {} under [agents].",
                reviewer_agent,
                suggestion,
                config_path.display()
            )
        })?;
    }

    Ok(())
}

/// Validates that agents are workflow-capable (`can_commit=true`).
///
/// Agents with `can_commit=false` are chat-only / non-tool agents and will
/// stall Ralph's workflow. This validation is skipped if a custom command
/// override is provided.
///
/// # Arguments
///
/// * `config` - The pipeline configuration
/// * `registry` - The agent registry
/// * `developer_agent` - Name of the developer agent
/// * `reviewer_agent` - Name of the reviewer agent
/// * `config_path` - Path to the unified config file for error messages
///
/// # Returns
///
/// Returns `Ok(())` if validation passes, or an error with details.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_can_commit(
    config: &Config,
    registry: &AgentRegistry,
    developer_agent: &str,
    reviewer_agent: &str,
    config_path: &Path,
) -> anyhow::Result<()> {
    // Enforce workflow-capable agents unless custom command override provided
    if config.developer_cmd.is_none() {
        let resolved = registry
            .resolve_fuzzy(developer_agent)
            .unwrap_or_else(|| developer_agent.to_string());
        if let Some(cfg) = registry.resolve_config(&resolved) {
            if !cfg.can_commit {
                let resolved_note = if resolved == developer_agent {
                    String::new()
                } else {
                    format!(" (resolved to '{resolved}')")
                };
                anyhow::bail!(
                    "Developer agent '{}'{} has can_commit=false and cannot run Ralph's workflow.\n\
                    Fix: choose a different agent (see --list-agents) or set can_commit=true in {} under [agents].",
                    developer_agent,
                    resolved_note,
                    config_path.display()
                );
            }
        }
    }
    if config.reviewer_cmd.is_none() {
        let resolved = registry
            .resolve_fuzzy(reviewer_agent)
            .unwrap_or_else(|| reviewer_agent.to_string());
        if let Some(cfg) = registry.resolve_config(&resolved) {
            if !cfg.can_commit {
                let resolved_note = if resolved == reviewer_agent {
                    String::new()
                } else {
                    format!(" (resolved to '{resolved}')")
                };
                anyhow::bail!(
                    "Reviewer agent '{}'{} has can_commit=false and cannot run Ralph's workflow.\n\
                    Fix: choose a different agent (see --list-agents) or set can_commit=true in {} under [agents].",
                    reviewer_agent,
                    resolved_note,
                    config_path.display()
                );
            }
        }
    }

    Ok(())
}

/// Validates that agent chains are properly configured.
///
/// Displays an error and exits if the agent chains are not configured.
///
/// # Arguments
///
/// * `registry` - The agent registry
/// * `sources` - Description of config sources consulted for resolution
/// * `logger` - Logger for output
pub fn validate_agent_chains(
    registry: &AgentRegistry,
    sources: &AgentResolutionSources,
    logger: &Logger,
) {
    if let Err(msg) = registry.validate_agent_chains(&sources.describe_searched_sources()) {
        logger.error(&msg);
        logger.warn("Hint: Run 'ralph --init-global' to create ~/.config/ralph-workflow.toml.");
        crate::app::env_access::exit_with_code(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_can_commit_uses_fuzzy_resolution() {
        let registry = AgentRegistry::new().unwrap();
        let config = Config {
            developer_cmd: None,
            reviewer_cmd: None,
            ..Config::default()
        };

        // "AiChat" resolves to "aichat" (can_commit=false). This must be rejected.
        let err = validate_can_commit(
            &config,
            &registry,
            "AiChat",
            "claude",
            Path::new("ralph-workflow.toml"),
        )
        .unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("can_commit=false"));
        assert!(msg.contains("AiChat"));
        assert!(msg.contains("resolved to 'aichat'"));
    }

    #[test]
    fn resolve_required_agents_error_mentions_searched_sources() {
        let config = Config {
            developer_agent: None,
            reviewer_agent: Some("claude".to_string()),
            ..Config::default()
        };

        let err = resolve_required_agents(
            &config,
            &AgentResolutionSources {
                local_config_path: Some(Path::new(".agent/ralph-workflow.toml").to_path_buf()),
                global_config_path: Some(Path::new("~/.config/ralph-workflow.toml").to_path_buf()),
                built_in_defaults: true,
            },
        )
        .unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("local config"),
            "error should mention local config: {msg}"
        );
        assert!(
            msg.contains("global config"),
            "error should mention global config: {msg}"
        );
        assert!(
            msg.contains("built-in defaults"),
            "error should mention built-in defaults: {msg}"
        );
        assert!(
            msg.contains("[agent_chains]/[agent_drains]"),
            "error should guide users to the canonical named chain/drain schema: {msg}"
        );
    }

    #[test]
    fn resolve_required_agents_error_for_reviewer_mentions_sources() {
        let config = Config {
            developer_agent: Some("claude".to_string()),
            reviewer_agent: None,
            ..Config::default()
        };

        let err = resolve_required_agents(
            &config,
            &AgentResolutionSources {
                local_config_path: Some(Path::new(".agent/ralph-workflow.toml").to_path_buf()),
                global_config_path: Some(Path::new("~/.config/ralph-workflow.toml").to_path_buf()),
                built_in_defaults: true,
            },
        )
        .unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("reviewer"),
            "error should name the missing role: {msg}"
        );
        assert!(
            msg.contains("local config"),
            "error should mention local config: {msg}"
        );
        assert!(
            msg.contains("[agent_chains]/[agent_drains]"),
            "error should guide users to the canonical named chain/drain schema: {msg}"
        );
    }

    #[test]
    fn resolve_required_agents_error_with_explicit_config_omits_local_source() {
        let config = Config {
            developer_agent: None,
            reviewer_agent: Some("claude".to_string()),
            ..Config::default()
        };

        let err = resolve_required_agents(
            &config,
            &AgentResolutionSources {
                local_config_path: None,
                global_config_path: Some(Path::new("/custom/path.toml").to_path_buf()),
                built_in_defaults: true,
            },
        )
        .unwrap_err();
        let msg = err.to_string();

        assert!(
            msg.contains("global config (/custom/path.toml), built-in defaults"),
            "error should include actual consulted sources: {msg}"
        );
        assert!(
            !msg.contains("local config"),
            "error should not mention local config when not consulted: {msg}"
        );
    }
}
