//! Event loop trace dump behavior.
//!
//! These tests verify that when the reducer event loop is exhausted (max iterations)
//! or recovers from a panic, it persists an execution trace to the per-run log directory.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::IntegrationFixture;
use crate::test_timeout::with_default_timeout;

use anyhow::Result;
use ralph_workflow::app::event_loop::{
    run_event_loop_with_handler, EventLoopConfig, StatefulHandler,
};
use ralph_workflow::logger::Logger;
use ralph_workflow::reducer::effect::{Effect, EffectHandler, EffectResult};
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::{PipelineEvent, PipelineState};
use ralph_workflow::workspace::{MemoryWorkspace, Workspace};
use std::path::PathBuf;
use std::sync::Arc;

const LOG_PATH: &str = ".agent/tmp/event_loop_trace_test.log";

#[derive(Debug)]
struct LoopingHandler {
    state: PipelineState,
}

impl EffectHandler<'_> for LoopingHandler {
    fn execute(
        &mut self,
        _effect: Effect,
        _ctx: &mut ralph_workflow::phases::PhaseContext<'_>,
    ) -> Result<EffectResult> {
        // Return a reducer-visible event that does not complete the pipeline.
        Ok(EffectResult::event(PipelineEvent::ContextCleaned))
    }
}

impl StatefulHandler for LoopingHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.state = state;
    }
}

#[derive(Debug)]
struct PanicHandler {
    state: PipelineState,
}

impl EffectHandler<'_> for PanicHandler {
    fn execute(
        &mut self,
        _effect: Effect,
        _ctx: &mut ralph_workflow::phases::PhaseContext<'_>,
    ) -> Result<EffectResult> {
        panic!("handler panic");
    }
}

impl StatefulHandler for PanicHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.state = state;
    }
}

#[derive(Debug)]
struct AdditionalEventsHandler {
    state: PipelineState,
}

impl EffectHandler<'_> for AdditionalEventsHandler {
    fn execute(
        &mut self,
        _effect: Effect,
        _ctx: &mut ralph_workflow::phases::PhaseContext<'_>,
    ) -> Result<EffectResult> {
        Ok(
            EffectResult::event(PipelineEvent::ContextCleaned).with_additional_event(
                PipelineEvent::CheckpointSaved {
                    trigger: ralph_workflow::reducer::event::CheckpointTrigger::PhaseTransition,
                },
            ),
        )
    }
}

impl StatefulHandler for AdditionalEventsHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.state = state;
    }
}

#[derive(Debug)]
struct PhaseChangingHandler {
    state: PipelineState,
}

impl EffectHandler<'_> for PhaseChangingHandler {
    fn execute(
        &mut self,
        _effect: Effect,
        _ctx: &mut ralph_workflow::phases::PhaseContext<'_>,
    ) -> Result<EffectResult> {
        Ok(EffectResult::event(PipelineEvent::FinalizingStarted))
    }
}

impl StatefulHandler for PhaseChangingHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.state = state;
    }
}

#[test]
fn test_event_loop_dumps_trace_on_max_iterations() {
    with_default_timeout(|| {
        let workspace = Arc::new(MemoryWorkspace::new(PathBuf::from("/test/repo")));
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::clone(&workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);

        // Force an infinite loop by making the handler return a no-progress event.
        let mut initial_state = PipelineState::initial(1, 0);
        initial_state.phase = PipelinePhase::Planning;
        initial_state.context_cleaned = true;

        let mut handler = LoopingHandler {
            state: initial_state.clone(),
        };

        let loop_config = EventLoopConfig { max_iterations: 3 };

        let res =
            run_event_loop_with_handler(&mut ctx, Some(initial_state), loop_config, &mut handler)
                .expect("event loop should run");
        assert!(
            !res.completed,
            "expected pipeline to not complete in loop scenario"
        );

        let trace_path_buf = fixture.run_log_context.event_loop_trace();
        let trace_path = trace_path_buf.to_string_lossy();
        assert!(
            workspace.was_written(trace_path.as_ref()),
            "expected event loop to dump trace to {trace_path}"
        );

        let trace = workspace
            .get_file(trace_path.as_ref())
            .expect("trace file should be readable");
        let line_count = trace.lines().filter(|l| !l.trim().is_empty()).count();
        assert!(
            line_count >= 3,
            "expected at least 3 trace entries, got {line_count}"
        );
    });
}

#[test]
fn test_event_loop_dumps_trace_on_panic() {
    with_default_timeout(|| {
        let workspace = Arc::new(MemoryWorkspace::new(PathBuf::from("/test/repo")));
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::clone(&workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);

        let mut initial_state = PipelineState::initial(1, 0);
        initial_state.phase = PipelinePhase::Planning;

        let mut handler = PanicHandler {
            state: initial_state.clone(),
        };

        let loop_config = EventLoopConfig { max_iterations: 10 };

        let res =
            run_event_loop_with_handler(&mut ctx, Some(initial_state), loop_config, &mut handler)
                .expect("event loop should return an EventLoopResult even on panic");

        assert!(res.completed, "panic recovery should terminate cleanly");
        assert_eq!(
            res.final_phase,
            PipelinePhase::Interrupted,
            "panic recovery should terminate in Interrupted phase"
        );

        let trace_path_buf = fixture.run_log_context.event_loop_trace();
        let trace_path = trace_path_buf.to_string_lossy();
        assert!(
            workspace.was_written(trace_path.as_ref()),
            "expected event loop to dump trace to {trace_path} on panic"
        );

        let trace = workspace
            .get_file(trace_path.as_ref())
            .expect("trace file should be readable");
        let last_line = trace
            .lines()
            .rfind(|l| !l.trim().is_empty())
            .expect("trace file should have at least one line");
        assert!(last_line.contains("\"kind\":\"final_state\""));
        assert!(
            last_line.contains("\"reason\":\"panic\"")
                || last_line.contains("\"reason\":\"max_iterations\""),
            "final_state reason should reflect panic recovery or max-iteration defensive termination"
        );
    });
}

#[test]
fn test_trace_records_additional_events() {
    with_default_timeout(|| {
        let workspace = Arc::new(MemoryWorkspace::new(PathBuf::from("/test/repo")));
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::clone(&workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);

        let mut initial_state = PipelineState::initial(1, 0);
        initial_state.phase = PipelinePhase::Planning;

        let mut handler = AdditionalEventsHandler {
            state: initial_state.clone(),
        };

        let loop_config = EventLoopConfig { max_iterations: 2 };

        let res =
            run_event_loop_with_handler(&mut ctx, Some(initial_state), loop_config, &mut handler)
                .expect("event loop should run");
        assert!(!res.completed);

        let trace_path_buf = fixture.run_log_context.event_loop_trace();
        let trace_path = trace_path_buf.to_string_lossy();
        let trace = workspace
            .get_file(trace_path.as_ref())
            .expect("trace file should be readable");
        assert!(
            trace.contains("\"event\":\"CheckpointSaved"),
            "expected trace to include additional CheckpointSaved event"
        );
        let last_line = trace
            .lines()
            .rfind(|l| !l.trim().is_empty())
            .expect("trace file should have at least one line");
        assert!(last_line.contains("\"kind\":\"final_state\""));
        assert!(last_line.contains("\"reason\":\"max_iterations\""));
    });
}

#[test]
fn test_trace_entry_phase_reflects_state_after_event_applied() {
    with_default_timeout(|| {
        let workspace = Arc::new(MemoryWorkspace::new(PathBuf::from("/test/repo")));
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::clone(&workspace) as Arc<dyn Workspace>);
        let mut ctx = fixture.ctx(None);

        let mut initial_state = PipelineState::initial(1, 0);
        initial_state.phase = PipelinePhase::Planning;

        let mut handler = PhaseChangingHandler {
            state: initial_state.clone(),
        };

        let loop_config = EventLoopConfig { max_iterations: 1 };

        let _res =
            run_event_loop_with_handler(&mut ctx, Some(initial_state), loop_config, &mut handler)
                .expect("event loop should run");

        let trace_path_buf = fixture.run_log_context.event_loop_trace();
        let trace_path = trace_path_buf.to_string_lossy();
        let trace = workspace
            .get_file(trace_path.as_ref())
            .expect("trace file should be readable");
        let first_line = trace
            .lines()
            .find(|l| !l.trim().is_empty())
            .expect("trace file should have at least one entry line");

        assert!(first_line.contains("\"event\":\"FinalizingStarted\""));
        assert!(
            first_line.contains("\"phase\":\"Finalizing\""),
            "expected trace phase to reflect state after applying FinalizingStarted"
        );
    });
}

#[test]
fn test_max_iterations_logs_trace_path_to_workspace_log() {
    with_default_timeout(|| {
        let workspace = Arc::new(MemoryWorkspace::new(PathBuf::from("/test/repo")));
        let mut fixture =
            IntegrationFixture::with_workspace(Arc::clone(&workspace) as Arc<dyn Workspace>);
        fixture.logger = Logger::new(fixture.colors)
            .with_workspace_log(Arc::clone(&workspace) as Arc<dyn Workspace>, LOG_PATH);
        let mut ctx = fixture.ctx(None);

        let mut initial_state = PipelineState::initial(1, 0);
        initial_state.phase = PipelinePhase::Planning;
        initial_state.context_cleaned = true;

        let mut handler = LoopingHandler {
            state: initial_state.clone(),
        };

        let loop_config = EventLoopConfig { max_iterations: 1 };

        let _res =
            run_event_loop_with_handler(&mut ctx, Some(initial_state), loop_config, &mut handler)
                .expect("event loop should run");

        let log = workspace
            .get_file(LOG_PATH)
            .expect("workspace log should be written");

        let trace_path_buf = fixture.run_log_context.event_loop_trace();
        let trace_path = trace_path_buf.to_string_lossy();
        assert!(
            log.contains(trace_path.as_ref()),
            "expected logs to mention trace path {trace_path}"
        );
    });
}
