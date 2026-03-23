//! Tests for git push flow in cloud mode.
//!
//! These tests verify that cloud mode correctly sequences git operations:
//! - `ConfigureGitAuth` effect is emitted before first push
//! - `PushToRemote` effect is emitted after each commit
//! - Push failures are handled gracefully (don't halt pipeline)
//! - PR creation works in finalizing phase
//! - Multiple commits result in multiple pushes
//!
//! All tests use reducer-level testing (`PipelineState`, effects, events)
//! to verify orchestration logic without requiring full pipeline execution.

use ralph_workflow::config::{CloudConfig, CloudStateConfig, GitAuthMethod, GitRemoteConfig};
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::event::{CommitEvent, PipelineEvent, ProcessExecutionResult};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::PipelineState;

use crate::common::IntegrationFixture;
use crate::test_timeout::with_default_timeout;

#[test]
fn test_git_remote_config_ssh_auth() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_GIT_AUTH_METHOD", "ssh"),
            ("RALPH_GIT_SSH_KEY_PATH", "/root/.ssh/id_rsa"),
        ];
        let config = GitRemoteConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        match config.auth_method {
            GitAuthMethod::SshKey { key_path } => {
                assert_eq!(
                    key_path.as_deref(),
                    Some("/root/.ssh/id_rsa"),
                    "SSH key path should be loaded"
                );
            }
            _ => panic!("Expected SshKey auth method"),
        }
    });
}

#[test]
fn test_git_remote_config_token_auth() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_GIT_AUTH_METHOD", "token"),
            ("RALPH_GIT_TOKEN", "ghp_test_token_123"),
            ("RALPH_GIT_TOKEN_USERNAME", "oauth2"),
        ];
        let config = GitRemoteConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        match config.auth_method {
            GitAuthMethod::Token { token, username } => {
                assert_eq!(token, "ghp_test_token_123", "Token should be loaded");
                assert_eq!(username, "oauth2", "Username should be loaded");
            }
            _ => panic!("Expected Token auth method"),
        }
    });
}

#[test]
fn test_git_remote_config_credential_helper() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_GIT_AUTH_METHOD", "credential-helper"),
            ("RALPH_GIT_CREDENTIAL_HELPER", "gcloud"),
        ];
        let config = GitRemoteConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        match config.auth_method {
            GitAuthMethod::CredentialHelper { helper } => {
                assert_eq!(helper, "gcloud", "Credential helper should be loaded");
            }
            _ => panic!("Expected CredentialHelper auth method"),
        }
    });
}

#[test]
fn test_git_remote_config_defaults() {
    with_default_timeout(|| {
        // Empty env — verify defaults
        let config = GitRemoteConfig::from_env_fn(|_| None);

        assert_eq!(
            config.remote_name, "origin",
            "Remote should default to origin"
        );
        assert!(
            !config.create_pr,
            "PR creation should be disabled by default"
        );
        assert!(
            !config.force_push,
            "Force push should be disabled by default"
        );
        assert!(
            config.push_branch.is_none(),
            "Push branch should be None by default"
        );
    });
}

#[test]
fn test_git_remote_config_pr_creation() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_GIT_CREATE_PR", "true"),
            ("RALPH_GIT_PR_BASE_BRANCH", "main"),
            ("RALPH_GIT_PR_TITLE", "Ralph changes for {run_id}"),
        ];
        let config = GitRemoteConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        assert!(config.create_pr, "PR creation should be enabled");
        assert_eq!(
            config.pr_base_branch.as_deref(),
            Some("main"),
            "PR base branch should be loaded"
        );
        assert_eq!(
            config.pr_title_template.as_deref(),
            Some("Ralph changes for {run_id}"),
            "PR title template should be loaded"
        );
    });
}

#[test]
fn test_git_remote_config_custom_remote() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_GIT_REMOTE", "upstream"),
            ("RALPH_GIT_PUSH_BRANCH", "feature/cloud-changes"),
        ];
        let config = GitRemoteConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        assert_eq!(
            config.remote_name, "upstream",
            "Custom remote name should be used"
        );
        assert_eq!(
            config.push_branch.as_deref(),
            Some("feature/cloud-changes"),
            "Custom push branch should be loaded"
        );
    });
}

#[test]
fn test_force_push_disabled_by_default() {
    with_default_timeout(|| {
        // Empty env — force push should be disabled
        let config = GitRemoteConfig::from_env_fn(|_| None);

        assert!(
            !config.force_push,
            "Force push should be disabled by default for safety"
        );
    });
}

#[test]
fn test_force_push_can_be_enabled() {
    with_default_timeout(|| {
        let config = GitRemoteConfig::from_env_fn(|k| match k {
            "RALPH_GIT_FORCE_PUSH" => Some("true".to_string()),
            _ => None,
        });

        assert!(
            config.force_push,
            "Force push should be enabled when explicitly set"
        );
    });
}

#[test]
fn test_pipeline_state_has_cloud_fields() {
    with_default_timeout(|| {
        let cloud_config = CloudStateConfig::disabled();
        let mut state = PipelineState::initial(1, 0);
        state.cloud = cloud_config;

        assert!(!state.cloud.enabled, "Cloud should be disabled");
        assert!(
            state.pending_push_commit.is_none(),
            "No pending push when disabled"
        );
        assert!(
            !state.git_auth_configured,
            "Git auth not configured initially"
        );
        assert!(!state.pr_created, "PR not created initially");
        assert!(state.pr_url.is_none(), "No PR URL initially");
        assert_eq!(state.push_count, 0, "Push count starts at zero");
    });
}

#[test]
fn test_git_auth_configured_event_updates_state() {
    with_default_timeout(|| {
        let cloud_config = CloudStateConfig::disabled();
        let mut state = PipelineState::initial(1, 0);
        state.cloud = cloud_config;
        state.git_auth_configured = false;

        let event = PipelineEvent::Commit(CommitEvent::GitAuthConfigured);
        let new_state = ralph_workflow::reducer::reduce(state, event);

        assert!(
            new_state.git_auth_configured,
            "Git auth should be marked as configured after event"
        );
    });
}

#[test]
fn test_push_completed_clears_pending_push() {
    with_default_timeout(|| {
        let cloud_config = CloudStateConfig::disabled();
        let mut state = PipelineState::initial(1, 0);
        state.cloud = cloud_config;
        state.pending_push_commit = Some("abc123".to_string());
        state.push_count = 0;

        let event = PipelineEvent::Commit(CommitEvent::PushExecuted {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            commit_sha: "abc123".to_string(),
            result: ProcessExecutionResult {
                exit_code: 0,
                stdout: String::new(),
                stderr: String::new(),
            },
        });
        let new_state = ralph_workflow::reducer::reduce(state, event);

        assert!(
            new_state.pending_push_commit.is_none(),
            "Pending push should be cleared after successful push"
        );
        assert_eq!(new_state.push_count, 1, "Push count should increment");
    });
}

#[test]
fn test_push_failed_keeps_pending_push_for_retry() {
    with_default_timeout(|| {
        let cloud_config = CloudStateConfig::disabled();
        let mut state = PipelineState::initial(1, 0);
        state.cloud = cloud_config;
        state.pending_push_commit = Some("abc123".to_string());
        state.push_count = 0;

        let event = PipelineEvent::Commit(CommitEvent::PushFailed {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            error: "Network timeout".to_string(),
        });
        let new_state = ralph_workflow::reducer::reduce(state, event);

        assert_eq!(
            new_state.pending_push_commit.as_deref(),
            Some("abc123"),
            "Push failures must not clear pending push so orchestration can retry"
        );
        assert_eq!(
            new_state.push_count, 0,
            "Push count should not change on failure"
        );
    });
}

#[test]
fn test_pr_created_event_updates_state() {
    with_default_timeout(|| {
        let cloud_config = CloudStateConfig::disabled();
        let mut state = PipelineState::initial(1, 0);
        state.cloud = cloud_config;
        state.pr_created = false;

        let event = PipelineEvent::Commit(CommitEvent::PullRequestCreated {
            url: "https://github.com/user/repo/pull/123".to_string(),
            number: 123,
        });
        let new_state = ralph_workflow::reducer::reduce(state, event);

        assert!(new_state.pr_created, "PR should be marked as created");
        assert_eq!(
            new_state.pr_url.as_deref(),
            Some("https://github.com/user/repo/pull/123"),
            "PR URL should be stored"
        );
    });
}

#[test]
fn test_push_to_remote_effect_pushes_head_to_named_remote_branch() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::effect::{Effect, EffectHandler};
        use ralph_workflow::reducer::handler::MainEffectHandler;

        let mut fixture = IntegrationFixture::new();
        fixture.cloud = CloudConfig {
            enabled: true,
            api_url: Some("https://example.com".to_string()),
            api_token: Some("token".to_string()),
            run_id: Some("run-123".to_string()),
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteConfig {
                auth_method: GitAuthMethod::SshKey { key_path: None },
                push_branch: None,
                create_pr: false,
                pr_title_template: None,
                pr_body_template: None,
                pr_base_branch: None,
                force_push: false,
                remote_name: "origin".to_string(),
            },
        };

        let mut ctx = fixture.ctx(None);

        let state = PipelineState::initial(1, 0);
        let mut handler = MainEffectHandler::new(state);

        let effect = Effect::PushToRemote {
            remote: "origin".to_string(),
            branch: "feature/run-123".to_string(),
            force: false,
            commit_sha: "abc123".to_string(),
        };

        let _res = handler
            .execute(effect, &mut ctx)
            .expect("effect execution should succeed");

        let calls = fixture.executor.execute_calls_for("git");
        assert_eq!(calls.len(), 1, "Should execute exactly one git command"); // OK: content checked below

        let (_cmd, args, _env, _workdir) = &calls[0];

        let expected_refspec = "HEAD:refs/heads/feature/run-123".to_string();
        assert!(
            args.iter().any(|a| a == &expected_refspec),
            "git push should use an explicit HEAD refspec to avoid requiring a local branch"
        );
        assert!(
            !args.iter().any(|a| a == "feature/run-123"),
            "git push should not reference a local branch by name"
        );
    });
}

fn orchestration_ready_state() -> PipelineState {
    let mut state = PipelineState::initial(1, 0);
    state.prompt_permissions.locked = true;
    state.gitignore_entries_ensured = true;
    state.context_cleaned = true;
    state
}

#[test]
fn test_orchestration_cloud_disabled_never_emits_push_or_pr_effects() {
    with_default_timeout(|| {
        let mut state = orchestration_ready_state();
        state.cloud = CloudStateConfig::disabled();
        state.pending_push_commit = Some("abc123".to_string());
        state.git_auth_configured = false;
        state.phase = PipelinePhase::Finalizing;
        state.cloud.git_remote.create_pr = true;

        let effect = determine_next_effect(&state);

        assert!(
            !matches!(effect, Effect::ConfigureGitAuth { .. }),
            "Cloud disabled must not emit ConfigureGitAuth"
        );
        assert!(
            !matches!(effect, Effect::PushToRemote { .. }),
            "Cloud disabled must not emit PushToRemote"
        );
        assert!(
            !matches!(effect, Effect::CreatePullRequest { .. }),
            "Cloud disabled must not emit CreatePullRequest"
        );
    });
}

#[test]
fn test_orchestration_cloud_enabled_sequences_auth_then_push() {
    with_default_timeout(|| {
        let mut state = orchestration_ready_state();
        state.cloud.enabled = true;
        state.cloud.run_id = Some("run_123".to_string());
        state.cloud.git_remote.push_branch = "feature/run-123".to_string();
        state.pending_push_commit = Some("abc123".to_string());
        state.git_auth_configured = false;

        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::ConfigureGitAuth { .. }),
            "Should configure auth before pushing"
        );

        state.git_auth_configured = true;
        let effect = determine_next_effect(&state);

        match effect {
            Effect::PushToRemote {
                remote,
                branch,
                force: _,
                commit_sha,
            } => {
                assert_eq!(remote, "origin");
                assert_eq!(branch, "feature/run-123");
                assert_eq!(commit_sha, "abc123");
            }
            other => panic!("Expected PushToRemote, got: {other:?}"),
        }
    });
}

#[test]
fn test_orchestration_cloud_enabled_creates_pr_in_finalizing_when_configured() {
    with_default_timeout(|| {
        let mut state = orchestration_ready_state();
        state.cloud.enabled = true;
        state.cloud.run_id = Some("run_123".to_string());
        state.cloud.git_remote.push_branch = "feature/run-123".to_string();
        state.cloud.git_remote.create_pr = true;
        state.cloud.git_remote.pr_base_branch = Some("main".to_string());
        state.cloud.git_remote.pr_title_template = Some("Ralph changes for {run_id}".to_string());
        state.cloud.git_remote.pr_body_template = Some("Summary: {prompt_summary}".to_string());

        state.phase = PipelinePhase::Finalizing;
        state.pr_created = false;
        state.pending_push_commit = None;
        state.push_count = 1;
        state.last_pushed_commit = Some("abc123".to_string());
        state.metrics.commits_created_total = 1;

        let effect = determine_next_effect(&state);

        match effect {
            Effect::CreatePullRequest {
                base_branch,
                head_branch,
                title,
                body,
            } => {
                assert_eq!(base_branch, "main");
                assert_eq!(head_branch, "feature/run-123");
                assert_eq!(title, "Ralph changes for run_123");
                assert_eq!(body, "Summary: Ralph workflow run run_123");
            }
            other => panic!("Expected CreatePullRequest, got: {other:?}"),
        }
    });
}
