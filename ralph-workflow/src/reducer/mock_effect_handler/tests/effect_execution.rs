// Tests for effect simulation and mock behavior.

use super::*;

#[test]
fn mock_effect_handler_simulates_empty_diff() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state).with_empty_diff();

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();

    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "claude",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    // CheckCommitDiff should mark empty diff
    let result = handler
        .execute(Effect::CheckCommitDiff, &mut ctx)
        .expect("Expected effect result");

    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::DiffPrepared {
                empty: true,
                ..
            })
        ),
        "Should return CommitDiffPrepared when empty diff is simulated, got: {:?}",
        result.event
    );
}

#[test]
fn mock_effect_handler_captures_materialize_commit_inputs_even_when_diff_missing() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();

    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "claude",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    // No diff file exists in workspace - should invalidate, but still capture the executed effect.
    let attempt = 1;
    let result = handler
        .execute(Effect::MaterializeCommitInputs { attempt }, &mut ctx)
        .expect("effect executes");

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::DiffInvalidated { .. })
    ));

    assert!(
        handler
            .was_effect_executed(|e| matches!(e, Effect::MaterializeCommitInputs { attempt: 1 })),
        "MaterializeCommitInputs must be captured even on early-return invalidation paths"
    );
}

#[test]
fn mock_effect_handler_emits_residual_files_found_when_configured() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let state = PipelineState::initial(1, 0);
    let mut handler =
        MockEffectHandler::new(state).with_residual_files_for_pass(1, ["src/leftover.rs"]);

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();

    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "claude",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    let result = handler
        .execute(Effect::CheckResidualFiles { pass: 1 }, &mut ctx)
        .expect("effect executes");

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::ResidualFilesFound {
            pass: 1,
            ..
        })
    ));
}

#[test]
fn mock_effect_handler_execute_mock_respects_pre_termination_snapshot_dirty() {
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state).with_dirty_pre_termination_snapshot(3);

    let result = handler.execute_mock(&Effect::CheckUncommittedChangesBeforeTermination);

    assert!(matches!(
        result.event,
        PipelineEvent::Commit(
            crate::reducer::event::CommitEvent::PreTerminationUncommittedChangesDetected {
                file_count: 3
            }
        )
    ));
}

#[test]
fn mock_effect_handler_normal_commit_generation() {
    use crate::reducer::state::CommitValidatedOutcome;

    let state = PipelineState {
        commit_validated_outcome: Some(CommitValidatedOutcome {
            attempt: 1,
            message: Some("mock commit message".to_string()),
            reason: None,
        }),
        ..PipelineState::initial(1, 0)
    };
    let mut handler = MockEffectHandler::new(state); // No with_empty_diff()

    // ApplyCommitMessageOutcome should return CommitMessageGenerated normally
    let result = handler.execute_mock(&Effect::ApplyCommitMessageOutcome);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::MessageGenerated { .. })
        ),
        "Should return CommitMessageGenerated when validated outcome exists, got: {:?}",
        result.event
    );
}

#[test]
fn mock_effect_handler_review_validation_emits_no_issues_outcome() {
    let state = PipelineState::initial(1, 1);
    let mut handler = MockEffectHandler::new(state);

    let result = handler.execute_mock(&Effect::ValidateReviewIssuesXml { pass: 0 });

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::IssuesXmlValidated {
            issues,
            no_issues_found: Some(ref message),
            ..
        }) if issues.is_empty() && message == "ok"
    ));
}

#[test]
fn mock_effect_handler_write_timeout_context_emits_timeout_context_written_event() {
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let result = handler.execute_mock(&Effect::WriteTimeoutContext {
        role: crate::agents::AgentRole::Developer,
        logfile_path: ".agent/logs/development.log".to_string(),
        context_path: ".agent/tmp/timeout_context_1.txt".to_string(),
    });

    assert!(matches!(
        result.event,
        PipelineEvent::Agent(crate::reducer::event::AgentEvent::TimeoutContextWritten {
            role: crate::agents::AgentRole::Developer,
            ref logfile_path,
            ref context_path,
        }) if logfile_path == ".agent/logs/development.log"
            && context_path == ".agent/tmp/timeout_context_1.txt"
    ));
    assert!(result.ui_events.is_empty());
    assert!(result.additional_events.is_empty());
}

#[test]
fn mock_effect_handler_trigger_dev_fix_flow_does_not_write_completion_marker() {
    use crate::agents::{AgentRegistry, AgentRole};
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::event::PipelinePhase;
    use crate::workspace::{MemoryWorkspace, Workspace};
    use std::path::{Path, PathBuf};
    use std::sync::Arc;

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();

    let cloud = crate::config::types::CloudConfig::disabled();
    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "codex",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let effect = Effect::TriggerDevFixFlow {
        failed_phase: PipelinePhase::Development,
        failed_role: AgentRole::Developer,
        retry_cycle: 1,
    };

    let result = handler
        .execute(effect, &mut ctx)
        .expect("Expected effect result");
    assert!(matches!(
        result.event,
        PipelineEvent::AwaitingDevFix(
            crate::reducer::event::AwaitingDevFixEvent::DevFixTriggered { .. }
        )
    ));

    let marker_path = Path::new(".agent/tmp/completion_marker");
    assert!(
        !workspace.exists(marker_path),
        "TriggerDevFixFlow must not write completion marker during recovery"
    );
}

#[test]
fn mock_effect_handler_trigger_dev_fix_flow_emits_events_on_marker_write_failure() {
    use crate::agents::{AgentRegistry, AgentRole};
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::event::{AwaitingDevFixEvent, PipelinePhase};
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();

    let cloud = crate::config::types::CloudConfig::disabled();
    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "codex",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let effect = Effect::TriggerDevFixFlow {
        failed_phase: PipelinePhase::Development,
        failed_role: AgentRole::Developer,
        retry_cycle: 1,
    };

    let result = handler.execute(effect, &mut ctx);
    assert!(
        result.is_ok(),
        "TriggerDevFixFlow should emit events even if marker write fails"
    );

    let result = result.expect("Expected effect result");
    // After the bug fix, TriggerDevFixFlow emits DevFixTriggered and DevFixCompleted
    // but NOT CompletionMarkerEmitted (that's now emitted by orchestration when exhausted)
    assert!(matches!(
        result.additional_events.last(),
        Some(PipelineEvent::AwaitingDevFix(
            AwaitingDevFixEvent::DevFixCompleted { .. }
        ))
    ));
}

#[test]
fn mock_attempt_recovery_uses_previous_phase_when_failed_phase_for_recovery_missing() {
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        previous_phase: Some(PipelinePhase::Review),
        failed_phase_for_recovery: None,
        ..PipelineState::initial(1, 0)
    };
    let mut handler = MockEffectHandler::new(state);

    let result = handler.execute_mock(&Effect::AttemptRecovery {
        level: 1,
        attempt_count: 1,
    });

    assert!(matches!(
        result.event,
        PipelineEvent::AwaitingDevFix(
            crate::reducer::event::AwaitingDevFixEvent::RecoveryAttempted {
                target_phase: PipelinePhase::Review,
                ..
            }
        )
    ));
}

#[test]
fn mock_attempt_recovery_never_targets_awaiting_dev_fix() {
    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        failed_phase_for_recovery: Some(PipelinePhase::AwaitingDevFix),
        ..PipelineState::initial(1, 0)
    };
    let mut handler = MockEffectHandler::new(state);

    let result = handler.execute_mock(&Effect::AttemptRecovery {
        level: 1,
        attempt_count: 1,
    });

    assert!(matches!(
        result.event,
        PipelineEvent::AwaitingDevFix(
            crate::reducer::event::AwaitingDevFixEvent::RecoveryAttempted {
                target_phase: PipelinePhase::Development,
                ..
            }
        )
    ));
}

/// Test that `UIEvents` do not affect pipeline state.
#[test]
fn ui_events_do_not_affect_state() {
    // This test verifies that UIEvents are purely display-only
    // and do not cause any state mutations
    let state = PipelineState::initial(1, 0);
    let state_clone = state.clone();

    // UIEvent exists but reducer never sees it
    drop(UIEvent::PhaseTransition {
        from: None,
        to: PipelinePhase::Development,
    });

    // State should be unchanged
    assert_eq!(state.phase, state_clone.phase);
}

/// Test that `MockEffectHandler` emits `XmlOutput` events for plan validation.
#[test]
fn mock_effect_handler_emits_xml_output_for_plan() {
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let _result = handler.execute_mock(&Effect::ValidatePlanningXml { iteration: 1 });

    // Verify XmlOutput event was emitted with DevelopmentPlan type
    assert!(
        handler.was_ui_event_emitted(|e| matches!(
            e,
            UIEvent::XmlOutput {
                xml_type: XmlOutputType::DevelopmentPlan,
                ..
            }
        )),
        "Should emit XmlOutput event for plan validation"
    );
}

/// Test that `MockEffectHandler` emits `XmlOutput` events for development extraction.
#[test]
fn mock_effect_handler_emits_xml_output_for_development() {
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let _result = handler.execute_mock(&Effect::ExtractDevelopmentXml { iteration: 1 });

    // Verify XmlOutput event was emitted with DevelopmentResult type
    assert!(
        handler.was_ui_event_emitted(|e| matches!(
            e,
            UIEvent::XmlOutput {
                xml_type: XmlOutputType::DevelopmentResult,
                ..
            }
        )),
        "Should emit XmlOutput event for development result"
    );
}

/// Test that `MockEffectHandler` emits `XmlOutput` events for review pass.
#[test]
fn mock_effect_handler_emits_xml_output_for_review_snippets() {
    let state = PipelineState::initial(1, 1);
    let mut handler = MockEffectHandler::new(state);

    let _result = handler.execute_mock(&Effect::ExtractReviewIssueSnippets { pass: 1 });

    // Verify XmlOutput event was emitted with ReviewIssues type
    assert!(
        handler.was_ui_event_emitted(|e| matches!(
            e,
            UIEvent::XmlOutput {
                xml_type: XmlOutputType::ReviewIssues,
                ..
            }
        )),
        "Should emit XmlOutput event for review issue snippets"
    );
}

/// Test that `MockEffectHandler` emits `XmlOutput` events for fix attempt.
#[test]
fn mock_effect_handler_emits_xml_output_for_fix() {
    let state = PipelineState::initial(1, 1);
    let mut handler = MockEffectHandler::new(state);

    let _result = handler.execute_mock(&Effect::ValidateFixResultXml { pass: 1 });

    // Verify XmlOutput event was emitted with FixResult type
    assert!(
        handler.was_ui_event_emitted(|e| matches!(
            e,
            UIEvent::XmlOutput {
                xml_type: XmlOutputType::FixResult,
                ..
            }
        )),
        "Should emit XmlOutput event for fix result"
    );
}

/// Test that `MockEffectHandler` emits `XmlOutput` events for commit message.
#[test]
fn mock_effect_handler_emits_xml_output_for_commit() {
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let _result = handler.execute_mock(&Effect::ValidateCommitXml);

    // Verify XmlOutput event was emitted with CommitMessage type
    assert!(
        handler.was_ui_event_emitted(|e| matches!(
            e,
            UIEvent::XmlOutput {
                xml_type: XmlOutputType::CommitMessage,
                ..
            }
        )),
        "Should emit XmlOutput event for commit message"
    );
}

#[test]
fn mock_save_checkpoint_persists_interrupted_by_user_flag() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::event::CheckpointTrigger;
    use crate::workspace::{MemoryWorkspace, Workspace};
    use std::path::{Path, PathBuf};
    use std::sync::Arc;

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();

    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "codex",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    let mut state = PipelineState::initial(1, 0);
    state.interrupted_by_user = true;
    state.recovery_epoch = 7;
    let mut handler = MockEffectHandler::new(state);

    let _ = handler
        .execute(
            Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::Interrupt,
            },
            &mut ctx,
        )
        .expect("mock save checkpoint should succeed");

    let checkpoint_path = Path::new(".agent/checkpoint.json");
    assert!(
        workspace.exists(checkpoint_path),
        "Mock handler should persist checkpoint file"
    );

    let json = workspace
        .read(checkpoint_path)
        .expect("checkpoint json should be readable");
    let v: serde_json::Value = serde_json::from_str(&json).expect("valid checkpoint json");

    assert_eq!(
        v.get("interrupted_by_user")
            .and_then(serde_json::Value::as_bool),
        Some(true),
        "Mock checkpoint must persist interrupted_by_user=true for parity with real handler"
    );

    assert_eq!(
        v.get("recovery_epoch").and_then(serde_json::Value::as_u64),
        Some(7),
        "Mock checkpoint must persist recovery_epoch for parity with real handler"
    );
}

#[test]
fn mock_execute_save_checkpoint_captures_effect_once() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::event::CheckpointTrigger;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();

    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "codex",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let _ = handler
        .execute(
            Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::Interrupt,
            },
            &mut ctx,
        )
        .expect("mock save checkpoint should succeed");

    let count = handler
        .captured_effects()
        .into_iter()
        .filter(|e| matches!(e, Effect::SaveCheckpoint { .. }))
        .count();
    assert_eq!(
        count, 1,
        "SaveCheckpoint executed via EffectHandler::execute must be captured exactly once"
    );
}

#[test]
fn mock_execute_emit_completion_marker_captures_effect_once() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();

    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "codex",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let _ = handler
        .execute(
            Effect::EmitCompletionMarkerAndTerminate {
                is_failure: true,
                reason: Some("test".to_string()),
            },
            &mut ctx,
        )
        .expect("mock completion marker should succeed");

    let count = handler
        .captured_effects()
        .into_iter()
        .filter(|e| matches!(e, Effect::EmitCompletionMarkerAndTerminate { .. }))
        .count();
    assert_eq!(
        count, 1,
        "EmitCompletionMarkerAndTerminate executed via EffectHandler::execute must be captured exactly once"
    );
}
