use super::common::TestFixture;
use crate::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{
    AgentChainState, ContinuationState, PipelineState, PromptMode, SameAgentRetryReason,
};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::io;
use std::path::{Path, PathBuf};

use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_registry::TemplateRegistry;
use std::fs;
use tempfile::tempdir;

#[derive(Debug, Clone)]
struct WriteFailingWorkspace {
    inner: MemoryWorkspace,
    forbidden_write_path: PathBuf,
}

impl WriteFailingWorkspace {
    fn new(inner: MemoryWorkspace, forbidden_write_path: PathBuf) -> Self {
        Self {
            inner,
            forbidden_write_path,
        }
    }
}

impl Workspace for WriteFailingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        if relative == self.forbidden_write_path.as_path() {
            return Err(io::Error::other(format!(
                "write forbidden for {}",
                self.forbidden_write_path.display()
            )));
        }
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}

fn init_agent_chain(handler: &mut MainEffectHandler) {
    handler.state.agent_chain = AgentChainState::initial().with_agents(
        vec!["dev-agent".to_string()],
        vec![vec![]],
        crate::agents::AgentRole::Developer,
    );
}

fn seed_materialized_planning_inputs(handler: &mut MainEffectHandler) {
    handler.state.prompt_inputs.planning =
        Some(crate::reducer::state::MaterializedPlanningInputs {
            iteration: 0,
            prompt: crate::reducer::state::MaterializedPromptInput {
                kind: crate::reducer::state::PromptInputKind::Prompt,
                content_id_sha256: "id".to_string(),
                consumer_signature_sha256: handler.state.agent_chain.consumer_signature_sha256(),
                original_bytes: 0,
                final_bytes: 0,
                model_budget_bytes: None,
                inline_budget_bytes: Some(crate::prompts::MAX_INLINE_CONTENT_SIZE as u64),
                representation: crate::reducer::state::PromptInputRepresentation::Inline,
                reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
            },
        });
}

#[must_use]
fn same_agent_retry_state(retry_count: u32) -> PipelineState {
    PipelineState {
        continuation: ContinuationState {
            same_agent_retry_count: retry_count,
            same_agent_retry_reason: Some(SameAgentRetryReason::Timeout),
            ..ContinuationState::new()
        },
        ..PipelineState::initial(1, 0)
    }
}

fn materialize_and_reduce(
    handler: &mut MainEffectHandler,
    ctx: &crate::phases::PhaseContext<'_>,
    iteration: u32,
) {
    let materialize = handler
        .materialize_planning_inputs(ctx, iteration)
        .expect("materialize_planning_inputs should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), materialize.event);
    for ev in materialize.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }
}

#[test]
fn test_prepare_planning_prompt_xsd_retry_captures_prompt_and_replays_from_history() {
    use crate::prompts::PromptHistoryEntry;
    use crate::reducer::ui_event::UIEvent;

    let invalid_xml = "<ralph-plan><ralph-status>incomplete</ralph-status>";
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/plan.xml", invalid_xml);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);
    handler.state.continuation.xsd_retry_count = 1;

    let first = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_planning_prompt should succeed");

    let key = "planning_0_xsd_retry_1";
    assert!(
        first.ui_events.iter().any(|e| matches!(
            e,
            UIEvent::PromptReplayHit {
                key: k,
                was_replayed: false
            } if k == key
        )),
        "Expected PromptReplayHit(was_replayed=false) for {key}; got: {:?}",
        first.ui_events
    );
    assert!(
        first.additional_events.iter().any(|e| matches!(
            e,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: k,
                content_id: Some(id),
                ..
            }) if k == key && id.len() == 64
        )),
        "Expected PromptCaptured for {key}; got: {:?}",
        first.additional_events
    );

    // Reduce events into handler state to simulate checkpointed prompt history.
    handler.state = crate::reducer::reduce(handler.state.clone(), first.event);
    for ev in first.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    // Sanity: ensure history contains the captured prompt.
    assert!(
        handler
            .state
            .prompt_history
            .get(key)
            .is_some_and(|e: &PromptHistoryEntry| !e.content.trim().is_empty()),
        "Expected non-empty stored prompt content for {key}"
    );

    let second = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_planning_prompt should succeed");
    assert!(
        second.ui_events.iter().any(|e| matches!(
            e,
            UIEvent::PromptReplayHit {
                key: k,
                was_replayed: true
            } if k == key
        )),
        "Expected PromptReplayHit(was_replayed=true) for {key}; got: {:?}",
        second.ui_events
    );
    assert!(
        !second.additional_events.iter().any(|e| matches!(
            e,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured { key: k, .. })
                if k == key
        )),
        "Prompt replay should not emit PromptCaptured for {key}; got: {:?}",
        second.additional_events
    );
}

#[test]
fn test_planning_xsd_retry_rematerializes_last_output_when_state_present_but_file_missing() {
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::prompt_inputs::sha256_hex_str;
    use crate::reducer::state::{
        MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    };
    use crate::reducer::state::{MaterializedXsdRetryLastOutput, PromptMaterializationReason};
    use std::path::Path;

    let invalid_xml = "<ralph-plan><ralph-status>incomplete</ralph-status>";
    let workspace = MemoryWorkspace::new_test()
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/plan.xml", invalid_xml);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);
    handler.state.continuation.xsd_retry_count = 1;

    // Arrange: reducer state claims last_output was already materialized, but the file is missing.
    let content_id_sha256 = sha256_hex_str(invalid_xml);
    let consumer_signature_sha256 = handler.state.agent_chain.consumer_signature_sha256();
    handler.state.prompt_inputs.xsd_retry_last_output = Some(MaterializedXsdRetryLastOutput {
        phase: PipelinePhase::Planning,
        scope_id: 0,
        last_output: MaterializedPromptInput {
            kind: PromptInputKind::LastOutput,
            content_id_sha256,
            consumer_signature_sha256,
            original_bytes: invalid_xml.len() as u64,
            final_bytes: invalid_xml.len() as u64,
            model_budget_bytes: None,
            inline_budget_bytes: Some(crate::prompts::MAX_INLINE_CONTENT_SIZE as u64),
            representation: PromptInputRepresentation::FileReference {
                path: Path::new(".agent/tmp/last_output.xml").to_path_buf(),
            },
            reason: PromptMaterializationReason::PolicyForcedReference,
        },
    });

    assert!(
        !ctx.workspace
            .exists(Path::new(".agent/tmp/last_output.xml")),
        "precondition: last_output.xml must be missing"
    );

    let result = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_planning_prompt should succeed");

    assert!(
        result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::XsdRetryLastOutputMaterialized {
                phase: PipelinePhase::Planning,
                scope_id: 0,
                ..
            })
        )),
        "Expected rematerialization event when last_output.xml is missing"
    );
    assert!(
        ctx.workspace
            .exists(Path::new(".agent/tmp/last_output.xml")),
        "Expected last_output.xml to be re-written when missing"
    );
}

#[test]
fn test_prepare_planning_prompt_same_agent_retry_replays_from_prompt_history_when_available() {
    use crate::reducer::ui_event::UIEvent;

    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(same_agent_retry_state(1));
    init_agent_chain(&mut handler);
    materialize_and_reduce(&mut handler, &ctx, 0);

    let key = "planning_0_same_agent_retry_1";
    handler.state.prompt_history.insert(
        key.to_string(),
        crate::prompts::PromptHistoryEntry::from_string("STORED-PROMPT".to_string()),
    );

    let result = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_planning_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/planning_prompt.txt"))
        .expect("planning prompt should be written");
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
fn test_prepare_planning_prompt_normal_captures_prompt_with_content_id() {
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "# Prompt\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);
    seed_materialized_planning_inputs(&mut handler);

    let result = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_planning_prompt should succeed");

    assert!(result.additional_events.iter().any(|ev| matches!(
        ev,
        PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
            key,
            content_id: Some(id),
            ..
        }) if key == "planning_0" && id.len() == 64
    )));
}

#[test]
fn test_prepare_planning_prompt_same_agent_retry_uses_previous_prepared_prompt() {
    let marker = "<<<PREVIOUS_PLANNING_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/planning_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(same_agent_retry_state(1));

    materialize_and_reduce(&mut handler, &ctx, 0);

    let result = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_planning_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/planning_prompt.txt"))
        .expect("planning prompt should be written");

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
        "Same-agent retry should not emit TemplateRendered when reusing the stored prompt"
    );
}

#[test]
fn test_prepare_planning_prompt_emits_template_rendered_on_validation_failure() {
    let tempdir = tempdir().expect("create temp dir");
    let template_path = tempdir.path().join("planning_xml.txt");
    fs::write(
        &template_path,
        "Prompt:\n{{PROMPT}}\nMissing: {{MISSING}}\n",
    )
    .expect("write planning template");

    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "# Prompt\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    fixture.template_context =
        TemplateContext::new(TemplateRegistry::new(Some(tempdir.path().to_path_buf())));

    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);
    seed_materialized_planning_inputs(&mut handler);

    let result = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_planning_prompt should succeed");

    assert!(result.ui_events.iter().any(|ev| matches!(
        ev,
        crate::reducer::ui_event::UIEvent::PromptReplayHit { key, was_replayed: false }
            if key == "planning_0"
    )));

    match result.event {
        PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
            phase,
            template_name,
            log,
        }) => {
            assert_eq!(phase, PipelinePhase::Planning);
            assert_eq!(template_name, "planning_xml");
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
fn test_prepare_planning_prompt_workspace_write_failure_is_non_fatal() {
    // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
    // When prompt file write fails, the handler logs a warning and continues successfully.
    let inner = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_dir(".agent/tmp")
        .with_file(
            ".agent/tmp/planning_prompt.txt",
            "<<<PREVIOUS_PLANNING_PROMPT_MARKER>>>",
        );
    let failing_ws =
        WriteFailingWorkspace::new(inner, PathBuf::from(".agent/tmp/planning_prompt.txt"));

    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx_with_workspace(&failing_ws);

    let mut handler = MainEffectHandler::new(same_agent_retry_state(1));

    materialize_and_reduce(&mut handler, &ctx, 0);

    // Per AC #5: Write failure should NOT return an error; it should succeed
    // with a warning logged instead.
    let result = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_planning_prompt should succeed even when write fails (non-fatal)");

    // Verify that the prompt was prepared in memory even though the write failed
    assert!(
        matches!(
            result.event,
            PipelineEvent::Planning(crate::reducer::event::PlanningEvent::PromptPrepared { .. })
        ),
        "should emit Planning(PromptPrepared) event even when write fails, got: {:?}",
        result.event
    );
}

#[test]
fn test_prepare_planning_prompt_same_agent_retry_does_not_stack_retry_notes() {
    let marker = "<<<PREVIOUS_PLANNING_PROMPT_MARKER>>>";
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "Prompt")
        .with_dir(".agent/tmp")
        .with_file(".agent/tmp/planning_prompt.txt", marker);

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(same_agent_retry_state(1));

    materialize_and_reduce(&mut handler, &ctx, 0);

    handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_planning_prompt should succeed");

    handler.state.continuation.same_agent_retry_count = 2;
    handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::SameAgentRetry)
        .expect("prepare_planning_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/planning_prompt.txt"))
        .expect("planning prompt should be written");

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
fn test_prepare_planning_prompt_uses_references_for_oversize_prompt() {
    let large_prompt = "x".repeat(crate::prompts::MAX_INLINE_CONTENT_SIZE + 10);
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", &large_prompt)
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);

    materialize_and_reduce(&mut handler, &ctx, 0);

    // Need a fresh mutable ctx after materialize_and_reduce borrowed it
    let ctx = fixture.ctx();
    handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::Normal)
        .expect("prepare_planning_prompt should succeed");

    let prompt = fixture
        .workspace
        .read(Path::new(".agent/tmp/planning_prompt.txt"))
        .expect("planning prompt file should be written");

    assert!(
        prompt.contains("PROMPT.md.backup"),
        "planning prompt should reference PROMPT.md.backup when prompt is oversize"
    );
    assert!(
        !prompt.contains(&large_prompt[..100]),
        "planning prompt should not inline the large prompt content"
    );
}

#[test]
fn test_materialize_planning_inputs_errors_when_prompt_missing() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);

    let result = handler.materialize_planning_inputs(&ctx, 0);
    assert!(
        result.is_err(),
        "Expected Err when PROMPT.md is missing, got {result:?}",
    );
}

#[test]
fn test_prepare_planning_prompt_errors_when_prompt_missing() {
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    // Seed reducer state with materialized planning inputs so prepare_planning_prompt can run.
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);
    seed_materialized_planning_inputs(&mut handler);

    let result = handler.prepare_planning_prompt(&ctx, 0, PromptMode::Normal);
    assert!(
        result.is_err(),
        "Expected Err when PROMPT.md is missing, got {result:?}",
    );
}

#[test]
fn test_prepare_planning_prompt_errors_when_inputs_not_materialized() {
    let workspace = MemoryWorkspace::new_test()
        .with_file("PROMPT.md", "# Prompt\n")
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    let result = handler.prepare_planning_prompt(&ctx, 0, PromptMode::Normal);
    assert!(
        result.is_err(),
        "Expected Err when planning inputs are missing, got {result:?}",
    );
}

#[test]
fn test_prepare_planning_prompt_xsd_retry_emits_oversize_detected_for_last_output() {
    use crate::reducer::state::PromptInputKind;

    let large_last_output = "x".repeat(crate::prompts::MAX_INLINE_CONTENT_SIZE + 10);
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/plan.xml", &large_last_output)
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);

    let result = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_planning_prompt should succeed");

    assert!(
        result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::OversizeDetected {
                kind: PromptInputKind::LastOutput,
                ..
            })
        )),
        "Expected OversizeDetected event for PromptInputKind::LastOutput during planning XSD retry"
    );
    assert!(
        result.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered { .. })
        )),
        "Planning XSD retry should emit TemplateRendered for log-based validation"
    );
}

#[test]
fn test_planning_xsd_retry_oversize_detected_is_deduped_across_retries() {
    use crate::reducer::state::PromptInputKind;

    let large_last_output = "x".repeat(crate::prompts::MAX_INLINE_CONTENT_SIZE + 10);
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/plan.xml", &large_last_output)
        .with_dir(".agent/tmp");

    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    init_agent_chain(&mut handler);

    let first = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_planning_prompt should succeed");
    handler.state = crate::reducer::reduce(handler.state.clone(), first.event);
    for ev in first.additional_events {
        handler.state = crate::reducer::reduce(handler.state.clone(), ev);
    }

    let second = handler
        .prepare_planning_prompt(&ctx, 0, PromptMode::XsdRetry)
        .expect("prepare_planning_prompt should succeed");

    assert!(
        !second.additional_events.iter().any(|ev| matches!(
            ev,
            PipelineEvent::PromptInput(PromptInputEvent::OversizeDetected { kind: PromptInputKind::LastOutput, .. })
        )),
        "Expected OversizeDetected for LastOutput to be emitted only once for identical planning XSD retry context"
    );
}
