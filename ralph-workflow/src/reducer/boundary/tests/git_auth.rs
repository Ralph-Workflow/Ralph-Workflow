//! Seam tests for `handle_configure_git_auth` boundary function.
//!
//! Contract assertions:
//! 1. SSH-key path: `env.configure_git_ssh_command` capability is called with the key path.
//! 2. Invalid SSH key path: capability call fails gracefully; `GitAuthConfigured` still emitted.
//! 3. Token auth: `env.disable_git_terminal_prompt` capability is called.
//! 4. All auth methods emit `CommitEvent::GitAuthConfigured` on success.

use super::common::TestFixture;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{CommitEvent, PipelineEvent};

// ---------------------------------------------------------------------------
// Contract 1: SSH key path → configure_git_ssh_command called.
// ---------------------------------------------------------------------------

#[test]
fn handle_configure_git_auth_calls_configure_ssh_command_for_specific_key() {
    // Arrange
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    // Act: configure auth with specific SSH key path.
    let result =
        MainEffectHandler::handle_configure_git_auth(&ctx, "ssh-key:/home/user/.ssh/id_ed25519");

    // Contract 1: env.configure_git_ssh_command was called with the key path.
    let configured_keys = fixture.git_env.configured_ssh_keys();
    assert_eq!(
        configured_keys.len(),
        1,
        "configure_git_ssh_command must be called exactly once for ssh-key:<path> auth"
    );
    assert!(
        configured_keys[0].contains("id_ed25519"),
        "SSH command must reference the provided key path, got: {:?}",
        configured_keys[0]
    );

    // Contract 4: event emitted regardless.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::GitAuthConfigured)
        ),
        "expected GitAuthConfigured event, got: {:?}",
        result.event
    );
}

// ---------------------------------------------------------------------------
// Contract 2: default SSH key path (no path) → configure_git_ssh_command NOT called.
// ---------------------------------------------------------------------------

#[test]
fn handle_configure_git_auth_does_not_call_configure_ssh_for_default_ssh_key() {
    // Arrange: "default" param means use system SSH agent, no explicit key config.
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    // Act
    let result = MainEffectHandler::handle_configure_git_auth(&ctx, "ssh-key:default");

    // Contract: no SSH command configured for "default" (uses SSH_AUTH_SOCK / ~/.ssh/id_rsa).
    let configured_keys = fixture.git_env.configured_ssh_keys();
    assert!(
        configured_keys.is_empty(),
        "configure_git_ssh_command must NOT be called for ssh-key:default"
    );

    // Contract 4: event always emitted.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::GitAuthConfigured)
        ),
        "expected GitAuthConfigured event, got: {:?}",
        result.event
    );
}

// ---------------------------------------------------------------------------
// Contract 2 (error): invalid SSH key path → capability error handled gracefully.
// ---------------------------------------------------------------------------

#[test]
fn handle_configure_git_auth_emits_git_auth_configured_even_when_ssh_key_path_invalid() {
    // Arrange: empty SSH key path causes GitEnvError in MockGitEnvironment.
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    // An empty path after the "ssh-key:" prefix triggers validation error.
    let result = MainEffectHandler::handle_configure_git_auth(&ctx, "ssh-key:");

    // Contract 2: error handled gracefully — capability failure must NOT propagate.
    // Contract 4: GitAuthConfigured still emitted so pipeline can continue.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::GitAuthConfigured)
        ),
        "GitAuthConfigured must be emitted even when SSH key validation fails, got: {:?}",
        result.event
    );
}

// ---------------------------------------------------------------------------
// Contract 3: token auth → disable_git_terminal_prompt called.
// ---------------------------------------------------------------------------

#[test]
fn handle_configure_git_auth_disables_terminal_prompt_for_token_auth() {
    // Arrange
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    // Act: token auth should disable terminal prompts so git doesn't block waiting for input.
    let result = MainEffectHandler::handle_configure_git_auth(&ctx, "token:x-access-token");

    // Contract 3: env.disable_git_terminal_prompt capability was called.
    assert!(
        fixture.git_env.terminal_prompt_disabled(),
        "disable_git_terminal_prompt must be called for token auth to prevent interactive prompts"
    );

    // Contract 4: event emitted.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::GitAuthConfigured)
        ),
        "expected GitAuthConfigured event, got: {:?}",
        result.event
    );
}

// ---------------------------------------------------------------------------
// Contract 3 (credential-helper): disable_git_terminal_prompt called.
// ---------------------------------------------------------------------------

#[test]
fn handle_configure_git_auth_disables_terminal_prompt_for_credential_helper() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let result =
        MainEffectHandler::handle_configure_git_auth(&ctx, "credential-helper:osxkeychain");

    assert!(
        fixture.git_env.terminal_prompt_disabled(),
        "disable_git_terminal_prompt must be called for credential-helper auth"
    );

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(CommitEvent::GitAuthConfigured)
    ));
}
