use super::common::TestFixture;
use crate::config::types::{CloudConfig, GitAuthMethod, GitRemoteConfig};
use crate::executor::MockProcessExecutor;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{CommitEvent, PipelineEvent};
use crate::reducer::ui_event::UIEvent;
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

#[test]
fn handle_create_pull_request_rejects_invalid_title() {
    let mut fixture = TestFixture::new();
    fixture.cloud.enabled = true;
    let ctx = fixture.ctx();

    let result =
        MainEffectHandler::handle_create_pull_request(&ctx, "main", "feature", "   ", "body");

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(CommitEvent::PullRequestFailed { .. })
    ));

    assert!(result.ui_events.iter().any(|event| matches!(
        event,
        UIEvent::PullRequestFailed { error } if error.contains("Non-empty text expected")
    )));

    assert!(fixture.executor.execute_calls_for("gh").is_empty());
    assert!(fixture.executor.execute_calls_for("glab").is_empty());
}

// ---------------------------------------------------------------------------
// Seam tests: handle_create_pull_request executor capability interactions.
// ---------------------------------------------------------------------------

/// Contract 1 + 3: gh CLI is called; success → PullRequestCreated event + UIEvent.
#[test]
fn handle_create_pull_request_gh_success_emits_pull_request_created() {
    // Arrange: gh CLI returns success with a PR URL.
    let executor = Arc::new(
        MockProcessExecutor::new().with_output("gh", "https://github.com/owner/repo/pull/42\n"),
    );
    let mut fixture = TestFixture::new();
    fixture.executor = executor;
    let ctx = fixture.ctx();

    // Act
    let result = MainEffectHandler::handle_create_pull_request(
        &ctx,
        "main",
        "feature/my-branch",
        "Add feature",
        "PR body",
    );

    // Contract 1: gh executor was called.
    let gh_calls = fixture.executor.execute_calls_for("gh");
    assert_eq!(
        gh_calls.len(),
        1,
        "gh CLI must be called exactly once for PR creation"
    );
    let (_cmd, args, _env, _workdir) = &gh_calls[0];
    assert!(
        args.contains(&"pr".to_string()) && args.contains(&"create".to_string()),
        "gh must be called with 'pr create', got: {args:?}"
    );
    assert!(
        args.iter().any(|a| a == "Add feature"),
        "gh args must include the PR title"
    );

    // Contract 3: correct typed event on success.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::PullRequestCreated { ref url, number })
                if url.contains("pull/42") && number == 42
        ),
        "expected PullRequestCreated event with url and number, got: {:?}",
        result.event
    );

    // UI event also emitted.
    assert!(
        result.ui_events.iter().any(|e| matches!(
            e,
            UIEvent::PullRequestCreated { url, number }
                if url.contains("pull/42") && *number == 42
        )),
        "expected PullRequestCreated UIEvent"
    );
}

/// Contract 2: gh not found (IO error) → falls back to glab; glab success → PullRequestCreated.
///
/// The fallback to glab is triggered when `gh` returns an OS-level spawn error (e.g. command
/// not found), not merely a non-zero exit code.  Non-zero exit from a running `gh` is treated
/// as a `PullRequestFailed` directly, without a glab fallback.
#[test]
fn handle_create_pull_request_falls_back_to_glab_when_gh_fails() {
    // Arrange: gh CLI unavailable at OS level; glab returns success.
    let executor = Arc::new(
        MockProcessExecutor::new()
            .with_io_error("gh", std::io::ErrorKind::NotFound, "gh: not found")
            .with_output("glab", "https://gitlab.com/owner/repo/-/merge_requests/7\n"),
    );
    let mut fixture = TestFixture::new();
    fixture.executor = executor;
    let ctx = fixture.ctx();

    // Act
    let result = MainEffectHandler::handle_create_pull_request(
        &ctx,
        "main",
        "feature/branch",
        "My MR",
        "body",
    );

    // Contract 2: glab was invoked after gh failure.
    let glab_calls = fixture.executor.execute_calls_for("glab");
    assert_eq!(
        glab_calls.len(),
        1,
        "glab must be called as fallback when gh fails"
    );
    let (_cmd, args, _env, _workdir) = &glab_calls[0];
    assert!(
        args.contains(&"mr".to_string()) && args.contains(&"create".to_string()),
        "glab must be called with 'mr create', got: {args:?}"
    );

    // Contract 3: success event from glab fallback.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::PullRequestCreated { ref url, number })
                if url.contains("merge_requests/7") && number == 7
        ),
        "expected PullRequestCreated from glab fallback, got: {:?}",
        result.event
    );
}

/// Contract 2: both gh and glab fail → PullRequestFailed with combined error.
#[test]
fn handle_create_pull_request_emits_pull_request_failed_when_both_tools_fail() {
    // Arrange: both gh and glab fail at the OS level (spawn error).
    let executor = Arc::new(
        MockProcessExecutor::new()
            .with_io_error("gh", std::io::ErrorKind::NotFound, "gh: not found")
            .with_io_error("glab", std::io::ErrorKind::NotFound, "glab: not found"),
    );
    let mut fixture = TestFixture::new();
    fixture.executor = executor;
    let ctx = fixture.ctx();

    // Act
    let result = MainEffectHandler::handle_create_pull_request(
        &ctx,
        "main",
        "feature/branch",
        "My PR",
        "body",
    );

    // Contract 2 error path: PullRequestFailed with error describing both failures.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::PullRequestFailed { ref error })
                if error.contains("gh") || error.contains("glab")
        ),
        "expected PullRequestFailed when both gh and glab fail, got: {:?}",
        result.event
    );

    assert!(
        result
            .ui_events
            .iter()
            .any(|e| matches!(e, UIEvent::PullRequestFailed { .. })),
        "expected PullRequestFailed UIEvent"
    );
}
