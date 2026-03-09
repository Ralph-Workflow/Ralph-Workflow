use super::super::common::TestFixture;
use crate::prompts::PromptHistoryEntry;
use crate::reducer::event::{
    AgentEvent, PipelineEvent, PipelinePhase, PromptInputEvent, ReviewEvent,
};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{ContinuationState, PipelineState, PromptInputKind, PromptMode};
use crate::reducer::ui_event::UIEvent;
use crate::workspace::Workspace;
use std::path::Path;

#[test]
fn test_prepare_review_prompt_uses_xsd_retry_prompt_key() {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_file(
            ".agent/tmp/issues.xml",
            &"x".repeat(crate::prompts::MAX_INLINE_CONTENT_SIZE + 10),
        )
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");

    assert!(
        result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured { key, .. })
                if key == "review_0_xsd_retry_1"
        )),
        "expected retry prompt to be captured with retry key via PromptCaptured event"
    );

    assert!(
        result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::OversizeDetected {
                kind: PromptInputKind::LastOutput,
                ..
            })
        )),
        "Expected OversizeDetected event for PromptInputKind::LastOutput during review XSD retry"
    );
    assert!(
        result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered { .. })
        )),
        "Review XSD retry should emit TemplateRendered for log-based validation"
    );
}

#[test]
fn test_prepare_review_prompt_xsd_retry_replays_from_prompt_history_when_content_id_matches() {
    // Arrange: create a stable last_output and XSD error so we can compute content-id.
    let last_output = "<ralph-issues>invalid</ralph-issues>";
    let xsd_error = "XSD validation failed: missing <status>";

    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_file(".agent/tmp/issues.xml", last_output)
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            last_review_xsd_error: Some(xsd_error.to_string()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let last_output_id = sha256_hex_str(last_output);
    let current_prompt_content_id =
        sha256_hex_str(&format!("review_xsd_retry|{xsd_error}|{last_output_id}"));

    let stored_prompt = "REPLAYED REVIEW XSD RETRY PROMPT";
    handler.state.prompt_history.insert(
        "review_0_xsd_retry_1".to_string(),
        PromptHistoryEntry::new(stored_prompt.to_string(), Some(current_prompt_content_id)),
    );

    // Act
    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");

    // Assert: prompt is replayed and PromptCaptured is not emitted.
    assert!(result.ui_events.iter().any(|ev| {
        matches!(
            ev,
            UIEvent::PromptReplayHit {
                key,
                was_replayed: true
            } if key == "review_0_xsd_retry_1"
        )
    }));
    assert!(
        !result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured { .. })
        )),
        "replayed prompts must not emit PromptCaptured"
    );

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");
    assert_eq!(prompt, stored_prompt);
}

#[test]
fn test_review_xsd_retry_oversize_detected_is_deduped_across_retries() {
    let large_last_output = "x".repeat(crate::prompts::MAX_INLINE_CONTENT_SIZE + 10);
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_file(".agent/tmp/issues.xml", &large_last_output)
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let first = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), first.event);
    for ev in first.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    let second = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");

    assert!(
        !second.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::OversizeDetected { kind: PromptInputKind::LastOutput, .. })
        )),
        "Expected OversizeDetected for LastOutput to be emitted only once for identical review XSD retry context"
    );
}

#[test]
fn test_prepare_review_prompt_xsd_retry_ignores_last_output_placeholders() {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_file(
            crate::files::llm_output_extraction::file_based_extraction::paths::ISSUES_XML,
            "{{MISSING}}",
        );

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });
    // Insert into state.prompt_history (handler reads from self.state.prompt_history).
    handler.state.prompt_history.insert(
        "review_0_xsd_retry_1".to_string(),
        PromptHistoryEntry::from_string("Last output was {{MISSING}}".to_string()),
    );

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");

    assert!(matches!(result.event, PipelineEvent::Review(_)));
}

#[test]
fn test_prepare_review_prompt_xsd_retry_ignores_xsd_error_placeholders() {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            last_review_xsd_error: Some("XSD error {{BROKEN}}".to_string()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::PromptPrepared { .. })
    ));
}

#[test]
fn test_prepare_review_prompt_uses_xsd_retry_template_name() {
    use crate::reducer::prompt_inputs::sha256_hex_str;

    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_file(".agent/tmp/issues.xml", "<ralph-issues>bad</ralph-issues>")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let xsd_error = "XML output failed validation. Provide valid XML output.";
    // In XSD retry, `.agent/tmp/last_output.xml` is materialized from `.agent/tmp/issues.xml`.
    let last_output_id = sha256_hex_str("<ralph-issues>bad</ralph-issues>");
    let current_prompt_content_id =
        sha256_hex_str(&format!("review_xsd_retry|{xsd_error}|{last_output_id}"));

    // Insert into state.prompt_history (handler reads from self.state.prompt_history).
    handler.state.prompt_history.insert(
        "review_0_xsd_retry_1".to_string(),
        PromptHistoryEntry::new(
            "retry prompt {{UNRESOLVED}}".to_string(),
            Some(current_prompt_content_id),
        ),
    );

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");

    assert!(
        matches!(result.event, PipelineEvent::Review(_)),
        "expected retry prompt to be prepared even if prompt_history contains stale placeholders"
    );
    assert!(result.ui_events.iter().any(|ev| {
        matches!(
            ev,
            UIEvent::PromptReplayHit {
                key,
                was_replayed: true
            } if key == "review_0_xsd_retry_1"
        )
    }));
    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");
    assert!(
        prompt.contains("retry prompt {{UNRESOLVED}}"),
        "expected stored prompt to be replayed for deterministic resume"
    );
}

#[test]
fn test_prepare_review_prompt_xsd_retry_allows_missing_issues_xml() {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed without issues.xml");

    assert!(matches!(result.event, PipelineEvent::Review(_)));
    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .expect("review prompt file should be written");
    assert!(
        prompt.contains("XSD VALIDATION FAILED - FIX XML ONLY"),
        "expected review XSD retry template to be used"
    );
}

#[test]
fn test_prepare_fix_prompt_uses_xsd_retry_template_name() {
    use crate::reducer::prompt_inputs::sha256_hex_str;

    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "Issue\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    // Match `fix_flow.rs` XSD retry prompt content-id computation.
    let prompt_id = sha256_hex_str("# Prompt backup\n");
    let plan_id = sha256_hex_str("# Plan\n");
    let issues_id = sha256_hex_str("Issue\n");
    let xsd_error = "XML output failed validation. Provide valid XML output.";
    let last_output_id = sha256_hex_str("");
    let current_prompt_content_id = sha256_hex_str(&format!(
        "fix_xsd_retry|{prompt_id}|{plan_id}|{issues_id}|{xsd_error}|{last_output_id}"
    ));

    // Insert into state.prompt_history (handler reads from self.state.prompt_history).
    handler.state.prompt_history.insert(
        "fix_0_xsd_retry_1".to_string(),
        PromptHistoryEntry::new(
            "retry prompt {{UNRESOLVED}}".to_string(),
            Some(current_prompt_content_id),
        ),
    );

    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_fix_prompt should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::FixPromptPrepared { .. })
    ));
}

#[test]
fn test_prepare_fix_prompt_xsd_retry_ignores_xsd_error_placeholders() {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "Issue\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            last_fix_xsd_error: Some("XSD error {{BROKEN}}".to_string()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_fix_prompt should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(ReviewEvent::FixPromptPrepared { .. })
    ));
}

#[test]
fn test_prepare_fix_prompt_xsd_retry_reports_missing_xsd_error() {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "Issue\n")
        .with_file(".agent/tmp/fix_result.xml", "<ralph-fix-result/>")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            last_fix_xsd_error: Some(String::new()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_fix_prompt should succeed");

    assert!(result.ui_events.iter().any(|ev| matches!(
        ev,
        crate::reducer::ui_event::UIEvent::PromptReplayHit { key, was_replayed: false }
            if key == "fix_0_xsd_retry_1"
    )));

    match result.event {
        PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
            phase,
            template_name,
            log,
        }) => {
            assert_eq!(phase, PipelinePhase::Review);
            assert_eq!(template_name, "fix_mode_xsd_retry");
            assert!(log.unsubstituted.contains(&"XSD_ERROR".to_string()));
        }
        other => panic!("expected TemplateRendered event, got {other:?}"),
    }

    assert!(
        result.additional_events.iter().any(|event| matches!(
            event,
            PipelineEvent::Agent(AgentEvent::TemplateVariablesInvalid { missing_variables, .. })
                if missing_variables.contains(&"XSD_ERROR".to_string())
        )),
        "expected TemplateVariablesInvalid with missing variables"
    );
}

#[test]
fn test_prepare_review_prompt_xsd_retry_emits_prompt_replay_hit_on_template_validation_early_return(
) {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/DIFF.backup", "diff --git a/a b/a\n+change\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            invalid_output_attempts: 1,
            last_review_xsd_error: Some(String::new()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(0, 1)
    });

    let result = handler
        .prepare_review_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_review_prompt should succeed");

    assert!(result.ui_events.iter().any(|ev| matches!(
        ev,
        UIEvent::PromptReplayHit { key, was_replayed: false }
            if key == "review_0_xsd_retry_1"
    )));

    assert!(matches!(
        result.event,
        PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered { .. })
    ));
}

#[test]
fn test_prepare_fix_prompt_uses_prompt_history_replay() {
    use crate::reducer::prompt_inputs::sha256_hex_str;

    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "Issue\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let prompt_id = sha256_hex_str("# Prompt backup\n");
    let plan_id = sha256_hex_str("# Plan\n");
    let issues_id = sha256_hex_str("Issue\n");
    let content_id = sha256_hex_str(&format!("fix_xml|{prompt_id}|{plan_id}|{issues_id}"));

    // Insert into state.prompt_history (handler reads from self.state.prompt_history).
    handler.state.prompt_history.insert(
        "fix_0".to_string(),
        PromptHistoryEntry::new("REPLAYED PROMPT".to_string(), Some(content_id)),
    );
    handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_fix_prompt should succeed");

    let content = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt should be written");
    assert!(content.contains("REPLAYED PROMPT"));
}

#[test]
fn test_prepare_fix_prompt_does_not_replay_legacy_entry_without_content_id_when_current_inputs_have_content_id(
) {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_file(".agent/ISSUES.md", "Issue\n");

    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.prompt_history.insert(
        "fix_0".to_string(),
        PromptHistoryEntry::from_string("LEGACY REPLAY".to_string()),
    );

    let result = handler
        .prepare_fix_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_fix_prompt should succeed");

    assert!(result.ui_events.iter().any(|ev| matches!(
        ev,
        crate::reducer::ui_event::UIEvent::PromptReplayHit { key, was_replayed: false }
            if key == "fix_0"
    )));

    let content = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/fix_prompt.txt"))
        .expect("fix prompt should be written");
    assert!(
        !content.contains("LEGACY REPLAY"),
        "legacy entry must not be replayed when current inputs have a content-id"
    );
}

#[test]
fn test_fix_mode_xsd_retry_template_mentions_illegal_control_characters() {
    let template = include_str!("../../../../prompts/templates/fix_mode_xsd_retry.txt");
    assert!(
        template.contains(
            r"Illegal control characters (NUL byte, etc.) - common: \u0000 instead of \u00A0"
        ),
        "Expected fix_mode_xsd_retry template to mention illegal control characters"
    );
}

#[test]
fn test_fix_mode_xsd_retry_template_lists_fix_result_status_values() {
    let template = include_str!("../../../../prompts/templates/fix_mode_xsd_retry.txt");
    assert!(
        template.contains("all_issues_addressed")
            && template.contains("issues_remain")
            && template.contains("no_issues_found"),
        "Expected fix_mode_xsd_retry template to list fix-result status values"
    );
}
