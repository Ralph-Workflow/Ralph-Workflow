use crate::config::{CloudStateConfig, GitAuthStateMethod, GitRemoteStateConfig};
use crate::reducer::event::CommitEvent;
use crate::reducer::{reduce, PipelineEvent};

#[test]
fn test_push_failed_keeps_pending_push_commit_for_retry() {
    let mut state = super::create_test_state();
    state.cloud = CloudStateConfig {
        enabled: true,
        api_url: Some("https://api.example.com".to_string()),
        run_id: Some("run_1".to_string()),
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
    state.pending_push_commit = Some("abc123".to_string());

    let next = reduce(
        state,
        PipelineEvent::Commit(CommitEvent::PushFailed {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            error: "boom".to_string(),
        }),
    );

    assert_eq!(
        next.pending_push_commit.as_deref(),
        Some("abc123"),
        "Push failures must not clear pending push commit; reducer should allow retry"
    );
}

#[test]
fn test_push_failed_eventually_records_unpushed_commit_and_clears_pending() {
    let mut state = super::create_test_state();
    state.cloud = CloudStateConfig {
        enabled: true,
        api_url: Some("https://api.example.com".to_string()),
        run_id: Some("run_1".to_string()),
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
    state.pending_push_commit = Some("abc123".to_string());

    for i in 0..3 {
        state = reduce(
            state,
            PipelineEvent::Commit(CommitEvent::PushFailed {
                remote: "origin".to_string(),
                branch: "main".to_string(),
                error: format!("boom-{i}"),
            }),
        );
    }

    assert!(
        state.pending_push_commit.is_none(),
        "after exhausting push failure budget, reducer should clear pending push so pipeline can proceed"
    );
    assert!(
        state.unpushed_commits.iter().any(|c| c == "abc123"),
        "unpushed commits must be recorded for completion reporting"
    );
}

#[test]
fn test_push_failed_error_is_redacted_before_storing_in_state() {
    let mut state = super::create_test_state();
    state.cloud = CloudStateConfig {
        enabled: true,
        api_url: Some("https://api.example.com".to_string()),
        run_id: Some("run_1".to_string()),
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
    state.pending_push_commit = Some("abc123".to_string());

    let next = reduce(
        state,
        PipelineEvent::Commit(CommitEvent::PushFailed {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            error: "fatal: could not read Username for 'https://token@github.com/org/repo.git': terminal prompts disabled".to_string(),
        }),
    );

    let err = next.last_push_error.expect("stored error");
    assert!(
        !err.contains("token@github.com"),
        "userinfo should be redacted"
    );
    assert!(
        err.contains("<redacted>"),
        "should contain redaction marker"
    );
}
