use super::common::TestFixture;
use crate::config::types::{CloudConfig, GitAuthMethod, GitRemoteConfig};
use crate::executor::MockProcessExecutor;
use crate::reducer::handler::MainEffectHandler;
use std::sync::Arc;

#[test]
fn test_push_to_remote_token_auth_uses_ephemeral_credential_helper() {
    let cloud = CloudConfig {
        enabled: true,
        api_url: Some("https://api.example.com".to_string()),
        api_token: Some("secret".to_string()),
        run_id: Some("run_1".to_string()),
        heartbeat_interval_secs: 30,
        graceful_degradation: true,
        git_remote: GitRemoteConfig {
            auth_method: GitAuthMethod::Token {
                token: "ghp_test".to_string(),
                username: "x-access-token".to_string(),
            },
            push_branch: Some("main".to_string()),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        },
    };

    let mut fixture = TestFixture::new();
    fixture.cloud = cloud;
    let ctx = fixture.ctx();
    let _ = MainEffectHandler::handle_push_to_remote(
        &ctx,
        "origin".to_string(),
        "main".to_string(),
        false,
        "abc123".to_string(),
    );

    let calls = fixture.executor.execute_calls_for("git");
    assert_eq!(calls.len(), 1);
    let (_cmd, args, _env, _workdir) = &calls[0];

    assert!(
        args.iter().any(|a| a == "-c"),
        "expected per-command -c overrides for token auth"
    );
    assert!(
        args.iter().any(|a| a.starts_with("credential.helper=!")),
        "expected ephemeral credential helper for token auth"
    );
    assert!(args.contains(&"push".to_string()));
    assert!(args.contains(&"origin".to_string()));
    assert!(
        args.iter().any(|a| a.contains("refs/heads/main")),
        "expected refspec containing 'refs/heads/main', got {args:?}"
    );
}

#[test]
fn test_push_to_remote_credential_helper_sets_credential_helper_override() {
    let cloud = CloudConfig {
        enabled: true,
        api_url: Some("https://api.example.com".to_string()),
        api_token: Some("secret".to_string()),
        run_id: Some("run_1".to_string()),
        heartbeat_interval_secs: 30,
        graceful_degradation: true,
        git_remote: GitRemoteConfig {
            auth_method: GitAuthMethod::CredentialHelper {
                helper: "gcloud".to_string(),
            },
            push_branch: Some("main".to_string()),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        },
    };

    let mut fixture = TestFixture::new();
    fixture.cloud = cloud;
    let ctx = fixture.ctx();
    let _ = MainEffectHandler::handle_push_to_remote(
        &ctx,
        "origin".to_string(),
        "main".to_string(),
        false,
        "abc123".to_string(),
    );

    let calls = fixture.executor.execute_calls_for("git");
    assert_eq!(calls.len(), 1);
    let (_cmd, args, _env, _workdir) = &calls[0];
    assert!(
        args.iter().any(|a| a == "credential.helper=gcloud"),
        "expected credential.helper override for credential-helper auth"
    );
}

#[test]
fn test_push_to_remote_emits_ui_event_on_success() {
    let cloud = CloudConfig {
        enabled: true,
        api_url: Some("https://api.example.com".to_string()),
        api_token: Some("secret".to_string()),
        run_id: Some("run_1".to_string()),
        heartbeat_interval_secs: 30,
        graceful_degradation: true,
        git_remote: GitRemoteConfig {
            auth_method: GitAuthMethod::SshKey { key_path: None },
            push_branch: Some("main".to_string()),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        },
    };

    let mut fixture = TestFixture::new();
    fixture.cloud = cloud;
    let ctx = fixture.ctx();
    let result = MainEffectHandler::handle_push_to_remote(
        &ctx,
        "origin".to_string(),
        "main".to_string(),
        false,
        "abc123".to_string(),
    );

    assert!(
        result.ui_events.iter().any(|e| matches!(
            e,
            crate::reducer::ui_event::UIEvent::PushCompleted {
                remote,
                branch,
                commit_sha
            } if remote == "origin" && branch == "main" && commit_sha == "abc123"
        )),
        "expected PushCompleted UIEvent"
    );
}

#[test]
fn test_push_to_remote_emits_ui_event_on_failure_with_redacted_error() {
    let cloud = CloudConfig {
        enabled: true,
        api_url: Some("https://api.example.com".to_string()),
        api_token: Some("secret".to_string()),
        run_id: Some("run_1".to_string()),
        heartbeat_interval_secs: 30,
        graceful_degradation: true,
        git_remote: GitRemoteConfig {
            auth_method: GitAuthMethod::SshKey { key_path: None },
            push_branch: Some("main".to_string()),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        },
    };

    let executor = Arc::new(MockProcessExecutor::new().with_error(
        "git",
        "HTTP 401: Bearer SECRET_TOKEN https://user:pass@example.com?access_token=abc",
    ));
    let mut fixture = TestFixture::new();
    fixture.cloud = cloud;
    fixture.executor = executor;
    let ctx = fixture.ctx();
    let result = MainEffectHandler::handle_push_to_remote(
        &ctx,
        "origin".to_string(),
        "main".to_string(),
        false,
        "abc123".to_string(),
    );

    let mut saw = false;
    for e in &result.ui_events {
        if let crate::reducer::ui_event::UIEvent::PushFailed { error, .. } = e {
            assert!(
                !error.contains("SECRET_TOKEN"),
                "should redact token: {error}"
            );
            assert!(
                !error.contains("user:pass"),
                "should redact userinfo: {error}"
            );
            assert!(
                error.contains("<redacted>"),
                "should contain redaction marker: {error}"
            );
            saw = true;
        }
    }
    assert!(saw, "expected PushFailed UIEvent");
}
