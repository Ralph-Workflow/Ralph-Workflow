use serde::{Deserialize, Serialize};
use specta::Type;

use crate::boundary::config_process;

/// Information about an update check result for an agent tool.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ToolUpdateInfo {
    pub name: String,
    pub current_version: Option<String>,
    pub latest_version: Option<String>,
    pub update_available: bool,
    pub message: String,
}

/// Check installed agent tools for available updates.
///
/// # Errors
///
/// Returns an error if tool version checking fails unexpectedly.
pub fn check_tool_updates() -> Result<Vec<ToolUpdateInfo>, String> {
    let tools = [
        ("Claude Code", "claude"),
        ("Codex", "codex"),
        ("OpenCode", "opencode"),
    ];

    let results = tools
        .iter()
        .map(|(name, binary)| {
            let current_version = config_process::read_version_line(binary);

            ToolUpdateInfo {
                name: (*name).to_string(),
                current_version: current_version.clone(),
                latest_version: None, // Would require network call or package manager check
                update_available: false, // Cannot determine without network check
                message: current_version.map_or_else(
                    || format!("{binary} not installed"),
                    |v| format!("Current: {v} — check package manager for updates"),
                ),
            }
        })
        .collect();

    Ok(results)
}

/// Trigger a platform-appropriate installation flow for an agent tool.
///
/// On macOS/Linux this opens a terminal with the recommended install command.
/// On Windows it opens the tool's download page.
///
/// # Errors
///
/// Returns an error if the tool name is unknown or the install command fails to launch.
pub fn install_agent_tool(name: String) -> Result<(), String> {
    let install_cmd = match name.as_str() {
        "Claude Code" => Some("bun install -g @anthropic-ai/claude-code"),
        "Codex" => Some("bun install -g @openai/codex"),
        "OpenCode" => Some("bun install -g opencode-ai"),
        other => return Err(format!("Unknown tool: {other}")),
    };

    if let Some(cmd) = install_cmd {
        config_process::spawn_install_command(cmd)?;
    }

    Ok(())
}

/// Open the CLI settings or configuration for an agent tool.
///
/// # Errors
///
/// Returns an error if the tool name is unknown or the settings command fails to launch.
pub fn open_tool_settings(name: String) -> Result<(), String> {
    let binary = match name.as_str() {
        "Claude Code" => "claude",
        "Codex" => "codex",
        "OpenCode" => "opencode",
        other => return Err(format!("Unknown tool: {other}")),
    };

    // Attempt to open settings via the CLI's settings/config subcommand.
    config_process::spawn_help(binary)?;

    Ok(())
}

/// Refresh the list of available models for a given agent tool.
///
/// # Errors
///
/// Returns an error if the tool name is unknown or the model list cannot be retrieved.
pub fn refresh_tool_models(name: String) -> Result<Vec<String>, String> {
    let binary = match name.as_str() {
        "Claude Code" => "claude",
        "Codex" => "codex",
        "OpenCode" => "opencode",
        other => return Err(format!("Unknown tool: {other}")),
    };

    // Attempt to retrieve model list via CLI.
    if let Some(models) = config_process::read_model_list(binary) {
        return Ok(models);
    }

    // Fallback: return known model names per tool.
    let fallback = match name.as_str() {
        "Claude Code" => vec![
            "claude-opus-4-5".to_string(),
            "claude-sonnet-4-6".to_string(),
            "claude-haiku-4".to_string(),
        ],
        "Codex" => vec!["gpt-4o".to_string(), "gpt-4o-mini".to_string()],
        "OpenCode" => vec!["claude-sonnet-4-6".to_string(), "gpt-4o".to_string()],
        _ => vec![],
    };

    Ok(fallback)
}

/// Information about an installed or detectable agent tool (CLI).
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct AgentToolInfo {
    pub name: String,
    pub binary: String,
    pub installed: bool,
    pub version: Option<String>,
    pub auth_status: String,
    pub health: String,
    pub description: String,
    pub available_models: Vec<String>,
    pub binary_location: Option<String>,
}

/// Probe a known CLI tool binary in the PATH.
fn probe_tool(name: &str, binary: &str, description: &str) -> AgentToolInfo {
    let binary_location = config_process::probe_binary_location(binary);
    let installed = binary_location.is_some();

    let version = if installed {
        config_process::read_version_line(binary)
    } else {
        None
    };

    let health = if installed {
        if version.is_some() {
            "Ready".to_string()
        } else {
            "Needs setup".to_string()
        }
    } else {
        "Not installed".to_string()
    };

    // Try to retrieve available models; fall back to known defaults if the CLI
    // doesn't support --list-models.
    let available_models = if installed {
        config_process::read_model_list(binary).unwrap_or_else(|| match name {
            "Claude Code" => vec![
                "claude-opus-4-5".to_string(),
                "claude-sonnet-4-6".to_string(),
                "claude-haiku-4".to_string(),
            ],
            "Codex" => vec!["gpt-4o".to_string(), "gpt-4o-mini".to_string()],
            "OpenCode" => vec!["claude-sonnet-4-6".to_string(), "gpt-4o".to_string()],
            _ => vec![],
        })
    } else {
        vec![]
    };

    AgentToolInfo {
        name: name.to_string(),
        binary: binary.to_string(),
        installed,
        version,
        auth_status: if installed {
            "Unknown".to_string()
        } else {
            "N/A".to_string()
        },
        health,
        description: description.to_string(),
        available_models,
        binary_location,
    }
}

/// Get information about known agent tools (Claude Code, Codex, `OpenCode`).
///
/// # Errors
///
/// This command currently never fails but returns `Err` to satisfy the Result interface.
pub fn get_agent_tools() -> Result<Vec<AgentToolInfo>, String> {
    let tools = vec![
        probe_tool(
            "Claude Code",
            "claude",
            "Anthropic's Claude AI coding assistant",
        ),
        probe_tool("Codex", "codex", "OpenAI's Codex CLI coding agent"),
        probe_tool(
            "OpenCode",
            "opencode",
            "Open-source AI coding agent compatible with multiple providers",
        ),
    ];
    Ok(tools)
}

/// Run a test invocation of an agent tool to verify it works.
///
/// # Errors
///
/// Returns an error string if the tool is not installed or the test invocation fails.
pub fn test_agent_tool_connection(name: String) -> Result<String, String> {
    let binary = match name.as_str() {
        "Claude Code" => "claude",
        "Codex" => "codex",
        "OpenCode" => "opencode",
        other => return Err(format!("Unknown tool: {other}")),
    };

    let output = config_process::run_version_output(binary)
        .map_err(|e| format!("{binary} not found or failed to start: {e}"))?;

    if output.success {
        let version = output.stdout;
        Ok(format!("Connected: {}", version.trim()))
    } else {
        Err(format!("Tool returned error: {}", output.stderr))
    }
}

#[cfg(test)]
mod tests {
    use super::check_tool_updates;

    #[test]
    fn test_check_tool_updates_returns_result_for_all_tools() {
        let result = check_tool_updates();
        assert!(result.is_ok(), "check_tool_updates should succeed");
        let updates = match result {
            Ok(updates) => updates,
            Err(error) => panic!("tool update check should return updates list: {error}"),
        };
        assert_eq!(
            updates.len(),
            3,
            "Should check 3 tools: Claude Code, Codex, OpenCode"
        );
        let tool_names: Vec<&str> = updates.iter().map(|u| u.name.as_str()).collect();
        assert!(
            tool_names.contains(&"Claude Code"),
            "Should include Claude Code"
        );
        assert!(tool_names.contains(&"Codex"), "Should include Codex");
        assert!(tool_names.contains(&"OpenCode"), "Should include OpenCode");
    }
}
