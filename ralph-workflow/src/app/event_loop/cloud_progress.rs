//! Cloud progress reporting: maps UI events to progress updates for cloud reporting.

use crate::reducer::state::PipelineState;
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;

pub(super) fn safe_cloud_error_string(e: &crate::cloud::types::CloudError) -> String {
    crate::cloud::redaction::redact_secrets(&e.to_string())
}

pub(super) fn report_cloud_progress(
    ctx: &crate::phases::PhaseContext<'_>,
    state: &PipelineState,
    ui_events: &[UIEvent],
) -> Result<()> {
    if let Some(reporter) = ctx.cloud_reporter {
        for ui_event in ui_events {
            if let Some(update) = ui_event_to_progress_update(ui_event, state, ctx.cloud) {
                if let Err(e) = reporter.report_progress(&update) {
                    let error = safe_cloud_error_string(&e);
                    if !ctx.cloud.graceful_degradation {
                        return Err(anyhow::anyhow!("Cloud progress report failed: {error}"));
                    }
                    ctx.logger
                        .warn(&format!("Cloud progress report failed: {error}"));
                }
            }
        }
    }

    Ok(())
}

#[derive(Debug)]
struct ProgressContextFields {
    iteration: Option<u32>,
    total_iterations: Option<u32>,
    review_pass: Option<u32>,
    total_review_passes: Option<u32>,
    previous_phase: Option<String>,
}

impl ProgressContextFields {
    fn from_state(state: &PipelineState) -> Self {
        Self {
            iteration: one_based(state.iteration, state.total_iterations),
            total_iterations: nonzero(state.total_iterations),
            review_pass: one_based(state.reviewer_pass, state.total_reviewer_passes),
            total_review_passes: nonzero(state.total_reviewer_passes),
            previous_phase: state.previous_phase.as_ref().map(|p| format!("{p:?}")),
        }
    }
}

const fn nonzero(v: u32) -> Option<u32> {
    if v == 0 {
        None
    } else {
        Some(v)
    }
}

fn one_based(current_zero_based: u32, total: u32) -> Option<u32> {
    nonzero(total).map(|t| (current_zero_based.saturating_add(1)).min(t))
}

fn phase_transition_progress(
    from: Option<crate::reducer::event::PipelinePhase>,
    to: crate::reducer::event::PipelinePhase,
    fields: &mut ProgressContextFields,
) -> (String, crate::cloud::types::ProgressEventType) {
    let from_str = from.map(|p| format!("{p:?}"));
    let to_str = format!("{to:?}");
    fields.previous_phase.clone_from(&from_str);
    let message = format!(
        "Phase transition: {} -> {}",
        from_str.as_deref().unwrap_or("None"),
        to_str
    );
    (
        message,
        crate::cloud::types::ProgressEventType::PhaseTransition {
            from: from_str,
            to: to_str,
        },
    )
}

fn iteration_progress(
    current: u32,
    total: u32,
    fields: &mut ProgressContextFields,
) -> (String, crate::cloud::types::ProgressEventType) {
    fields.iteration = Some(current);
    fields.total_iterations = Some(total);
    (
        format!("Development iteration {current}/{total}"),
        crate::cloud::types::ProgressEventType::IterationProgress { current, total },
    )
}

fn review_progress(
    pass: u32,
    total: u32,
    fields: &mut ProgressContextFields,
) -> (String, crate::cloud::types::ProgressEventType) {
    fields.review_pass = Some(pass);
    fields.total_review_passes = Some(total);
    (
        format!("Review pass {pass}/{total}"),
        crate::cloud::types::ProgressEventType::ReviewProgress { pass, total },
    )
}

fn agent_activity_progress(agent: &str) -> (String, crate::cloud::types::ProgressEventType) {
    (
        format!("Agent {agent}: activity"),
        crate::cloud::types::ProgressEventType::AgentInvoked {
            role: "Agent".to_string(),
            agent: agent.to_string(),
        },
    )
}

fn push_completed_progress(
    remote: &str,
    branch: &str,
    commit_sha: &str,
) -> (String, crate::cloud::types::ProgressEventType) {
    let short = &commit_sha[..7.min(commit_sha.len())];
    (
        format!("Push completed: {short} -> {remote}/{branch}"),
        crate::cloud::types::ProgressEventType::PushCompleted {
            remote: remote.to_string(),
            branch: branch.to_string(),
        },
    )
}

fn push_failed_progress(
    remote: &str,
    branch: &str,
    error: &str,
) -> (String, crate::cloud::types::ProgressEventType) {
    let error = crate::cloud::redaction::redact_secrets(error);
    (
        format!("Push failed: {remote}/{branch}: {error}"),
        crate::cloud::types::ProgressEventType::PushFailed {
            remote: remote.to_string(),
            branch: branch.to_string(),
            error,
        },
    )
}

fn pull_request_created_progress(
    url: &str,
    number: u32,
) -> (String, crate::cloud::types::ProgressEventType) {
    let message = if number > 0 {
        format!("PR created #{number}: {url}")
    } else {
        format!("PR created: {url}")
    };
    (
        message,
        crate::cloud::types::ProgressEventType::PullRequestCreated {
            url: url.to_string(),
            number,
        },
    )
}

fn pull_request_failed_progress(error: &str) -> (String, crate::cloud::types::ProgressEventType) {
    let error = crate::cloud::redaction::redact_secrets(error);
    (
        format!("PR creation failed: {error}"),
        crate::cloud::types::ProgressEventType::PullRequestFailed { error },
    )
}

fn map_ui_event_to_progress(
    ui_event: &UIEvent,
    fields: &mut ProgressContextFields,
) -> Option<(String, crate::cloud::types::ProgressEventType)> {
    match ui_event {
        UIEvent::PhaseTransition { from, to } => {
            Some(phase_transition_progress(*from, *to, fields))
        }
        UIEvent::IterationProgress { current, total } => {
            Some(iteration_progress(*current, *total, fields))
        }
        UIEvent::ReviewProgress { pass, total } => Some(review_progress(*pass, *total, fields)),
        UIEvent::AgentActivity {
            agent,
            message: _activity_msg,
        } => Some(agent_activity_progress(agent)),
        UIEvent::PushCompleted {
            remote,
            branch,
            commit_sha,
        } => Some(push_completed_progress(remote, branch, commit_sha)),
        UIEvent::PushFailed {
            remote,
            branch,
            error,
        } => Some(push_failed_progress(remote, branch, error)),
        UIEvent::PullRequestCreated { url, number } => {
            Some(pull_request_created_progress(url, *number))
        }
        UIEvent::PullRequestFailed { error } => Some(pull_request_failed_progress(error)),
        UIEvent::XmlOutput { .. } => None,
    }
}

/// Convert a UI event to a progress update for cloud reporting.
///
/// Returns None for events that don't warrant cloud progress updates.
fn ui_event_to_progress_update(
    ui_event: &UIEvent,
    state: &PipelineState,
    cloud: &crate::config::CloudConfig,
) -> Option<crate::cloud::types::ProgressUpdate> {
    use crate::cloud::types::ProgressUpdate;

    let _run_id = cloud.run_id.clone()?;
    let mut fields = ProgressContextFields::from_state(state);
    let (message, event_type) = map_ui_event_to_progress(ui_event, &mut fields)?;

    Some(ProgressUpdate {
        timestamp: chrono::Utc::now().to_rfc3339(),
        phase: format!("{:?}", state.phase),
        previous_phase: fields.previous_phase,
        iteration: fields.iteration,
        total_iterations: fields.total_iterations,
        review_pass: fields.review_pass,
        total_review_passes: fields.total_review_passes,
        message,
        event_type,
    })
}

#[cfg(test)]
mod progress_mapping_tests {
    use super::ui_event_to_progress_update;
    use crate::config::types::{CloudConfig, GitAuthMethod, GitRemoteConfig};
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::state::PipelineState;
    use crate::reducer::ui_event::UIEvent;

    fn cloud_for_test() -> CloudConfig {
        CloudConfig {
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
        }
    }

    #[test]
    fn iteration_progress_maps_to_iteration_progress_event_type() {
        let cloud = cloud_for_test();
        let mut state = PipelineState::initial(10, 0);
        state.phase = PipelinePhase::Development;
        state.iteration = 99;

        let ui = UIEvent::IterationProgress {
            current: 2,
            total: 5,
        };
        let update = ui_event_to_progress_update(&ui, &state, &cloud).expect("update");

        assert_eq!(update.iteration, Some(2));
        assert_eq!(update.total_iterations, Some(5));

        match update.event_type {
            crate::cloud::types::ProgressEventType::IterationProgress { current, total } => {
                assert_eq!(current, 2);
                assert_eq!(total, 5);
            }
            other => panic!("unexpected event type: {other:?}"),
        }
    }

    #[test]
    fn review_progress_maps_to_review_progress_event_type() {
        let cloud = cloud_for_test();
        let mut state = PipelineState::initial(10, 0);
        state.phase = PipelinePhase::Review;
        state.reviewer_pass = 99;

        let ui = UIEvent::ReviewProgress { pass: 1, total: 3 };
        let update = ui_event_to_progress_update(&ui, &state, &cloud).expect("update");

        assert_eq!(update.review_pass, Some(1));
        assert_eq!(update.total_review_passes, Some(3));

        match update.event_type {
            crate::cloud::types::ProgressEventType::ReviewProgress { pass, total } => {
                assert_eq!(pass, 1);
                assert_eq!(total, 3);
            }
            other => panic!("unexpected event type: {other:?}"),
        }
    }

    #[test]
    fn push_failed_maps_to_push_failed_event_type() {
        let cloud = cloud_for_test();
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::CommitMessage;

        let ui = UIEvent::PushFailed {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            error: "Bearer SECRET".to_string(),
        };
        let update = ui_event_to_progress_update(&ui, &state, &cloud).expect("update");

        match update.event_type {
            crate::cloud::types::ProgressEventType::PushFailed {
                remote,
                branch,
                error,
            } => {
                assert_eq!(remote, "origin");
                assert_eq!(branch, "main");
                assert!(!error.contains("SECRET"), "error must be redacted: {error}");
            }
            other => panic!("unexpected event type: {other:?}"),
        }
    }

    #[test]
    fn phase_transition_uses_one_based_iteration_and_review_pass() {
        let cloud = cloud_for_test();
        let mut state = PipelineState::initial(5, 3);
        state.phase = PipelinePhase::Planning;
        state.iteration = 0;
        state.total_iterations = 5;
        state.reviewer_pass = 0;
        state.total_reviewer_passes = 3;

        let ui = UIEvent::PhaseTransition {
            from: None,
            to: PipelinePhase::Development,
        };
        let update = ui_event_to_progress_update(&ui, &state, &cloud).expect("update");

        assert_eq!(update.iteration, Some(1));
        assert_eq!(update.total_iterations, Some(5));
        assert_eq!(update.review_pass, Some(1));
        assert_eq!(update.total_review_passes, Some(3));
    }

    #[test]
    fn agent_activity_is_not_forwarded_verbatim_to_cloud_progress() {
        let cloud = cloud_for_test();
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Development;

        let ui = UIEvent::AgentActivity {
            agent: "dev-agent".to_string(),
            message: "token=SECRET_VALUE and /home/user/.ssh/id_rsa".to_string(),
        };
        let update = ui_event_to_progress_update(&ui, &state, &cloud).expect("update");

        assert!(
            update.message.contains("dev-agent"),
            "should still identify which agent produced activity"
        );
        assert!(
            !update.message.contains("SECRET_VALUE"),
            "must not forward raw activity text containing secrets"
        );
        assert!(
            !update.message.contains("id_rsa"),
            "must not forward sensitive paths from activity messages"
        );
    }

    #[test]
    fn mapping_returns_none_when_run_id_missing() {
        let mut cloud = cloud_for_test();
        cloud.run_id = None;

        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Planning;

        let ui = UIEvent::IterationProgress {
            current: 1,
            total: 1,
        };
        let update = ui_event_to_progress_update(&ui, &state, &cloud);

        assert!(update.is_none(), "run_id is required for cloud progress");
    }

    #[test]
    fn phase_transition_uses_event_from_for_previous_phase() {
        let cloud = cloud_for_test();
        let mut state = PipelineState::initial(2, 1);
        state.phase = PipelinePhase::Development;
        state.previous_phase = Some(PipelinePhase::Planning);

        let ui = UIEvent::PhaseTransition {
            from: Some(PipelinePhase::Review),
            to: PipelinePhase::CommitMessage,
        };
        let update = ui_event_to_progress_update(&ui, &state, &cloud).expect("update");

        assert_eq!(update.previous_phase.as_deref(), Some("Review"));
    }
}

#[cfg(test)]
mod cloud_progress_error_redaction_tests {
    use super::safe_cloud_error_string;

    #[test]
    fn cloud_progress_errors_are_redacted_and_truncated_for_logs_and_errors() {
        let e = crate::cloud::types::CloudError::HttpError(
            401,
            "Bearer SECRET_TOKEN and https://user:pass@example.com?access_token=abc".to_string(),
        );
        let out = safe_cloud_error_string(&e);

        assert!(!out.contains("SECRET_TOKEN"), "should redact tokens: {out}");
        assert!(
            !out.contains("user:pass"),
            "should redact url userinfo: {out}"
        );
        assert!(
            out.contains("<redacted>"),
            "should include redaction marker: {out}"
        );
    }
}
