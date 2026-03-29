//! Tests for the harness applicator module

use crate::agents::harness::applicator::{
    apply_harness_config, detect_agent_type, merge_claude_settings, AgentType,
    CLAUDE_SETTINGS_LOCAL,
};
use crate::agents::session::{AgentSession, SessionDrain};
use crate::workspace::{memory_workspace::MemoryWorkspace, Workspace};
use std::collections::HashMap;
use std::path::Path;

fn test_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

const TEST_ENDPOINT: &str = "unix:///tmp/ralph-mcp/test.sock";

// -----------------------------------------------------------------------
// detect_agent_type
// -----------------------------------------------------------------------

#[test]
fn detect_claude_bare() {
    assert_eq!(detect_agent_type("claude"), AgentType::Claude);
}

#[test]
fn detect_claude_full_path() {
    assert_eq!(
        detect_agent_type("/usr/local/bin/claude"),
        AgentType::Claude
    );
}

#[test]
fn detect_claude_case_insensitive() {
    assert_eq!(detect_agent_type("Claude"), AgentType::Claude);
}

#[test]
fn detect_opencode() {
    assert_eq!(detect_agent_type("opencode"), AgentType::OpenCode);
}

#[test]
fn detect_aider() {
    assert_eq!(detect_agent_type("aider"), AgentType::Aider);
}

#[test]
fn detect_codex_cli() {
    assert_eq!(detect_agent_type("codex-cli"), AgentType::Codex);
}

#[test]
fn detect_codex_bare() {
    assert_eq!(detect_agent_type("codex"), AgentType::Codex);
}

#[test]
fn detect_ccs_bare() {
    assert_eq!(detect_agent_type("ccs"), AgentType::Claude);
}

#[test]
fn detect_ccs_with_profile() {
    assert_eq!(detect_agent_type("ccs mm"), AgentType::Claude);
}

#[test]
fn detect_ccs_slash_profile() {
    assert_eq!(detect_agent_type("ccs/work"), AgentType::Claude);
}

#[test]
fn detect_ccs_full_path() {
    assert_eq!(detect_agent_type("/usr/local/bin/ccs"), AgentType::Claude);
}

#[test]
fn detect_ccs_case_insensitive() {
    assert_eq!(detect_agent_type("CCS"), AgentType::Claude);
}

#[test]
fn detect_unknown() {
    assert_eq!(detect_agent_type("some-other-tool"), AgentType::Unknown);
}

#[test]
fn detect_empty_string() {
    assert_eq!(detect_agent_type(""), AgentType::Unknown);
}

// -----------------------------------------------------------------------
// apply_harness_config - Claude (merges into settings.local.json)
// -----------------------------------------------------------------------

#[test]
fn apply_claude_writes_session_scoped_settings_and_env() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Claude, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let expected_config_path = format!(
        ".agent/tmp/harness/{}/claude/settings.local.json",
        session.session_id.as_str()
    );
    assert_eq!(
        result.config_path.as_deref(),
        Some(expected_config_path.as_str())
    );
    assert!(ws.was_written(&expected_config_path));
    assert!(!ws.was_written(CLAUDE_SETTINGS_LOCAL));

    assert!(result.extra_env_vars.is_empty(), "no extra env for Claude");
    assert_eq!(result.extra_cmd_args.len(), 2);
    assert_eq!(result.extra_cmd_args[0], "--settings");
    assert!(result.extra_cmd_args[1].contains(&format!(
        ".agent/tmp/harness/{}/claude/settings.local.json",
        session.session_id.as_str()
    )));

    let content = ws
        .read(Path::new(&expected_config_path))
        .expect("read config");
    assert!(content.contains("mcpServers"));
    assert!(content.contains("permissions"));
    assert!(content.contains("--mcp-proxy"));
}

#[test]
fn apply_claude_preserves_existing_settings() {
    let ws = MemoryWorkspace::new_test();
    // Pre-populate with existing user settings
    ws.create_dir_all(Path::new(".claude")).expect("create dir");
    ws.write(
        Path::new(CLAUDE_SETTINGS_LOCAL),
        r#"{
            "env": {"MY_VAR": "my_value"},
            "mcpServers": {
                "my-other-server": {
                    "command": "my-server",
                    "args": [],
                    "env": {}
                }
            },
            "permissions": {
                "allow": ["Bash(npm test)"],
                "deny": ["WebSearch"]
            }
        }"#,
    )
    .expect("write existing");

    let session = test_session();
    let original = ws
        .read(Path::new(CLAUDE_SETTINGS_LOCAL))
        .expect("read original before apply");

    let result = apply_harness_config(AgentType::Claude, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");
    assert!(result.config_path.is_some());

    let expected_config_path = result.config_path.clone().expect("config path");
    let content = ws
        .read(Path::new(&expected_config_path))
        .expect("read merged harness config");
    let parsed: serde_json::Value = serde_json::from_str(&content).expect("valid JSON");

    let unchanged_project_local = ws
        .read(Path::new(CLAUDE_SETTINGS_LOCAL))
        .expect("read project-local config after apply");
    assert_eq!(
        original, unchanged_project_local,
        "project-local settings.local.json must not be modified"
    );

    // User's existing env is preserved
    assert_eq!(parsed["env"]["MY_VAR"].as_str(), Some("my_value"));

    // User's existing MCP server is preserved
    assert!(parsed["mcpServers"]["my-other-server"].is_object());

    // Ralph's MCP server is added
    assert!(parsed["mcpServers"]["ralph"].is_object());
    assert_eq!(
        parsed["mcpServers"]["ralph"]["command"].as_str(),
        Some("ralph")
    );

    // User's existing permissions are preserved
    let allow = parsed["permissions"]["allow"]
        .as_array()
        .expect("allow array");
    let allow_strs: Vec<&str> = allow.iter().filter_map(|v| v.as_str()).collect();
    assert!(allow_strs.contains(&"Bash(npm test)"));
    // Ralph's permissions are added
    assert!(allow_strs.contains(&"mcp__ralph__ralph_submit_artifact"));

    let deny = parsed["permissions"]["deny"]
        .as_array()
        .expect("deny array");
    assert!(deny
        .iter()
        .filter_map(|v| v.as_str())
        .any(|x| x == "WebSearch"));
}

#[test]
fn apply_claude_injects_settings_arg() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Claude, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let extra = &result.extra_cmd_args;
    assert_eq!(extra.len(), 2, "Claude harness should append flag and path");
    assert_eq!(extra[0], "--settings");
    assert!(
        extra[1].contains(&format!(
            ".agent/tmp/harness/{}/claude/settings.local.json",
            session.session_id.as_str()
        )),
        "escaped path should point to session config"
    );
    assert!(extra[1].starts_with('\''), "settings arg must be quoted");
    assert!(extra[1].ends_with('\''), "settings arg must be quoted");
    assert!(result.extra_env_vars.is_empty(), "no extra env for Claude");
}

#[test]
fn apply_claude_no_duplicate_permissions() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();

    // Apply twice
    apply_harness_config(AgentType::Claude, &session, TEST_ENDPOINT, &ws).expect("first apply");
    apply_harness_config(AgentType::Claude, &session, TEST_ENDPOINT, &ws).expect("second apply");

    let expected_config_path = format!(
        ".agent/tmp/harness/{}/claude/settings.local.json",
        session.session_id.as_str()
    );
    let content = ws
        .read(Path::new(&expected_config_path))
        .expect("read config");
    let parsed: serde_json::Value = serde_json::from_str(&content).expect("valid JSON");

    // Count occurrences of ralph_submit_artifact - should be exactly 1
    let allow = parsed["permissions"]["allow"]
        .as_array()
        .expect("allow array");
    let count = allow
        .iter()
        .filter(|v| v.as_str() == Some("mcp__ralph__ralph_submit_artifact"))
        .count();
    assert_eq!(count, 1, "should not duplicate permissions on re-apply");
}

// -----------------------------------------------------------------------
// apply_harness_config - OpenCode
// -----------------------------------------------------------------------

#[test]
fn apply_opencode_writes_config_json() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::OpenCode, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let session_id = session.session_id.as_str();
    let expected_path = format!(".agent/tmp/harness/{session_id}/config.json");
    assert_eq!(result.config_path.as_deref(), Some(expected_path.as_str()));
    assert!(ws.was_written(&expected_path));

    assert!(result.extra_env_vars.contains_key("OPENCODE_CONFIG"));
    assert!(result.extra_cmd_args.is_empty());

    let content = ws.read(Path::new(&expected_path)).expect("read config");
    assert!(content.contains("\"mcp\""));
    assert!(content.contains("\"type\": \"local\""));
    assert!(content.contains("--mcp-proxy"));
}

#[test]
fn apply_opencode_preserves_auth_env() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::OpenCode, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let session_id = session.session_id.as_str();
    let expected_relative = format!(".agent/tmp/harness/{session_id}/config.json");
    let expected_config_value = ws
        .absolute(Path::new(&expected_relative))
        .to_string_lossy()
        .into_owned();

    let existing_auth_token = "existing-opencode-token";
    let existing_env = HashMap::from([(
        "OPENCODE_AUTH_TOKEN".to_string(),
        existing_auth_token.to_string(),
    )]);
    let mut merged_env = existing_env.clone();
    merged_env.extend(result.extra_env_vars.clone());

    assert_eq!(
        result.extra_env_vars.get("OPENCODE_CONFIG"),
        Some(&expected_config_value),
        "OPENCODE_CONFIG must point to the harness config",
    );
    assert!(
        !result.extra_env_vars.contains_key("OPENCODE_AUTH_TOKEN"),
        "harness must not override existing OpenCode auth state",
    );
    assert_eq!(
        merged_env.get("OPENCODE_AUTH_TOKEN").map(String::as_str),
        Some(existing_auth_token),
        "existing OpenCode auth token must survive merging",
    );
    assert_eq!(
        merged_env.get("OPENCODE_CONFIG"),
        Some(&expected_config_value),
        "merged env should still expose the MCP config path",
    );
    assert_eq!(
        result.extra_env_vars.len(),
        1,
        "only the OpenCode config env var should be injected"
    );
    assert_eq!(
        merged_env.len(),
        2,
        "merged env should only contain the auth token and config path"
    );
}

#[test]
fn apply_opencode_preserves_multiple_auth_env_vars() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::OpenCode, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let session_id = session.session_id.as_str();
    let expected_relative = format!(".agent/tmp/harness/{session_id}/config.json");
    let expected_config_value = ws
        .absolute(Path::new(&expected_relative))
        .to_string_lossy()
        .into_owned();

    let existing_env = HashMap::from([
        ("OPENCODE_AUTH_TOKEN".to_string(), "token".to_string()),
        ("OPENCODE_AUTH_METHOD".to_string(), "bearer".to_string()),
    ]);
    let mut merged_env = existing_env.clone();
    merged_env.extend(result.extra_env_vars.clone());

    assert_eq!(
        result.extra_env_vars.len(),
        1,
        "only MCP config is injected"
    );
    assert_eq!(
        merged_env.len(),
        3,
        "merged env includes the auth token, method, and MCP config"
    );
    assert_eq!(
        result.extra_env_vars.get("OPENCODE_CONFIG"),
        Some(&expected_config_value),
        "OPENCODE_CONFIG must point to the harness config",
    );
    assert_eq!(
        merged_env.get("OPENCODE_AUTH_TOKEN").map(String::as_str),
        Some("token"),
        "auth token preserved"
    );
    assert_eq!(
        merged_env.get("OPENCODE_AUTH_METHOD").map(String::as_str),
        Some("bearer"),
        "auth method preserved"
    );
}

#[test]
fn apply_opencode_preserves_config_dir_env() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::OpenCode, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let session_id = session.session_id.as_str();
    let expected_relative = format!(".agent/tmp/harness/{session_id}/config.json");
    let expected_config_value = ws
        .absolute(Path::new(&expected_relative))
        .to_string_lossy()
        .into_owned();

    let existing_env = HashMap::from([
        (
            "OPENCODE_CONFIG_DIR".to_string(),
            "/home/user/.opencode".to_string(),
        ),
        ("OPENCODE_AUTH_TOKEN".to_string(), "token".to_string()),
    ]);
    let mut merged_env = existing_env.clone();
    merged_env.extend(result.extra_env_vars.clone());

    assert_eq!(result.extra_env_vars.len(), 1);
    assert_eq!(merged_env.len(), 3);
    assert_eq!(
        result.extra_env_vars.get("OPENCODE_CONFIG"),
        Some(&expected_config_value)
    );
    assert_eq!(
        merged_env.get("OPENCODE_CONFIG_DIR").map(String::as_str),
        Some("/home/user/.opencode")
    );
    assert_eq!(
        merged_env.get("OPENCODE_AUTH_TOKEN").map(String::as_str),
        Some("token")
    );
}

// -----------------------------------------------------------------------
// apply_harness_config - Aider
// -----------------------------------------------------------------------

#[test]
fn apply_aider_writes_args_json() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Aider, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let session_id = session.session_id.as_str();
    let expected_path = format!(".agent/tmp/harness/{session_id}/args.json");
    assert_eq!(result.config_path.as_deref(), Some(expected_path.as_str()));
    assert!(ws.was_written(&expected_path));
    assert!(result.extra_env_vars.is_empty());

    let content = ws.read(Path::new(&expected_path)).expect("read config");
    assert!(content.contains("--no-commit"));
}

// -----------------------------------------------------------------------
// apply_harness_config - Codex
// -----------------------------------------------------------------------

#[test]
fn apply_codex_writes_config_json() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Codex, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let session_id = session.session_id.as_str();
    let expected_path = format!(".agent/tmp/harness/{session_id}/config.toml");
    assert_eq!(result.config_path.as_deref(), Some(expected_path.as_str()));
    assert!(ws.was_written(&expected_path));
    assert!(result.extra_env_vars.is_empty());
    assert!(result.extra_cmd_args.len() >= 8);
    assert_eq!(result.extra_cmd_args[0], "-c");

    let content = ws.read(Path::new(&expected_path)).expect("read config");
    assert!(content.contains("[mcp_servers.ralph]"));
    assert!(content.contains("--mcp-proxy"));
}

#[test]
fn apply_codex_preserves_auth_env() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Codex, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let existing_auth_token = "existing-codex-token";
    let existing_env = HashMap::from([(
        "CODEX_AUTH_TOKEN".to_string(),
        existing_auth_token.to_string(),
    )]);
    let mut merged_env = existing_env.clone();
    merged_env.extend(result.extra_env_vars.clone());

    assert!(result.extra_env_vars.is_empty());
    assert!(
        !result.extra_env_vars.contains_key("CODEX_AUTH_TOKEN"),
        "harness must not override existing Codex auth state",
    );
    assert_eq!(
        merged_env.get("CODEX_AUTH_TOKEN").map(String::as_str),
        Some(existing_auth_token),
        "existing Codex auth token must survive merging",
    );
    assert_eq!(
        result.extra_env_vars.len(),
        0,
        "Codex MCP config is injected via CLI -c overrides",
    );
    assert_eq!(
        merged_env.len(),
        1,
        "merged env should only contain the existing auth token"
    );
}

#[test]
fn apply_codex_preserves_multiple_auth_env_vars() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Codex, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let existing_env = HashMap::from([
        ("CODEX_AUTH_TOKEN".to_string(), "token".to_string()),
        ("CODEX_AUTH_METHOD".to_string(), "api_key".to_string()),
    ]);
    let mut merged_env = existing_env.clone();
    merged_env.extend(result.extra_env_vars.clone());

    assert_eq!(
        result.extra_env_vars.len(),
        0,
        "Codex MCP config is injected via CLI -c overrides"
    );
    assert_eq!(
        merged_env.len(),
        2,
        "merged env keeps both auth entries unchanged"
    );
    assert_eq!(
        merged_env.get("CODEX_AUTH_TOKEN").map(String::as_str),
        Some("token"),
        "auth token preserved"
    );
    assert_eq!(
        merged_env.get("CODEX_AUTH_METHOD").map(String::as_str),
        Some("api_key"),
        "auth method preserved"
    );
}

#[test]
fn apply_codex_preserves_config_dir_env() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Codex, &session, TEST_ENDPOINT, &ws)
        .expect("should succeed");

    let existing_env = HashMap::from([
        (
            "CODEX_CONFIG_DIR".to_string(),
            "/home/user/.codex".to_string(),
        ),
        ("CODEX_AUTH_TOKEN".to_string(), "token".to_string()),
    ]);
    let mut merged_env = existing_env.clone();
    merged_env.extend(result.extra_env_vars.clone());

    assert_eq!(result.extra_env_vars.len(), 0);
    assert_eq!(merged_env.len(), 2);
    assert_eq!(
        merged_env.get("CODEX_CONFIG_DIR").map(String::as_str),
        Some("/home/user/.codex")
    );
    assert_eq!(
        merged_env.get("CODEX_AUTH_TOKEN").map(String::as_str),
        Some("token")
    );
}

#[test]
fn apply_unknown_returns_error() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();
    let result = apply_harness_config(AgentType::Unknown, &session, TEST_ENDPOINT, &ws);
    assert!(result.is_err());
}

// -----------------------------------------------------------------------
// merge_claude_settings unit tests
// -----------------------------------------------------------------------

#[test]
fn merge_empty_existing_gets_ralph_config() {
    let existing = serde_json::json!({});
    let ralph = serde_json::json!({
        "mcpServers": {"ralph": {"command": "ralph"}},
        "permissions": {"allow": ["tool_a"], "deny": ["tool_b"]}
    });
    let merged = merge_claude_settings(&existing, &ralph);
    assert!(merged["mcpServers"]["ralph"].is_object());
    assert_eq!(merged["permissions"]["allow"][0].as_str(), Some("tool_a"));
}

#[test]
fn merge_preserves_unrelated_fields() {
    let existing = serde_json::json!({
        "env": {"FOO": "bar"},
        "custom_field": 42
    });
    let ralph = serde_json::json!({
        "mcpServers": {"ralph": {"command": "ralph"}}
    });
    let merged = merge_claude_settings(&existing, &ralph);
    assert_eq!(merged["env"]["FOO"].as_str(), Some("bar"));
    assert_eq!(merged["custom_field"].as_i64(), Some(42));
}

// -----------------------------------------------------------------------
// Integration: detect + apply round-trip
// -----------------------------------------------------------------------

/// Regression test: CCS agents MUST get harness config with mcpServers.
///
/// CCS (Claude Code Switch) wraps Claude Code with different profiles/providers.
/// Without this, CCS agents run without `ralph_submit_artifact` and the pipeline
/// silently fails to extract artifacts.
#[test]
fn apply_ccs_agent_gets_mcp_config() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();

    // CCS agents must be detected as Claude and get full harness config
    let agent_type = detect_agent_type("ccs mm");
    assert_eq!(
        agent_type,
        AgentType::Claude,
        "CCS must be detected as Claude"
    );

    let result = apply_harness_config(agent_type, &session, TEST_ENDPOINT, &ws)
        .expect("harness config should succeed for CCS");

    // Must write session-scoped harness config with mcpServers
    assert!(
        result.config_path.is_some(),
        "CCS agent must get config path"
    );
    assert!(result.extra_env_vars.is_empty(), "no extra env for Claude");
    assert_eq!(result.extra_cmd_args.len(), 2);
    assert_eq!(result.extra_cmd_args[0], "--settings");
    assert!(result.extra_cmd_args[1].contains(".agent/tmp/harness"));
    assert!(
        !ws.was_written(CLAUDE_SETTINGS_LOCAL),
        "CCS harness must not mutate project .claude/settings.local.json"
    );
    let config_path = result.config_path.as_deref().expect("config path");
    let content = ws
        .read(Path::new(config_path))
        .expect("session-scoped harness settings must be written");
    assert!(
        content.contains("mcpServers"),
        "CCS harness MUST include mcpServers but got: {content}"
    );
    assert!(
        content.contains("ralph_submit_artifact"),
        "CCS harness MUST include ralph_submit_artifact permission"
    );
    assert!(
        content.contains("--mcp-proxy"),
        "CCS harness MUST configure ralph MCP proxy"
    );
}

#[test]
fn round_trip_detect_and_apply() {
    let ws = MemoryWorkspace::new_test();
    let session = test_session();

    for (cmd, expected_type) in [
        ("claude", AgentType::Claude),
        ("ccs", AgentType::Claude),
        ("ccs/mm", AgentType::Claude),
        ("ccs work", AgentType::Claude),
        ("/opt/bin/opencode", AgentType::OpenCode),
        ("aider", AgentType::Aider),
        ("codex-cli", AgentType::Codex),
        ("mystery-agent", AgentType::Unknown),
    ] {
        let agent_type = detect_agent_type(cmd);
        assert_eq!(agent_type, expected_type, "cmd={cmd}");

        match agent_type {
            AgentType::Claude => {
                let result = apply_harness_config(agent_type, &session, TEST_ENDPOINT, &ws)
                    .expect("should succeed");
                assert!(result.config_path.is_some());
                assert!(result.extra_env_vars.is_empty());
                assert_eq!(result.extra_cmd_args.len(), 2);
                assert_eq!(result.extra_cmd_args[0], "--settings");
            }
            AgentType::Unknown => {
                assert!(apply_harness_config(agent_type, &session, TEST_ENDPOINT, &ws).is_err());
            }
            _ => {
                let result = apply_harness_config(agent_type, &session, TEST_ENDPOINT, &ws)
                    .expect("should succeed");
                assert!(result.config_path.is_some());
            }
        }
    }
}
