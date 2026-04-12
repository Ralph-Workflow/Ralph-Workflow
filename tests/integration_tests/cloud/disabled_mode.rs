//! Tests verifying that cloud mode disabled = zero behavior change.
//!
//! These tests verify the critical acceptance criterion: when `RALPH_CLOUD_MODE`
//! is unset or false, behavior is IDENTICAL to the CLI before cloud support was added.
//!
//! This means:
//! - No API calls occur
//! - No git push occurs
//! - Commits remain local
//! - No heartbeats
//! - No progress reporting
//! - No cloud-specific effects

use ralph_workflow::config::CloudConfig;
use ralph_workflow::reducer::effect::{Effect, EffectHandler, EffectResult};
use ralph_workflow::reducer::event::PipelineEvent;
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::ui_event::UIEvent;

use crate::common::IntegrationFixture;
use crate::test_timeout::with_default_timeout;

#[test]
fn test_cloud_mode_disabled_by_default() {
    with_default_timeout(|| {
        // Empty env — no vars set — fully isolated, no process env mutation
        let config = CloudConfig::from_env_fn(|_| None);

        assert!(
            !config.enabled,
            "Cloud mode should be disabled by default when RALPH_CLOUD_MODE is not set"
        );
        assert!(
            config.api_url.is_none(),
            "API URL should be None when disabled"
        );
        assert!(
            config.api_token.is_none(),
            "API token should be None when disabled"
        );
        assert!(
            config.run_id.is_none(),
            "Run ID should be None when disabled"
        );
    });
}

#[test]
fn test_cloud_mode_explicitly_disabled() {
    with_default_timeout(|| {
        // Explicitly set RALPH_CLOUD_MODE to false
        let config = CloudConfig::from_env_fn(|k| match k {
            "RALPH_CLOUD_MODE" => Some("false".to_string()),
            _ => None,
        });

        assert!(
            !config.enabled,
            "Cloud mode should be disabled when RALPH_CLOUD_MODE=false"
        );
    });
}

#[test]
fn test_cloud_mode_disabled_ignores_other_vars() {
    with_default_timeout(|| {
        // Set other cloud vars but leave RALPH_CLOUD_MODE unset
        let env = [
            ("RALPH_CLOUD_API_URL", "https://api.example.com"),
            ("RALPH_CLOUD_API_TOKEN", "secret"),
            ("RALPH_CLOUD_RUN_ID", "run123"),
        ];
        let config = CloudConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });

        assert!(
            !config.enabled,
            "Cloud mode should be disabled even if other vars are set"
        );
        // The other vars are not loaded when disabled
        assert!(config.api_url.is_none());
        assert!(config.api_token.is_none());
        assert!(config.run_id.is_none());
    });
}

#[test]
fn test_cloud_config_disabled_validation_passes() {
    with_default_timeout(|| {
        let config = CloudConfig::disabled();

        assert!(
            config.validate().is_ok(),
            "Disabled cloud config should always validate without required fields"
        );
    });
}

#[test]
fn test_cloud_mode_case_insensitive() {
    with_default_timeout(|| {
        // Test various capitalizations that should disable cloud mode
        for value in &["FALSE", "False", "false", "0"] {
            let v = *value;
            let config = CloudConfig::from_env_fn(|k| match k {
                "RALPH_CLOUD_MODE" => Some(v.to_string()),
                _ => None,
            });
            assert!(
                !config.enabled,
                "Cloud mode should be disabled for value: {v}"
            );
        }
    });
}

#[test]
fn test_disabled_config_has_safe_defaults() {
    with_default_timeout(|| {
        let config = CloudConfig::disabled();

        assert!(!config.enabled);
        assert!(config.api_url.is_none());
        assert!(config.api_token.is_none());
        assert!(config.run_id.is_none());
        assert_eq!(config.heartbeat_interval_secs, 30);
        assert!(config.graceful_degradation);
    });
}

#[test]
fn test_git_remote_config_defaults_when_disabled() {
    with_default_timeout(|| {
        // Empty env — no vars set
        let config = CloudConfig::from_env_fn(|_| None);

        // Git remote config should have safe defaults even when cloud disabled
        assert!(!config.git_remote.create_pr);
        assert!(!config.git_remote.force_push);
        assert_eq!(config.git_remote.remote_name, "origin");
    });
}

#[test]
fn test_cloud_env_var_variations_respected() {
    with_default_timeout(|| {
        // Test that empty string counts as disabled
        let config = CloudConfig::from_env_fn(|k| match k {
            "RALPH_CLOUD_MODE" => Some(String::new()),
            _ => None,
        });
        assert!(!config.enabled, "Empty string should disable cloud mode");

        // Test that random values count as disabled
        let config = CloudConfig::from_env_fn(|k| match k {
            "RALPH_CLOUD_MODE" => Some("maybe".to_string()),
            _ => None,
        });
        assert!(
            !config.enabled,
            "Non-true/1 values should disable cloud mode"
        );
    });
}

#[test]
fn test_cloud_mode_disabled_does_not_report_progress_even_if_reporter_is_injected() {
    with_default_timeout(|| {
        use ralph_workflow::app::event_loop::{
            run_event_loop_with_handler, EventLoopConfig, StatefulHandler,
        };
        use ralph_workflow::cloud::MockCloudReporter;

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
                    EffectResult::event(PipelineEvent::PromptPermissionsRestored).with_ui_event(
                        UIEvent::AgentActivity {
                            agent: "dev-agent".to_string(),
                            message: "token=SECRET_VALUE".to_string(),
                        },
                    ),
                )
            }
        }

        impl StatefulHandler for OneShotHandler {
            fn update_state(&mut self, state: PipelineState) {
                self.state = state;
            }
        }

        let mut fixture = IntegrationFixture::new();
        // IntegrationFixture already uses CloudConfig::disabled() by default
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
            0,
            "Cloud mode disabled must not emit progress updates"
        );
    });
}
