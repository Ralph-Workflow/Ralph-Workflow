use super::super::super::common::TestFixture;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_registry::TemplateRegistry;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::state::{PipelineState, PromptMode};
use crate::reducer::ui_event::UIEvent;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::fs;
use std::path::Path;
use tempfile::tempdir;

#[test]
fn test_prepare_fix_prompt_allows_literal_placeholders_in_issues() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "{{MISSING}}\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_fix_prompt should succeed");

    assert!(matches!(result.event, PipelineEvent::Review(_)));
    assert!(
        result.additional_events.iter().any(|event| matches!(
            event,
            PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
                phase: PipelinePhase::Review,
                template_name,
                log,
            }) if template_name == "fix_mode" && log.is_complete()
        )),
        "expected TemplateRendered event for fix prompt"
    );
}

#[test]
fn test_prepare_fix_prompt_emits_prompt_replay_hit_on_template_validation_failure() {
    let tempdir = tempdir().expect("create temp dir");
    let template_path = tempdir.path().join("fix_mode.txt");
    fs::write(
        &template_path,
        "Issues:\n{{ISSUES}}\nMissing: {{MISSING}}\n",
    )
    .expect("write fix template");

    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.template_context =
        TemplateContext::new(TemplateRegistry::new(Some(tempdir.path().to_path_buf())));
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_fix_prompt should succeed");

    assert!(
        result.ui_events.iter().any(|ev| matches!(
            ev,
            UIEvent::PromptReplayHit {
                key,
                was_replayed: false
            } if key == "fix_0"
        )),
        "expected PromptReplayHit observability event on template validation early return"
    );

    assert!(matches!(
        result.event,
        PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
            phase: PipelinePhase::Review,
            template_name,
            ..
        }) if template_name == "fix_mode"
    ));
}

#[test]
fn test_prepare_fix_prompt_embeds_sentinel_when_prompt_backup_missing() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "<issues/>\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let _ = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_fix_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");
    assert!(
        prompt.contains("[MISSING INPUT: .agent/PROMPT.md.backup]"),
        "expected missing prompt backup sentinel in fix prompt; got: {prompt}"
    );
}

#[test]
fn test_prepare_fix_prompt_embeds_sentinel_when_issues_missing() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let _ = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_fix_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt file should be written");
    assert!(
        prompt.contains("[MISSING INPUT: .agent/ISSUES.md]"),
        "expected missing issues sentinel in fix prompt; got: {prompt}"
    );
}
