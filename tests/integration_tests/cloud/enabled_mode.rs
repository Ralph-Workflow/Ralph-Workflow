//! Tests for cloud mode enabled behavior.
//!
//! These tests verify that when cloud mode is enabled, the system correctly:
//! - Reports progress to the cloud API
//! - Handles API failures gracefully (graceful degradation)
//! - Sends heartbeats
//! - Reports completion
//!
//! All tests use `MockCloudReporter` to avoid real HTTP calls.

use ralph_workflow::cloud::{CloudReporter, MockCloudReporter, ProgressEventType, ProgressUpdate};
use ralph_workflow::config::{CloudConfig, GitRemoteConfig};
use ralph_workflow::reducer::effect::{Effect, EffectHandler, EffectResult};
use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::ui_event::UIEvent;

use crate::common::IntegrationFixture;
use crate::test_timeout::with_default_timeout;

#[test]
fn test_mock_cloud_reporter_captures_progress() {
    with_default_timeout(|| {
        let reporter = MockCloudReporter::new();

        let update = ProgressUpdate {
            timestamp: chrono::Utc::now().to_rfc3339(),
            phase: "Planning".to_string(),
            previous_phase: None,
            iteration: Some(1),
            total_iterations: Some(3),
            review_pass: None,
            total_review_passes: None,
            message: "Planning phase started".to_string(),
            event_type: ProgressEventType::PhaseTransition {
                from: None,
                to: "Planning".to_string(),
            },
        };

        reporter.report_progress(&update).unwrap();

        assert_eq!(
            reporter.progress_count(),
            1,
            "Reporter should capture progress update"
        );

        let calls = reporter.calls();
        assert_eq!(calls.len(), 1, "Should have exactly one call"); // OK: content checked below

        match &calls[0] {
            ralph_workflow::cloud::mock::MockCloudCall::Progress(captured) => {
                assert_eq!(captured.phase, "Planning", "Should capture phase");
                assert_eq!(
                    captured.message, "Planning phase started",
                    "Should capture message"
                );
                assert_eq!(captured.iteration, Some(1), "Should capture iteration");
                assert_eq!(
                    captured.total_iterations,
                    Some(3),
                    "Should capture total iterations"
                );
            }
            other => panic!("Expected Progress call, got {other:?}"),
        }
    });
}

#[test]
fn test_mock_cloud_reporter_captures_heartbeat() {
    with_default_timeout(|| {
        let reporter = MockCloudReporter::new();

        reporter.heartbeat().unwrap();
        reporter.heartbeat().unwrap();

        assert_eq!(
            reporter.heartbeat_count(),
            2,
            "Reporter should capture heartbeat calls"
        );
    });
}

#[test]
fn test_mock_cloud_reporter_captures_completion() {
    with_default_timeout(|| {
        let reporter = MockCloudReporter::new();

        let result = ralph_workflow::cloud::PipelineResult {
            success: true,
            commit_sha: Some("abc123".to_string()),
            pr_url: None,
            push_count: 0,
            last_pushed_commit: None,
            unpushed_commits: Vec::new(),
            last_push_error: None,
            iterations_used: 1,
            review_passes_used: 0,
            issues_found: false,
            duration_secs: 100,
            error_message: None,
        };

        reporter.report_completion(&result).unwrap();

        let calls = reporter.calls();
        assert_eq!(calls.len(), 1, "Should have exactly one call"); // OK: content checked below

        match &calls[0] {
            ralph_workflow::cloud::mock::MockCloudCall::Completion(result) => {
                assert!(result.success, "Completion should be successful");
                assert_eq!(result.commit_sha.as_deref(), Some("abc123"));
                assert_eq!(result.iterations_used, 1, "Should capture iterations used");
                assert_eq!(result.duration_secs, 100, "Should capture duration");
                assert!(!result.issues_found, "Should capture issues_found flag");
            }
            _ => panic!("Expected Completion call"),
        }
    });
}

#[test]
fn test_mock_cloud_reporter_graceful_degradation() {
    with_default_timeout(|| {
        let reporter = MockCloudReporter::new();
        reporter.set_should_fail(true);

        let update = ProgressUpdate {
            timestamp: chrono::Utc::now().to_rfc3339(),
            phase: "Development".to_string(),
            previous_phase: Some("Planning".to_string()),
            iteration: Some(1),
            total_iterations: Some(3),
            review_pass: None,
            total_review_passes: None,
            message: "Iteration 1 started".to_string(),
            event_type: ProgressEventType::IterationStarted { iteration: 1 },
        };

        let result = reporter.report_progress(&update);
        assert!(
            result.is_err(),
            "Should fail when configured to fail (for testing graceful degradation)"
        );

        // Verify the error is the expected mock failure
        match result {
            Err(ralph_workflow::cloud::CloudError::NetworkError(msg)) => {
                assert_eq!(msg, "Mock failure");
            }
            _ => panic!("Expected NetworkError with 'Mock failure'"),
        }
    });
}

#[test]
fn test_cloud_config_enabled_loads_all_fields() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_CLOUD_MODE", "true"),
            ("RALPH_CLOUD_API_URL", "https://api.example.com/v1"),
            ("RALPH_CLOUD_API_TOKEN", "secret_token_123"),
            ("RALPH_CLOUD_RUN_ID", "run_abc123"),
            ("RALPH_CLOUD_HEARTBEAT_INTERVAL", "60"),
            ("RALPH_CLOUD_GRACEFUL_DEGRADATION", "true"),
        ];
        let config = CloudConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        assert!(config.enabled, "Cloud mode should be enabled");
        assert_eq!(
            config.api_url.as_deref(),
            Some("https://api.example.com/v1"),
            "API URL should be loaded"
        );
        assert_eq!(
            config.api_token.as_deref(),
            Some("secret_token_123"),
            "API token should be loaded"
        );
        assert_eq!(
            config.run_id.as_deref(),
            Some("run_abc123"),
            "Run ID should be loaded"
        );
        assert_eq!(
            config.heartbeat_interval_secs, 60,
            "Heartbeat interval should be parsed from env var"
        );
        assert!(
            config.graceful_degradation,
            "Graceful degradation should be enabled"
        );
    });
}

#[test]
fn test_cloud_config_validation_requires_fields() {
    with_default_timeout(|| {
        // RALPH_CLOUD_MODE=true but no other required fields
        let config = CloudConfig::from_env_fn(|k| match k {
            "RALPH_CLOUD_MODE" => Some("true".to_string()),
            _ => None,
        });

        assert!(config.enabled, "Cloud mode should be enabled");
        let validation_result = config.validate();
        assert!(
            validation_result.is_err(),
            "Validation should fail when required fields are missing"
        );
    });
}

#[test]
fn test_cloud_mode_boolean_parsing() {
    with_default_timeout(|| {
        for value in &["true", "TRUE", "True", "1"] {
            let v = *value;
            let config = CloudConfig::from_env_fn(|k| match k {
                "RALPH_CLOUD_MODE" => Some(v.to_string()),
                _ => None,
            });
            assert!(
                config.enabled,
                "Cloud mode should be enabled for value: {v}"
            );
        }
    });
}

#[test]
fn test_graceful_degradation_default() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_CLOUD_MODE", "true"),
            ("RALPH_CLOUD_API_URL", "https://api.example.com"),
            ("RALPH_CLOUD_API_TOKEN", "token"),
            ("RALPH_CLOUD_RUN_ID", "run123"),
            // RALPH_CLOUD_GRACEFUL_DEGRADATION intentionally absent
        ];
        let config = CloudConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        assert!(
            config.graceful_degradation,
            "Graceful degradation should be enabled by default"
        );
    });
}

#[test]
fn test_heartbeat_interval_default() {
    with_default_timeout(|| {
        let env = [
            ("RALPH_CLOUD_MODE", "true"),
            ("RALPH_CLOUD_API_URL", "https://api.example.com"),
            ("RALPH_CLOUD_API_TOKEN", "token"),
            ("RALPH_CLOUD_RUN_ID", "run123"),
            // RALPH_CLOUD_HEARTBEAT_INTERVAL intentionally absent
        ];
        let config = CloudConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        assert_eq!(
            config.heartbeat_interval_secs, 30,
            "Heartbeat interval should default to 30 seconds"
        );
    });
}

#[test]
fn test_progress_event_types_serialize() {
    with_default_timeout(|| {
        // Verify that progress event types serialize correctly for API transmission
        let event = ProgressEventType::PhaseTransition {
            from: Some("Planning".to_string()),
            to: "Development".to_string(),
        };

        let serialized = serde_json::to_value(&event).unwrap();
        assert!(
            serialized.is_object(),
            "Event type should serialize to object"
        );
        assert_eq!(
            serialized["type"].as_str().unwrap(),
            "phase_transition",
            "Event type should be snake_case"
        );
    });
}

#[test]
fn test_progress_update_serialization() {
    with_default_timeout(|| {
        let update = ProgressUpdate {
            timestamp: "2025-02-15T10:00:00Z".to_string(),
            phase: "Planning".to_string(),
            previous_phase: None,
            iteration: Some(1),
            total_iterations: Some(3),
            review_pass: None,
            total_review_passes: None,
            message: "Starting planning".to_string(),
            event_type: ProgressEventType::PipelineStarted,
        };

        let serialized = serde_json::to_string(&update).unwrap();
        assert!(serialized.contains("Planning"), "Should contain phase");
        assert!(
            serialized.contains("2025-02-15"),
            "Should contain timestamp"
        );

        // Verify deserialization works
        let deserialized: ProgressUpdate = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized.phase, "Planning");
    });
}

#[test]
fn test_cloud_mode_enabled_reports_progress_updates_from_ui_events() {
    with_default_timeout(|| {
        use ralph_workflow::app::event_loop::{
            run_event_loop_with_handler, EventLoopConfig, StatefulHandler,
        };

        #[derive(Debug)]
        struct OneShotHandler {
            state: PipelineState,
        }

        impl EffectHandler<'_> for OneShotHandler {
            fn execute(
                &mut self,
                _effect: Effect,
                _ctx: &mut ralph_workflow::phases::PhaseContext<'_>,
            ) -> anyhow::Result<EffectResult> {
                Ok(
                    EffectResult::event(PipelineEvent::PromptPermissionsRestored)
                        .with_ui_event(UIEvent::PhaseTransition {
                            from: None,
                            to: PipelinePhase::Planning,
                        })
                        .with_ui_event(UIEvent::AgentActivity {
                            agent: "dev-agent".to_string(),
                            message: "token=SECRET_VALUE and /home/user/.ssh/id_rsa".to_string(),
                        }),
                )
            }
        }

        impl StatefulHandler for OneShotHandler {
            fn update_state(&mut self, state: PipelineState) {
                self.state = state;
            }
        }

        let mut fixture = IntegrationFixture::new();
        fixture.cloud = CloudConfig {
            enabled: true,
            api_url: Some("https://api.example.com".to_string()),
            api_token: Some("token".to_string()),
            run_id: Some("run_123".to_string()),
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteConfig::default(),
        };
        let reporter = MockCloudReporter::new();

        let mut ctx = fixture.ctx(Some(&reporter));

        let initial_state = PipelineState::initial(1, 0);
        let mut handler = OneShotHandler {
            state: initial_state.clone(),
        };

        let loop_config = EventLoopConfig { max_iterations: 5 };
        let _ =
            run_event_loop_with_handler(&mut ctx, Some(initial_state), loop_config, &mut handler)
                .expect("event loop should run");

        assert_eq!(
            reporter.progress_count(),
            2,
            "Cloud mode enabled should emit progress updates for UI events"
        );

        let calls = reporter.calls();
        let mut messages = Vec::new();
        for call in calls {
            if let ralph_workflow::cloud::mock::MockCloudCall::Progress(update) = call {
                messages.push(update.message);
            }
        }
        let joined = messages.join("\n");
        assert!(
            !joined.contains("SECRET_VALUE"),
            "must not leak secrets: {joined}"
        );
        assert!(!joined.contains("id_rsa"), "must not leak paths: {joined}");
        assert!(
            joined.contains("dev-agent"),
            "should retain agent identity: {joined}"
        );
    });
}
