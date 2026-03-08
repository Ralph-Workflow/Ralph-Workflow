//! Template rendering tests for review prompt preparation.
//!
//! Verifies that templates are correctly expanded, placeholders are handled properly,
//! and required markers are included in the generated review prompts.

use super::super::super::common::TestFixture;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_registry::TemplateRegistry;
use crate::prompts::PromptHistoryEntry;
use crate::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{ContinuationState, PipelineState, PromptMode};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::fs;
use std::path::Path;
use tempfile::tempdir;

#[test]
fn test_prepare_review_prompt_writes_prompt_file_with_required_markers() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }
    let _ = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");

    assert!(
        prompt.contains("PROMPT.md.backup"),
        "review prompt should instruct reading PROMPT.md.backup"
    );
    assert!(
        prompt.contains("<ralph-issues>"),
        "review prompt should include XML output instructions"
    );
}

#[test]
fn test_prepare_review_prompt_emits_template_rendered_on_validation_failure() {
    let tempdir = tempdir().expect("create temp dir");
    let template_path = tempdir.path().join("review_xml.txt");
    fs::write(
        &template_path,
        "Plan:\n{{PLAN}}\nChanges:\n{{CHANGES}}\nMissing: {{MISSING}}\n",
    )
    .expect("write review template");

    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.template_context =
        TemplateContext::new(TemplateRegistry::new(Some(tempdir.path().to_path_buf())));
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed");

    match result.event {
        PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
            phase,
            template_name,
            log,
        }) => {
            assert_eq!(phase, PipelinePhase::Review);
            assert_eq!(template_name, "review_xml");
            assert!(log.unsubstituted.contains(&"MISSING".to_string()));
        }
        other => panic!("expected TemplateRendered event, got {other:?}"),
    }

    assert!(
        result.additional_events.iter().any(|event| matches!(
            event,
            PipelineEvent::Agent(AgentEvent::TemplateVariablesInvalid { missing_variables, .. })
                if missing_variables.contains(&"MISSING".to_string())
        )),
        "expected TemplateVariablesInvalid with missing variables"
    );
}

#[test]
fn test_prepare_review_prompt_allows_literal_placeholders_in_plan() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "{{MISSING}}\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }
    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed");

    assert!(matches!(result.event, PipelineEvent::Review(_)));
}

#[test]
fn test_prepare_review_prompt_normal_mode_ignores_retry_state() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    // Store a retry prompt with unresolved placeholders in state.prompt_history.
    // Normal mode must NOT replay this retry prompt.
    // Handlers read from self.state.prompt_history, so we insert there.
    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });
    handler.state.prompt_history.insert(
        "review_0".to_string(),
        PromptHistoryEntry::from_string("{{UNRESOLVED}}".to_string()),
    );

    let materialize = handler
        .materialize_review_inputs(&ctx, 0)
        .expect("materialize_review_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_review_prompt should succeed");

    // Replayed prompts are trusted and not re-validated, so we expect ReviewPromptPrepared
    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::PromptPrepared { .. })
    ));
}
