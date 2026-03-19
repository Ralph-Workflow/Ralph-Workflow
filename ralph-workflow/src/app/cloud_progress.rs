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
        ui_events.iter().try_for_each(|ui_event| {
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
            Ok(())
        })?;
    }

    Ok(())
}

#[derive(Debug, Clone)]
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

    fn with_previous_phase(mut self, phase: Option<String>) -> Self {
        self.previous_phase = phase;
        self
    }

    fn with_iteration(mut self, iteration: Option<u32>, total: Option<u32>) -> Self {
        self.iteration = iteration;
        self.total_iterations = total;
        self
    }

    fn with_review(mut self, pass: Option<u32>, total: Option<u32>) -> Self {
        self.review_pass = pass;
        self.total_review_passes = total;
        self
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
    mut fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    let from_str = from.map(|p| format!("{p:?}"));
    let to_str = format!("{to:?}");
    fields = fields.with_previous_phase(from_str.clone());
    let message = format!(
        "Phase transition: {} -> {}",
        from_str.as_deref().unwrap_or("None"),
        to_str
    );
    (
        fields,
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
    mut fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    fields = fields.with_iteration(Some(current), Some(total));
    (
        fields,
        format!("Development iteration {current}/{total}"),
        crate::cloud::types::ProgressEventType::IterationProgress { current, total },
    )
}

fn review_progress(
    pass: u32,
    total: u32,
    mut fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    fields = fields.with_review(Some(pass), Some(total));
    (
        fields,
        format!("Review pass {pass}/{total}"),
        crate::cloud::types::ProgressEventType::ReviewProgress { pass, total },
    )
}

fn agent_activity_progress(
    agent: &str,
    fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    (
        fields,
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
    fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    (
        fields,
        format!("Pushed to {remote}/{branch}: {commit_sha}"),
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
    fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    let error = crate::cloud::redaction::redact_secrets(error);
    (
        fields,
        format!("Push to {remote}/{branch} failed: {error}"),
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
    fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    let message = if number > 0 {
        format!("PR created #{number}: {url}")
    } else {
        format!("PR created: {url}")
    };
    (
        fields,
        message,
        crate::cloud::types::ProgressEventType::PullRequestCreated {
            url: url.to_string(),
            number,
        },
    )
}

fn pull_request_failed_progress(
    error: &str,
    fields: ProgressContextFields,
) -> (
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
) {
    let error = crate::cloud::redaction::redact_secrets(error);
    (
        fields,
        format!("PR creation failed: {error}"),
        crate::cloud::types::ProgressEventType::PullRequestFailed { error },
    )
}

fn map_ui_event_to_progress(
    ui_event: &UIEvent,
    fields: ProgressContextFields,
) -> Option<(
    ProgressContextFields,
    String,
    crate::cloud::types::ProgressEventType,
)> {
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
        } => Some(agent_activity_progress(agent, fields)),
        UIEvent::PushCompleted {
            remote,
            branch,
            commit_sha,
        } => Some(push_completed_progress(remote, branch, commit_sha, fields)),
        UIEvent::PushFailed {
            remote,
            branch,
            error,
        } => Some(push_failed_progress(remote, branch, error, fields)),
        UIEvent::PullRequestCreated { url, number } => {
            Some(pull_request_created_progress(url, *number, fields))
        }
        UIEvent::PullRequestFailed { error } => Some(pull_request_failed_progress(error, fields)),
        // XmlOutput and PromptReplayHit are informational only; not forwarded to cloud progress.
        UIEvent::XmlOutput { .. } | UIEvent::PromptReplayHit { .. } => None,
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
    let fields = ProgressContextFields::from_state(state);
    let (fields, message, event_type) = map_ui_event_to_progress(ui_event, fields)?;

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
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 99,
            ..PipelineState::initial(10, 0)
        };

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
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 99,
            ..PipelineState::initial(10, 0)
        };

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
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            ..PipelineState::initial(1, 0)
        };

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
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            iteration: 0,
            ..PipelineState::initial(5, 3)
        };

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
        let state = PipelineState {
            phase: PipelinePhase::Development,
            ..PipelineState::initial(1, 0)
        };

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
        let cloud = CloudConfig {
            enabled: true,
            api_url: Some("https://api.example.com".to_string()),
            api_token: Some("secret".to_string()),
            run_id: None,
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

        let state = PipelineState {
            phase: PipelinePhase::Planning,
            ..PipelineState::initial(1, 0)
        };

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
        let state = PipelineState {
            phase: PipelinePhase::Development,
            previous_phase: Some(PipelinePhase::Planning),
            ..PipelineState::initial(2, 1)
        };

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
