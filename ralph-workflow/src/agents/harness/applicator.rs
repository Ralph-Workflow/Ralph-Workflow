//! Harness applicator: detects agent type and writes harness config to workspace.
//!
//! This module bridges the harness config generators with the agent spawn path,
//! ensuring each agent discovers its MCP configuration on launch.
//!
//! # Config Merging Strategy
//!
//! For Claude Code, we merge Ralph's MCP server into a session-scoped harness
//! config under `.agent/tmp/harness/...` and inject that config via Claude's
//! `--settings` flag. This preserves the user's existing project config
//! (`.claude/settings.local.json`) and avoids persistent permission overrides.
//!

use std::collections::HashMap;
use std::path::Path;

use crate::agents::harness::{
    AgentHarness, AiderHarness, ClaudeHarness, CodexHarness, HarnessConfig, OpenCodeHarness,
};
use crate::agents::session::AgentSession;
use crate::workspace::Workspace;

/// Recognised agent executable types.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentType {
    /// Claude Code CLI.
    Claude,
    /// OpenCode CLI.
    OpenCode,
    /// Aider CLI.
    Aider,
    /// Codex CLI.
    Codex,
    /// Unrecognised agent - graceful degradation.
    Unknown,
}

/// Detect the agent type from its command string.
///
/// Uses contains-based matching so that paths like `/usr/local/bin/claude` or
/// versioned names like `codex-cli` are handled correctly.
///
/// CCS (Claude Code Switch) is a wrapper around Claude Code that supports
/// multiple profiles/providers. CCS agents use the same settings format as
/// Claude Code, so they are classified as `AgentType::Claude`.
pub fn detect_agent_type(cmd: &str) -> AgentType {
    classify_agent_cmd(&cmd.to_lowercase())
}

/// Pure: map a lowercase command string to an `AgentType`.
fn classify_agent_cmd(lower: &str) -> AgentType {
    AGENT_PATTERNS
        .iter()
        .find(|(pattern, _)| lower.contains(pattern))
        .map(|(_, agent_type)| *agent_type)
        .unwrap_or(AgentType::Unknown)
}

/// Static table of (needle, AgentType) pairs used by `classify_agent_cmd`.
static AGENT_PATTERNS: &[(&str, AgentType)] = &[
    ("claude", AgentType::Claude),
    ("ccs", AgentType::Claude),
    ("opencode", AgentType::OpenCode),
    ("aider", AgentType::Aider),
    ("codex", AgentType::Codex),
];

/// Result of applying a harness configuration.
#[derive(Debug, Clone)]
pub struct HarnessApplyResult {
    /// Additional environment variables the agent process should inherit.
    pub extra_env_vars: HashMap<String, String>,
    /// Path where the config was written (for diagnostics/logging).
    pub config_path: Option<String>,
    /// Additional CLI arguments the agent should receive (already quoted).
    pub extra_cmd_args: Vec<String>,
}

/// The path to Claude Code's project-local settings file.
const CLAUDE_SETTINGS_LOCAL: &str = ".claude/settings.local.json";

/// Generate harness configuration, write it to the workspace, and return
/// any extra env vars/args needed for the agent to discover the config.
///
/// For Claude Code, this merges Ralph's MCP config into a session-scoped
/// settings file and injects it via Claude's `--settings` flag.
///
/// For `AgentType::Unknown` this is a no-op that returns an empty result.
///
/// # Errors
///
/// Returns an error if config serialisation or workspace I/O fails.
pub fn apply_harness_config(
    agent_type: AgentType,
    session: &AgentSession,
    mcp_endpoint: &str,
    workspace: &dyn Workspace,
) -> std::io::Result<HarnessApplyResult> {
    match agent_type {
        AgentType::Claude => apply_claude_harness(session, mcp_endpoint, workspace),
        AgentType::OpenCode => apply_opencode_harness(session, mcp_endpoint, workspace),
        AgentType::Aider => apply_aider_harness(session, mcp_endpoint, workspace),
        AgentType::Codex => apply_codex_harness(session, mcp_endpoint, workspace),
        AgentType::Unknown => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "MCP harness setup failed: unknown agent type is not allowed",
        )),
    }
}

/// Apply OpenCode harness: write config and inject via `OPENCODE_CONFIG` env var.
fn apply_opencode_harness(
    session: &AgentSession,
    mcp_endpoint: &str,
    workspace: &dyn Workspace,
) -> std::io::Result<HarnessApplyResult> {
    let harness_dir = harness_dir_for(session);
    let config = OpenCodeHarness.generate(session, mcp_endpoint);
    let content = harness_config_content(&config);
    let config_path = format!("{harness_dir}/config.json");
    write_config(workspace, &harness_dir, &config_path, &content)?;

    let absolute_path = workspace.root().join(&config_path);
    let env = HashMap::from([(
        "OPENCODE_CONFIG".to_string(),
        absolute_path.to_string_lossy().into_owned(),
    )]);

    Ok(HarnessApplyResult {
        extra_env_vars: env,
        config_path: Some(config_path),
        extra_cmd_args: Vec::new(),
    })
}

/// Apply Aider harness: write args config, no extra env or CLI args needed.
fn apply_aider_harness(
    session: &AgentSession,
    mcp_endpoint: &str,
    workspace: &dyn Workspace,
) -> std::io::Result<HarnessApplyResult> {
    let harness_dir = harness_dir_for(session);
    let config = AiderHarness.generate(session, mcp_endpoint);
    let content = harness_config_content(&config);
    let config_path = format!("{harness_dir}/args.json");
    write_config(workspace, &harness_dir, &config_path, &content)?;

    Ok(HarnessApplyResult {
        extra_env_vars: HashMap::new(),
        config_path: Some(config_path),
        extra_cmd_args: Vec::new(),
    })
}

/// Apply Codex harness: write config and inject MCP settings via `-c` overrides.
fn apply_codex_harness(
    session: &AgentSession,
    mcp_endpoint: &str,
    workspace: &dyn Workspace,
) -> std::io::Result<HarnessApplyResult> {
    let harness_dir = harness_dir_for(session);
    let config = CodexHarness.generate(session, mcp_endpoint);
    let content = harness_config_content(&config);
    let config_path = format!("{harness_dir}/config.toml");
    write_config(workspace, &harness_dir, &config_path, &content)?;

    let extra_cmd_args = build_codex_override_args(session, mcp_endpoint);

    Ok(HarnessApplyResult {
        extra_env_vars: HashMap::new(),
        config_path: Some(config_path),
        extra_cmd_args,
    })
}

/// Build the `-c key=value` CLI arguments for Codex MCP configuration.
///
/// The command is resolved to the absolute path of the current executable so
/// that Codex can spawn `ralph --mcp-proxy` without relying on PATH being set
/// in its environment. This mirrors the strategy used by `CodexHarness::generate`
/// for the config.toml; both must use the same resolved path so that the -c
/// overrides (which take precedence over the config file) do not regress to a
/// PATH-dependent bare `"ralph"`.
fn build_codex_override_args(session: &AgentSession, mcp_endpoint: &str) -> Vec<String> {
    // Resolve the absolute path to the ralph binary. Falls back to bare "ralph"
    // if current_exe() cannot be determined (mirrors CodexHarness::generate).
    let ralph_command = std::env::current_exe()
        .ok()
        .and_then(|p| p.to_str().map(String::from))
        .unwrap_or_else(|| "ralph".to_string());
    let overrides = [
        format!("mcp_servers.ralph.command=\"{ralph_command}\""),
        "mcp_servers.ralph.args=[\"--mcp-proxy\"]".to_string(),
        format!("mcp_servers.ralph.env.RALPH_MCP_ENDPOINT=\"{mcp_endpoint}\""),
        format!(
            "mcp_servers.ralph.env.RALPH_SESSION_ID=\"{}\"",
            session.session_id
        ),
    ];
    overrides
        .into_iter()
        .flat_map(|v| ["-c".to_string(), shell_escape_posix(v.as_str())])
        .collect()
}

/// Compute the per-session harness directory path.
fn harness_dir_for(session: &AgentSession) -> String {
    format!(".agent/tmp/harness/{}", session.session_id.as_str())
}

/// Apply Claude Code harness by writing a session-scoped config and injecting it
/// through Claude's `--settings` flag, leaving the user's real config untouched.
fn apply_claude_harness(
    session: &AgentSession,
    mcp_endpoint: &str,
    workspace: &dyn Workspace,
) -> std::io::Result<HarnessApplyResult> {
    let config = ClaudeHarness.generate(session, mcp_endpoint);
    let ralph_settings_json = harness_config_content(&config);
    let ralph_value: serde_json::Value = serde_json::from_str(&ralph_settings_json)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

    let existing = workspace
        .read(Path::new(CLAUDE_SETTINGS_LOCAL))
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .unwrap_or_else(|| serde_json::json!({}));

    let merged = merge_claude_settings(&existing, &ralph_value);
    let merged_str = serde_json::to_string_pretty(&merged)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let merged_mcp_config = serde_json::json!({
        "mcpServers": merged
            .get("mcpServers")
            .cloned()
            .unwrap_or_else(|| serde_json::json!({}))
    });
    let merged_mcp_str = serde_json::to_string_pretty(&merged_mcp_config)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

    let session_id = session.session_id.as_str();
    let harness_dir = format!(".agent/tmp/harness/{session_id}/claude");
    let config_path = format!("{harness_dir}/settings.local.json");
    let mcp_config_path = format!("{harness_dir}/mcp.json");
    let harness_dir_path = Path::new(&harness_dir);

    workspace.remove_dir_all_if_exists(harness_dir_path)?;
    workspace.create_dir_all(harness_dir_path)?;
    workspace.write(Path::new(&config_path), &merged_str)?;
    workspace.write(Path::new(&mcp_config_path), &merged_mcp_str)?;

    let absolute_path = workspace.root().join(&config_path);
    let escaped_settings = shell_escape_posix(absolute_path.to_string_lossy().as_ref());
    let absolute_mcp_path = workspace.root().join(&mcp_config_path);
    let escaped_mcp_config = shell_escape_posix(absolute_mcp_path.to_string_lossy().as_ref());

    Ok(HarnessApplyResult {
        extra_env_vars: HashMap::new(),
        config_path: Some(config_path),
        extra_cmd_args: vec![
            "--tools".to_string(),
            "''".to_string(),
            "--settings".to_string(),
            escaped_settings,
            "--mcp-config".to_string(),
            escaped_mcp_config,
        ],
    })
}

/// Merge Ralph's MCP settings into existing Claude Code settings.
///
/// - `mcpServers`: adds Ralph's server entry, preserving other servers
/// - `permissions.allow`: appends Ralph's MCP tool permissions, deduplicating
/// - `permissions.deny`: appends Ralph's deny entries, deduplicating
/// - All other fields: preserved from existing settings
fn merge_claude_settings(
    existing: &serde_json::Value,
    ralph: &serde_json::Value,
) -> serde_json::Value {
    // Build a new object by chaining existing entries with new merged entries
    let add_to_object = |obj: serde_json::Map<String, serde_json::Value>,
                         (key, value): (String, serde_json::Value)|
     -> serde_json::Map<String, serde_json::Value> {
        obj.into_iter()
            .chain(std::iter::once((key, value)))
            .collect()
    };

    let result = existing.as_object().cloned().unwrap_or_default();

    let with_mcp = merge_mcp_servers(existing, ralph)
        .map(|(k, v)| add_to_object(result.clone(), (k, v)))
        .unwrap_or(result);

    let with_perms = merge_permissions(existing, ralph)
        .map(|(k, v)| add_to_object(with_mcp.clone(), (k, v)))
        .unwrap_or(with_mcp);

    serde_json::Value::Object(with_perms)
}

/// Merge mcpServers from existing and ralph, returning (key, value) for the merged object.
fn merge_mcp_servers(
    existing: &serde_json::Value,
    ralph: &serde_json::Value,
) -> Option<(String, serde_json::Value)> {
    let ralph_servers = ralph.get("mcpServers")?.as_object()?;
    let existing_servers = existing
        .get("mcpServers")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();

    let merged: serde_json::Map<String, serde_json::Value> = existing_servers
        .into_iter()
        .chain(ralph_servers.iter().map(|(k, v)| (k.clone(), v.clone())))
        .collect();
    Some(("mcpServers".to_string(), serde_json::Value::Object(merged)))
}

/// Merge permissions from existing and ralph, returning (key, value) for the merged object.
fn merge_permissions(
    existing: &serde_json::Value,
    ralph: &serde_json::Value,
) -> Option<(String, serde_json::Value)> {
    let merge_array =
        |existing: &serde_json::Value, ralph: &serde_json::Value, key: &str| -> Vec<String> {
            let existing_arr: Vec<String> = existing
                .get("permissions")
                .and_then(|p| p.get(key))
                .and_then(|arr| arr.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();

            let ralph_arr: Vec<String> = ralph
                .get("permissions")
                .and_then(|p| p.get(key))
                .and_then(|arr| arr.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();

            // Deduplicate using HashSet, then convert back to Vec to preserve order
            let existing_set: std::collections::HashSet<_> = existing_arr.iter().collect();
            existing_arr
                .iter()
                .chain(ralph_arr.iter().filter(|s| !existing_set.contains(s)))
                .cloned()
                .collect()
        };

    let allow = merge_array(existing, ralph, "allow");
    let deny = merge_array(existing, ralph, "deny");

    Some((
        "permissions".to_string(),
        serde_json::json!({
            "allow": allow,
            "deny": deny
        }),
    ))
}

/// Extract the string content from a `HarnessConfig` variant.
fn harness_config_content(config: &HarnessConfig) -> String {
    match config {
        HarnessConfig::ClaudeCode(s) | HarnessConfig::OpenCode(s) | HarnessConfig::Codex(s) => {
            s.clone()
        }
        HarnessConfig::Aider(args) => {
            // Serialize args as a JSON array so the caller can reconstruct them.
            serde_json::to_string_pretty(args).unwrap_or_else(|_| "[]".to_string())
        }
    }
}

/// Create the harness directory and write the config file.
fn write_config(
    workspace: &dyn Workspace,
    dir: &str,
    file_path: &str,
    content: &str,
) -> std::io::Result<()> {
    workspace.create_dir_all(Path::new(dir))?;
    workspace.write(Path::new(file_path), content)
}

fn shell_escape_posix(s: &str) -> String {
    let inner: String = s
        .chars()
        .flat_map(|ch| {
            if ch == '\'' {
                "'\"'\"'".chars().collect::<Vec<_>>()
            } else {
                vec![ch]
            }
        })
        .collect();
    format!("'{inner}'")
}

#[cfg(test)]
mod tests;
