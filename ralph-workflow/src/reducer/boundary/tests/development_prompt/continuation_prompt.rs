use super::*;
use crate::prompts::PromptHistoryEntry;

#[test]
fn test_prepare_development_prompt_same_agent_retry_replays_from_prompt_history_when_available() {
    use crate::reducer::ui_event::UIEvent;

    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    });

    let materialize = handler
        .materialize_development_inputs(&ctx, 0)
        .expect("materialize_development_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    let key = "development_0_same_agent_retry_1";
    let inputs = handler
        .state
        .prompt_inputs
        .development
        .as_ref()
        .expect("precondition: development inputs must be materialized");
    let prompt_content_id = crate::reducer::prompt_inputs::sha256_hex_str(&format!(
        "development_same_agent_retry:prompt:{}:plan:{}:prompt_consumer:{}:plan_consumer:{}",
        inputs.prompt.content_id_sha256,
        inputs.plan.content_id_sha256,
        inputs.prompt.consumer_signature_sha256,
        inputs.plan.consumer_signature_sha256,
    ));
    handler.state.prompt_history.insert(
        key.to_string(),
        PromptHistoryEntry::new("STORED-PROMPT".to_string(), Some(prompt_content_id)),
    );

    let result = handler
        .prepare_development_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_development_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/development_prompt.txt"))
        .expect("development prompt should be written");
    assert_eq!(prompt, "STORED-PROMPT");

    assert!(
        result.ui_events.iter().any(|e| matches!(
            e,
            UIEvent::PromptReplayHit {
                key: k,
                was_replayed: true
            } if k == key
        )),
        "Expected PromptReplayHit(was_replayed=true) for {key}; got: {:?}",
        result.ui_events
    );
    assert!(
        !result.additional_events.iter().any(|e| matches!(
            e,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured { key: k, .. })
                if k == key
        )),
        "Prompt replay should not emit PromptCaptured for {key}; got: {:?}",
        result.additional_events
    );
}

#[test]
fn test_prepare_development_prompt_same_agent_retry_uses_previous_prepared_prompt() {
    let marker = "<<<PREVIOUS_DEVELOPMENT_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/development_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);

    let result = {
        let ctx = fixture.ctx();

        let mut handler = MainEffectHandler::new(PipelineState {
            continuation: ContinuationState {
                same_agent_retry_count: 1,
                same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
                ..ContinuationState::new()
            },
            ..PipelineState::initial(1, 1)
        });

        let materialize = handler
            .materialize_development_inputs(&ctx, 0)
            .expect("materialize_development_inputs should succeed");
        handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
        for ev in materialize.additional_events {
            handler.state = crate::reducer::reduce(handler.state.clone(), ev);
        }

        handler
            .prepare_development_prompt(&ctx, 0, PromptMode::SameAgentRetry)
            .expect("prepare_development_prompt should succeed")
    };

    let prompt = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/development_prompt.txt"))
        .expect("development prompt should be written");

    assert!(
        prompt.contains(marker),
        "Same-agent retry should reuse the previously prepared prompt; got: {prompt}"
    );
    assert!(
        prompt.contains("## Retry Note (attempt 1)"),
        "Same-agent retry should prepend retry note; got: {prompt}"
    );
    assert!(
        !result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered { .. })
        )),
        "Same-agent retry should not emit TemplateRendered when replaying the stored prompt"
    );
}

#[test]
fn test_prepare_development_prompt_same_agent_retry_does_not_stack_retry_notes() {
    let marker = "<<<PREVIOUS_DEVELOPMENT_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_file(".agent/PLAN.md", "# Plan\n")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/development_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);

    {
        let ctx = fixture.ctx();

        let mut handler = MainEffectHandler::new(PipelineState {
            continuation: ContinuationState {
                same_agent_retry_count: 1,
                same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
                ..ContinuationState::new()
            },
            ..PipelineState::initial(1, 1)
        });

        let materialize = handler
            .materialize_development_inputs(&ctx, 0)
            .expect("materialize_development_inputs should succeed");
        handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
        for ev in materialize.additional_events {
            handler.state = crate::reducer::reduce(handler.state.clone(), ev);
        }

        handler
            .prepare_development_prompt(&ctx, 0, PromptMode::SameAgentRetry)
            .expect("prepare_development_prompt should succeed");

        handler.state.continuation.same_agent_retry_count = 2;
        handler
            .prepare_development_prompt(&ctx, 0, PromptMode::SameAgentRetry)
            .expect("prepare_development_prompt should succeed");
    }

    let prompt = fixture
        .workspace
        .read(std::path::Path::new(".agent/tmp/development_prompt.txt"))
        .expect("development prompt should be written");

    assert!(
        prompt.contains(marker),
        "Same-agent retry should keep the base prompt content; got: {prompt}"
    );
    assert_eq!(
        prompt.matches("## Retry Note").count(),
        1,
        "Expected exactly one retry note block, got: {prompt}"
    );
    assert!(
        prompt.contains("## Retry Note (attempt 2)"),
        "Expected retry note attempt 2 after second retry, got: {prompt}"
    );
    assert!(
        !prompt.contains("## Retry Note (attempt 1)"),
        "Expected previous retry note to be replaced, got: {prompt}"
    );
}

#[test]
fn test_prepare_development_prompt_continuation_emits_template_rendered() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            continuation_attempt: 1,
            previous_status: Some(crate::reducer::state::DevelopmentStatus::Partial),
            previous_summary: Some("Partial summary".to_string()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    });

    let result = handler
        .prepare_development_prompt(&ctx, 0, PromptMode::Continuation)
        .expect("prepare_development_prompt should succeed");

    assert!(
        result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered { .. })
        )),
        "Continuation prompt should emit TemplateRendered for log-based validation"
    );
}

#[test]
fn test_prepare_development_prompt_continuation_replay_skips_template_rendered() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState {
        continuation: ContinuationState {
            continuation_attempt: 1,
            previous_status: Some(crate::reducer::state::DevelopmentStatus::Partial),
            previous_summary: Some("Partial summary".to_string()),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    });
    // Insert into state.prompt_history (handler reads from self.state.prompt_history).
    let prompt_content_id = crate::reducer::prompt_inputs::sha256_hex_str(&format!(
        "development_continuation:attempt:{}:consumer:{}",
        handler.state.continuation.continuation_attempt,
        handler.state.agent_chain.consumer_signature_sha256(),
    ));
    handler.state.prompt_history.insert(
        "development_0_continuation_1".to_string(),
        PromptHistoryEntry::new(
            "stored continuation prompt".to_string(),
            Some(prompt_content_id),
        ),
    );

    let result = handler
        .prepare_development_prompt(&ctx, 0, PromptMode::Continuation)
        .expect("prepare_development_prompt should succeed");

    assert!(
        !result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered { .. })
        )),
        "Continuation prompt replay should skip TemplateRendered emission"
    );
}
