//! Planning phase helper functions.
//!
//! Pure helper functions extracted from planning.rs to keep file size under the
//! 1000-line boundary module limit. These functions are called by the
//! `MainEffectHandler` implementation in planning.rs.

use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::planning::{apply_same_agent_retry_preamble, planning_prompt_content_id};
use crate::phases::PhaseContext;
use crate::prompts::content_reference::PromptContentReference;
use crate::prompts::{
    prompt_planning_xml_with_references, prompt_planning_xml_with_references_and_log,
    prompt_planning_xsd_retry_with_context_files_and_log, PromptScopeKey, RetryMode,
    SessionCapabilities,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;

pub(in crate::reducer::boundary) const PLANNING_PROMPT_PATH: &str =
    ".agent/tmp/planning_prompt.txt";

// ---------------------------------------------------------------------------
// Outcome mapping
// ---------------------------------------------------------------------------

/// Pure outcome mapping: given validity and the current phase, return the
/// completion event and any UI transition events.
///
/// Extracted so callers receive a typed `(PipelineEvent, Vec<UIEvent>)` that can
/// be unit-tested without constructing `MainEffectHandler` or a `PhaseContext`.
pub(in crate::reducer::boundary) fn planning_outcome(
    iteration: u32,
    valid: bool,
    current_phase: PipelinePhase,
) -> (PipelineEvent, Vec<UIEvent>) {
    let ui_events: Vec<_> = valid
        .then_some(UIEvent::PhaseTransition {
            from: Some(current_phase),
            to: PipelinePhase::Development,
        })
        .into_iter()
        .collect();
    (
        PipelineEvent::plan_generation_completed(iteration, valid),
        ui_events,
    )
}

// ---------------------------------------------------------------------------
// Input helpers
// ---------------------------------------------------------------------------

pub(in crate::reducer::boundary) fn get_planning_inputs(
    handler: &crate::reducer::boundary::MainEffectHandler,
    iteration: u32,
) -> std::result::Result<
    &crate::reducer::state::MaterializedPlanningInputs,
    crate::reducer::event::ErrorEvent,
> {
    handler
        .state
        .prompt_inputs
        .planning
        .as_ref()
        .filter(|p| p.iteration == iteration)
        .ok_or(crate::reducer::event::ErrorEvent::PlanningInputsNotMaterialized { iteration })
}

pub(in crate::reducer::boundary) fn apply_planning_oversize_events(
    result: EffectResult,
    original_bytes: u64,
    inline_budget_bytes: u64,
    content_id_sha256: &str,
) -> EffectResult {
    if original_bytes > inline_budget_bytes {
        let result = result.with_ui_event(UIEvent::AgentActivity {
            agent: "pipeline".to_string(),
            message: format!(
                "Oversize PROMPT: {} KB > {} KB; using file reference",
                original_bytes / 1024,
                inline_budget_bytes / 1024
            ),
        });
        result.with_additional_event(PipelineEvent::prompt_input_oversize_detected(
            PipelinePhase::Planning,
            PromptInputKind::Prompt,
            content_id_sha256.to_string(),
            original_bytes,
            inline_budget_bytes,
            "inline-embedding".to_string(),
        ))
    } else {
        result
    }
}

// ---------------------------------------------------------------------------
// Same-agent retry helpers
// ---------------------------------------------------------------------------

pub(in crate::reducer::boundary) fn build_same_agent_scope_and_ids(
    handler: &crate::reducer::boundary::MainEffectHandler,
    iteration: u32,
    prompt_info: &MaterializedPromptInput,
) -> (PromptScopeKey, String, String) {
    let scope_key = PromptScopeKey::for_planning(
        iteration,
        RetryMode::SameAgent {
            count: handler.state.continuation.same_agent_retry_count,
        },
        handler.state.recovery_epoch,
    );
    let prompt_content_id = planning_prompt_content_id(
        "planning_same_agent_retry",
        &prompt_info.content_id_sha256,
        &prompt_info.consumer_signature_sha256,
    );
    let prompt_key = scope_key.to_string();
    (scope_key, prompt_key, prompt_content_id)
}

pub(in crate::reducer::boundary) fn build_same_agent_retry_capture(
    ctx: &PhaseContext<'_>,
    handler: &crate::reducer::boundary::MainEffectHandler,
    iteration: u32,
    prompt_ref: &PromptContentReference,
    retry_preamble: &str,
    prompt_info: &MaterializedPromptInput,
) -> std::result::Result<
    (
        PlanningPromptCapture,
        Option<crate::prompts::SubstitutionLog>,
    ),
    Box<EffectResult>,
> {
    let (scope_key, prompt_key, prompt_content_id) =
        build_same_agent_scope_and_ids(handler, iteration, prompt_info);
    let (prompt, was_replayed, should_validate) =
        super::get_stored_or_generate_prompt_with_validation(
            &scope_key,
            &handler.state.prompt_history,
            Some(&prompt_content_id),
            || load_same_agent_retry_base_prompt(ctx, prompt_ref, retry_preamble),
        );
    let rendered_log = gen_planning_normal_rendered_log(
        ctx,
        prompt_ref,
        prompt_needs_validation(should_validate, was_replayed),
        &prompt_key,
        was_replayed,
    )?;
    let capture = PlanningPromptCapture {
        prompt,
        prompt_key: Some(prompt_key),
        was_replayed,
        prompt_content_id: Some(prompt_content_id),
    };
    Ok((capture, rendered_log))
}

pub(in crate::reducer::boundary) fn prompt_needs_validation(
    should_validate: bool,
    was_replayed: bool,
) -> bool {
    should_validate && !was_replayed
}

pub(in crate::reducer::boundary) fn write_planning_prompt(ctx: &PhaseContext<'_>, prompt: &str) {
    if let Err(err) = ctx.workspace.write(Path::new(PLANNING_PROMPT_PATH), prompt) {
        ctx.logger.warn(&format!(
            "Failed to write planning prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
        ));
    }
}

/// Load the base prompt for a same-agent retry and return (prompt_with_preamble, should_validate).
///
/// If a previous prompt exists on disk, strips its preamble and reuses it (no validation needed).
/// Otherwise generates a fresh prompt from templates (validation required).
pub(in crate::reducer::boundary) fn load_same_agent_retry_base_prompt(
    ctx: &PhaseContext<'_>,
    prompt_ref: &crate::prompts::content_reference::PromptContentReference,
    retry_preamble: &str,
) -> (String, bool) {
    let (base_prompt, should_validate) = ctx
        .workspace
        .read(Path::new(PLANNING_PROMPT_PATH))
        .map_or_else(
            |_| {
                (
                    prompt_planning_xml_with_references(
                        ctx.template_context,
                        prompt_ref,
                        ctx.workspace,
                        SessionCapabilities::new(
                            &CapabilitySet::defaults_for_drain(SessionDrain::Planning),
                            &PolicyFlagSet::defaults_for_drain(SessionDrain::Planning),
                        ),
                    ),
                    true,
                )
            },
            |previous_prompt| {
                (
                    super::retry_guidance::strip_existing_same_agent_retry_preamble(
                        &previous_prompt,
                    )
                    .to_string(),
                    false,
                )
            },
        );
    (
        apply_same_agent_retry_preamble(retry_preamble, &base_prompt),
        should_validate,
    )
}

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

pub(in crate::reducer::boundary) struct PlanningPromptCapture {
    pub prompt: String,
    pub prompt_key: Option<String>,
    pub was_replayed: bool,
    pub prompt_content_id: Option<String>,
}

// ---------------------------------------------------------------------------
// File I/O helpers
// ---------------------------------------------------------------------------

pub(in crate::reducer::boundary) fn ensure_planning_tmp_dir(ctx: &PhaseContext<'_>) -> Result<()> {
    let tmp_dir = Path::new(".agent/tmp");
    if !ctx.workspace.exists(tmp_dir) {
        ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
            crate::reducer::event::ErrorEvent::WorkspaceCreateDirAllFailed {
                path: tmp_dir.display().to_string(),
                kind: crate::reducer::event::WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;
    }
    Ok(())
}

pub(in crate::reducer::boundary) fn read_planning_xsd_retry_last_output(
    ctx: &PhaseContext<'_>,
) -> Result<String> {
    ctx.workspace
        .read(Path::new(xml_paths::PLAN_XML))
        .or_else(|err| {
            if err.kind() == std::io::ErrorKind::NotFound {
                let processed_path = Path::new(".agent/tmp/plan.xml.processed");
                ctx.workspace.read(processed_path).inspect(|_output| {
                    ctx.logger
                        .info("XSD retry: using archived .processed file as last output");
                })
            } else {
                Err(err)
            }
        })
        .map_err(|err| {
            crate::reducer::event::ErrorEvent::WorkspaceReadFailed {
                path: xml_paths::PLAN_XML.to_string(),
                kind: crate::reducer::event::WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
            .into()
        })
}

// ---------------------------------------------------------------------------
// XSD retry helpers
// ---------------------------------------------------------------------------

pub(in crate::reducer::boundary) struct XsdRetryLastOutputParams<'a> {
    pub content_id_sha256: &'a str,
    pub consumer_signature_sha256: &'a str,
    pub inline_budget_bytes: u64,
    pub last_output_bytes: u64,
}

pub(in crate::reducer::boundary) fn is_xsd_retry_last_output_already_materialized(
    handler: &crate::reducer::boundary::MainEffectHandler,
    ctx: &PhaseContext<'_>,
    iteration: u32,
    params: &XsdRetryLastOutputParams<'_>,
) -> bool {
    handler
        .state
        .prompt_inputs
        .xsd_retry_last_output
        .as_ref()
        .is_some_and(|m| {
            m.phase == PipelinePhase::Planning
                && m.scope_id == iteration
                && m.last_output.content_id_sha256 == params.content_id_sha256
                && m.last_output.consumer_signature_sha256 == params.consumer_signature_sha256
                && ctx
                    .workspace
                    .exists(std::path::Path::new(".agent/tmp/last_output.xml"))
        })
}

pub(in crate::reducer::boundary) fn build_xsd_retry_last_output_events(
    iteration: u32,
    input: MaterializedPromptInput,
    content_id_sha256: &str,
    last_output_bytes: u64,
    inline_budget_bytes: u64,
) -> Vec<PipelineEvent> {
    std::iter::once(PipelineEvent::xsd_retry_last_output_materialized(
        PipelinePhase::Planning,
        iteration,
        input,
    ))
    .chain((last_output_bytes > inline_budget_bytes).then_some(
        PipelineEvent::prompt_input_oversize_detected(
            PipelinePhase::Planning,
            PromptInputKind::LastOutput,
            content_id_sha256.to_string(),
            last_output_bytes,
            inline_budget_bytes,
            "xsd-retry-context".to_string(),
        ),
    ))
    .collect()
}

pub(in crate::reducer::boundary) fn materialize_planning_xsd_retry_last_output(
    handler: &crate::reducer::boundary::MainEffectHandler,
    ctx: &PhaseContext<'_>,
    iteration: u32,
    last_output: &str,
    params: XsdRetryLastOutputParams<'_>,
) -> Result<Option<Vec<PipelineEvent>>> {
    if is_xsd_retry_last_output_already_materialized(handler, ctx, iteration, &params) {
        return Ok(None);
    }
    let last_output_path = Path::new(".agent/tmp/last_output.xml");
    ctx.workspace
        .write_atomic(last_output_path, last_output)
        .map_err(
            |err| crate::reducer::event::ErrorEvent::WorkspaceWriteFailed {
                path: last_output_path.display().to_string(),
                kind: crate::reducer::event::WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            },
        )?;
    let input = MaterializedPromptInput {
        kind: PromptInputKind::LastOutput,
        content_id_sha256: params.content_id_sha256.to_string(),
        consumer_signature_sha256: params.consumer_signature_sha256.to_string(),
        original_bytes: params.last_output_bytes,
        final_bytes: params.last_output_bytes,
        model_budget_bytes: None,
        inline_budget_bytes: Some(params.inline_budget_bytes),
        representation: PromptInputRepresentation::FileReference {
            path: last_output_path.to_path_buf(),
        },
        reason: PromptMaterializationReason::PolicyForcedReference,
    };
    Ok(Some(build_xsd_retry_last_output_events(
        iteration,
        input,
        params.content_id_sha256,
        params.last_output_bytes,
        params.inline_budget_bytes,
    )))
}

// ---------------------------------------------------------------------------
// Rendered log generation
// ---------------------------------------------------------------------------

pub(in crate::reducer::boundary) fn xsd_retry_incomplete_early_return(
    log: &crate::prompts::SubstitutionLog,
    prompt_key: &str,
    was_replayed: bool,
) -> Box<EffectResult> {
    Box::new(
        planning_template_incomplete_early_return(
            log,
            "planning_xsd_retry",
            prompt_key,
            was_replayed,
        )
        .unwrap_or_else(|| EffectResult::event(PipelineEvent::planning_prompt_prepared(0))),
    )
}

pub(in crate::reducer::boundary) fn gen_planning_xsd_retry_rendered_log(
    ctx: &PhaseContext<'_>,
    was_replayed: bool,
    prompt_key: &str,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if was_replayed {
        return Ok(None);
    }
    let rendered = prompt_planning_xsd_retry_with_context_files_and_log(
        ctx.template_context,
        "Previous XML output failed XSD validation. Please provide valid XML conforming to the schema.",
        ctx.workspace,
        "planning_xsd_retry",
        SessionCapabilities::new(
            &CapabilitySet::defaults_for_drain(SessionDrain::Planning),
            &PolicyFlagSet::defaults_for_drain(SessionDrain::Planning),
        ),
    );
    match rendered.log.is_complete() {
        true => Ok(Some(rendered.log)),
        false => Err(xsd_retry_incomplete_early_return(
            &rendered.log,
            prompt_key,
            was_replayed,
        )),
    }
}

pub(in crate::reducer::boundary) fn gen_planning_normal_rendered_log(
    ctx: &PhaseContext<'_>,
    prompt_ref: &PromptContentReference,
    should_render: bool,
    prompt_key: &str,
    was_replayed: bool,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if !should_render {
        return Ok(None);
    }
    let rendered = prompt_planning_xml_with_references_and_log(
        ctx.template_context,
        prompt_ref,
        ctx.workspace,
        "planning_xml",
        SessionCapabilities::new(
            &CapabilitySet::defaults_for_drain(SessionDrain::Planning),
            &PolicyFlagSet::defaults_for_drain(SessionDrain::Planning),
        ),
    );
    match rendered.log.is_complete() {
        true => Ok(Some(rendered.log)),
        false => {
            let missing = rendered.log.unsubstituted.clone();
            Err(Box::new(
                EffectResult::event(PipelineEvent::template_rendered(
                    PipelinePhase::Planning,
                    "planning_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                    crate::agents::AgentRole::Developer,
                    "planning_xml".to_string(),
                    missing,
                    Vec::new(),
                ))
                .with_ui_event(UIEvent::PromptReplayHit {
                    key: prompt_key.to_string(),
                    was_replayed,
                }),
            ))
        }
    }
}

// ---------------------------------------------------------------------------
// Result assembly helpers
// ---------------------------------------------------------------------------

pub(in crate::reducer::boundary) fn assemble_planning_prompt_result(
    iteration: u32,
    capture: PlanningPromptCapture,
    template_name: &str,
    rendered_log: Option<crate::prompts::SubstitutionLog>,
    xsd_retry_events: Option<Vec<PipelineEvent>>,
) -> EffectResult {
    let PlanningPromptCapture {
        prompt,
        prompt_key,
        was_replayed,
        prompt_content_id,
    } = capture;
    let replay_key = prompt_key.as_deref().map(|k| (k.to_string(), was_replayed));
    let prompt_captured_event = prompt_key.as_deref().and_then(|k| {
        (!was_replayed).then(|| {
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: k.to_string(),
                content: prompt.clone(),
                content_id: prompt_content_id.clone(),
            })
        })
    });
    let result = EffectResult::event(PipelineEvent::planning_prompt_prepared(iteration));
    let result = replay_key.map_or(result.clone(), |(key, replayed)| {
        result.with_ui_event(UIEvent::PromptReplayHit {
            key,
            was_replayed: replayed,
        })
    });
    let result =
        prompt_captured_event.map_or(result.clone(), |ev| result.with_additional_event(ev));
    let result = xsd_retry_events
        .into_iter()
        .flatten()
        .fold(result, |r, ev| r.with_additional_event(ev));
    rendered_log.map_or(result.clone(), |log| {
        result.with_additional_event(PipelineEvent::template_rendered(
            PipelinePhase::Planning,
            template_name.to_string(),
            log,
        ))
    })
}

pub(in crate::reducer::boundary) fn maybe_add_planning_invoked_event(
    result: EffectResult,
    iteration: u32,
) -> EffectResult {
    if result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }) {
        result.with_additional_event(PipelineEvent::planning_agent_invoked(iteration))
    } else {
        result
    }
}

pub(in crate::reducer::boundary) fn planning_template_incomplete_early_return(
    log: &crate::prompts::SubstitutionLog,
    template_name: &str,
    prompt_key: &str,
    was_replayed: bool,
) -> Option<EffectResult> {
    if log.is_complete() {
        return None;
    }
    let missing = log.unsubstituted.clone();
    Some(
        EffectResult::event(PipelineEvent::template_rendered(
            PipelinePhase::Planning,
            template_name.to_string(),
            log.clone(),
        ))
        .with_additional_event(PipelineEvent::agent_template_variables_invalid(
            crate::agents::AgentRole::Developer,
            template_name.to_string(),
            missing,
            Vec::new(),
        ))
        .with_ui_event(UIEvent::PromptReplayHit {
            key: prompt_key.to_string(),
            was_replayed,
        }),
    )
}

pub(in crate::reducer::boundary) fn build_planning_prompt_ref(
    ctx: &PhaseContext<'_>,
    input: &MaterializedPromptInput,
) -> Result<PromptContentReference> {
    match &input.representation {
        PromptInputRepresentation::Inline => {
            let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                crate::reducer::event::ErrorEvent::WorkspaceReadFailed {
                    path: "PROMPT.md".to_string(),
                    kind: crate::reducer::event::WorkspaceIoErrorKind::from_io_error_kind(
                        err.kind(),
                    ),
                }
            })?;
            Ok(PromptContentReference::inline(prompt_md))
        }
        PromptInputRepresentation::FileReference { path } => Ok(PromptContentReference::file_path(
            path.clone(),
            "Original user requirements from PROMPT.md",
        )),
    }
}
