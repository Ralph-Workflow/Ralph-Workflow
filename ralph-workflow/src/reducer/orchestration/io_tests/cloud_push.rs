// Cloud mode orchestration tests.
//
// Tests for cloud-specific effect determination:
// - ConfigureGitAuth before first push
// - PushToRemote after commits
// - CreatePullRequest in finalizing phase
// - No cloud effects when disabled

use super::*;
use crate::config::{CloudStateConfig, GitAuthStateMethod, GitRemoteStateConfig};

#[must_use]
fn create_cloud_enabled_state() -> PipelineState {
    let cloud = CloudStateConfig {
        enabled: true,
        api_url: Some("https://api.test.com".to_string()),
        run_id: Some("run_123".to_string()),
        heartbeat_interval_secs: 30,
        graceful_degradation: true,
        git_remote: GitRemoteStateConfig {
            auth_method: GitAuthStateMethod::SshKey { key_path: None },
            push_branch: "main".to_string(),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        },
    };

    let mut state = create_test_state();
    state.cloud = cloud;
    state
}

#[test]
fn test_cloud_disabled_no_push_effects() {
    // When cloud mode is disabled, no push effects should be emitted
    let mut state = create_test_state();
    state.cloud = CloudStateConfig::disabled();
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = false;

    let effect = determine_next_effect(&state);

    // Should not emit ConfigureGitAuth or PushToRemote
    assert!(
        !matches!(effect, Effect::ConfigureGitAuth { .. }),
        "Cloud disabled should not emit ConfigureGitAuth"
    );
    assert!(
        !matches!(effect, Effect::PushToRemote { .. }),
        "Cloud disabled should not emit PushToRemote"
    );
}

#[test]
fn test_cloud_enabled_pending_push_configures_auth_first() {
    // When cloud enabled and commit pending push, configure auth first
    let mut state = create_cloud_enabled_state();
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = false;

    let effect = determine_next_effect(&state);

    match effect {
        Effect::ConfigureGitAuth { auth_method } => {
            assert!(
                auth_method.starts_with("ssh-key:"),
                "Should configure SSH auth"
            );
        }
        other => panic!("Expected ConfigureGitAuth, got: {other:?}"),
    }
}

#[test]
fn test_cloud_enabled_auth_configured_emits_push() {
    // After auth is configured, emit push
    let mut state = create_cloud_enabled_state();
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = true;

    let effect = determine_next_effect(&state);

    match effect {
        Effect::PushToRemote {
            remote,
            branch,
            force,
            commit_sha,
        } => {
            assert_eq!(remote, "origin", "Should push to origin");
            assert_eq!(branch, "main", "Should push to main");
            assert!(!force, "Force push should be disabled by default");
            assert_eq!(commit_sha, "abc123", "Should push the pending commit");
        }
        other => panic!("Expected PushToRemote, got: {other:?}"),
    }
}

#[test]
fn test_cloud_enabled_no_pending_push_no_effects() {
    // When no commit is pending push, no push effects
    let mut state = create_cloud_enabled_state();
    state.pending_push_commit = None;
    state.git_auth_configured = true;
    state.phase = PipelinePhase::Development;

    let effect = determine_next_effect(&state);

    assert!(
        !matches!(effect, Effect::ConfigureGitAuth { .. }),
        "No ConfigureGitAuth when nothing pending"
    );
    assert!(
        !matches!(effect, Effect::PushToRemote { .. }),
        "No PushToRemote when nothing pending"
    );
}

#[test]
fn test_cloud_enabled_create_pr_in_finalizing() {
    // When in Finalizing phase with create_pr enabled, emit CreatePullRequest
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.create_pr = true;
    state.cloud.git_remote.pr_base_branch = Some("main".to_string());
    state.phase = PipelinePhase::Finalizing;
    state.pr_created = false;
    state.pending_push_commit = None; // No pending push
    state.metrics.commits_created_total = 1;
    state.push_count = 1;
    state.last_pushed_commit = Some("abc123".to_string());

    let effect = determine_next_effect(&state);

    match effect {
        Effect::CreatePullRequest {
            base_branch,
            head_branch,
            title,
            body: _,
        } => {
            assert_eq!(base_branch, "main", "Should target main branch");
            assert_eq!(head_branch, "main", "Should use push branch");
            assert!(
                title.contains("Ralph workflow"),
                "Should have default title"
            );
        }
        other => panic!("Expected CreatePullRequest, got: {other:?}"),
    }
}

#[test]
fn test_cloud_enabled_create_pr_renders_title_and_body_templates() {
    // PR title/body templates should be rendered (not passed through verbatim)
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.create_pr = true;
    state.cloud.git_remote.pr_base_branch = Some("main".to_string());
    state.cloud.git_remote.pr_title_template = Some("Ralph changes for {run_id}".to_string());
    state.cloud.git_remote.pr_body_template = Some("Summary: {prompt_summary}".to_string());
    state.phase = PipelinePhase::Finalizing;
    state.pr_created = false;
    state.pending_push_commit = None;
    state.metrics.commits_created_total = 1;
    state.push_count = 1;
    state.last_pushed_commit = Some("abc123".to_string());

    let effect = determine_next_effect(&state);

    match effect {
        Effect::CreatePullRequest { title, body, .. } => {
            assert_eq!(title, "Ralph changes for run_123");
            assert_eq!(body, "Summary: Ralph workflow run run_123");
        }
        other => panic!("Expected CreatePullRequest, got: {other:?}"),
    }
}

#[test]
fn test_cloud_enabled_pr_already_created_no_effect() {
    // When PR already created, don't emit CreatePullRequest again
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.create_pr = true;
    state.phase = PipelinePhase::Finalizing;
    state.pr_created = true;
    state.pending_push_commit = None;

    let effect = determine_next_effect(&state);

    assert!(
        !matches!(effect, Effect::CreatePullRequest { .. }),
        "Should not create PR twice"
    );
}

#[test]
fn test_cloud_enabled_create_pr_is_blocked_when_commits_failed_to_push() {
    // PR creation should not be attempted when there are known unpushed commits.
    // Instead, surface a clear terminal failure so cloud orchestration can react.
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.create_pr = true;
    state.phase = PipelinePhase::Finalizing;
    state.pr_created = false;
    state.pending_push_commit = None;
    state.unpushed_commits = vec!["deadbeef".to_string()];
    state.metrics.commits_created_total = 1;
    state.push_count = 0;
    state.last_pushed_commit = None;

    let effect = determine_next_effect(&state);

    match effect {
        Effect::EmitCompletionMarkerAndTerminate { is_failure, reason } => {
            assert!(
                is_failure,
                "Should terminate as failure when PR is impossible"
            );
            let reason = reason.unwrap_or_default();
            assert!(
                reason.to_ascii_lowercase().contains("push"),
                "Reason should mention push requirement: {reason}"
            );
        }
        other => panic!(
            "Expected EmitCompletionMarkerAndTerminate when unpushed commits exist, got: {other:?}"
        ),
    }
}

#[test]
fn test_cloud_enabled_token_auth_format() {
    // Test token auth formatting in ConfigureGitAuth effect
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.auth_method = GitAuthStateMethod::Token {
        username: "oauth2".to_string(),
    };
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = false;

    let effect = determine_next_effect(&state);

    match effect {
        Effect::ConfigureGitAuth { auth_method } => {
            assert_eq!(auth_method, "token:oauth2", "Should format token auth");
        }
        other => panic!("Expected ConfigureGitAuth, got: {other:?}"),
    }
}

#[test]
fn test_cloud_enabled_credential_helper_format() {
    // Test credential helper formatting
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.auth_method = GitAuthStateMethod::CredentialHelper {
        helper: "gcloud".to_string(),
    };
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = false;

    let effect = determine_next_effect(&state);

    match effect {
        Effect::ConfigureGitAuth { auth_method } => {
            assert_eq!(
                auth_method, "credential-helper:gcloud",
                "Should format credential helper"
            );
        }
        other => panic!("Expected ConfigureGitAuth, got: {other:?}"),
    }
}

#[test]
fn test_cloud_enabled_force_push_when_configured() {
    // Test that force push flag is respected
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.force_push = true;
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = true;

    let effect = determine_next_effect(&state);

    match effect {
        Effect::PushToRemote { force, .. } => {
            assert!(force, "Force push should be enabled when configured");
        }
        other => panic!("Expected PushToRemote, got: {other:?}"),
    }
}

#[test]
fn test_cloud_push_priority_over_phase_effects() {
    // Cloud push should have priority over normal phase effects
    let mut state = create_cloud_enabled_state();
    state.phase = PipelinePhase::Development;
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = true;

    let effect = determine_next_effect(&state);

    // Should emit PushToRemote before any Development phase effects
    assert!(
        matches!(effect, Effect::PushToRemote { .. }),
        "Push should have priority over phase effects"
    );
}

#[test]
fn test_cloud_push_does_not_block_other_priorities() {
    // Higher priority effects (like XSD retry) should still take precedence
    let mut state = create_cloud_enabled_state();
    state.pending_push_commit = Some("abc123".to_string());
    state.git_auth_configured = true;
    state.phase = PipelinePhase::Development;
    state.agent_chain.current_role = AgentRole::Analysis;
    // Set XSD retry pending (higher priority than cloud push)
    state.continuation.xsd_retry_pending = true;
    state.continuation.xsd_retry_count = 0;
    state.continuation.max_xsd_retry_count = 3;

    let effect = determine_next_effect(&state);

    // XSD retry should take precedence over cloud push
    assert!(
        matches!(
            effect,
            Effect::InvokeAnalysisAgent { .. } | Effect::InitializeAgentChain { .. }
        ),
        "XSD retry effects should take precedence over cloud push, got: {effect:?}"
    );
}

#[test]
fn test_cloud_pr_only_in_finalizing_phase() {
    // PR creation should only happen in Finalizing phase
    let mut state = create_cloud_enabled_state();
    state.cloud.git_remote.create_pr = true;
    state.phase = PipelinePhase::Development; // Not Finalizing
    state.pr_created = false;
    state.pending_push_commit = None;

    let effect = determine_next_effect(&state);

    assert!(
        !matches!(effect, Effect::CreatePullRequest { .. }),
        "PR should only be created in Finalizing phase"
    );
}
