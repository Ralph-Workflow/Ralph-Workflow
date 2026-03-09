//! Prompt mode implementations for development prompt preparation.
//!
//! Contains the per-mode logic for `SameAgentRetry` and `Normal` prompt modes,
//! extracted from `preparation.rs` for maintainability.

use super::super::super::MainEffectHandler;
use crate::phases::PhaseContext;
use crate::prompts::content_builder::PromptContentReferences;
use crate::prompts::content_reference::{
    PlanContentReference, PromptContentReference, MAX_INLINE_CONTENT_SIZE,
};
use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode, SubstitutionLog};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::state::PromptInputRepresentation;
use anyhow::Result;
use std::path::Path;

/// Output of a prompt mode computation.
///
/// Either produces the data needed to finalize the prompt, or short-circuits
/// with an early `EffectResult` (e.g., when template variable validation fails).
pub(super) enum PromptModeResult {
    /// Normal completion — caller assembles the final `EffectResult`.
    Data(PromptModeData),
    /// Early return — caller should propagate this `EffectResult` immediately.
    EarlyReturn(EffectResult),
}

/// Data produced by a successful prompt mode computation.
pub(super) struct PromptModeData {
    pub prompt: String,
    pub template_name: &'static str,
    pub prompt_key: Option<String>,
    pub was_replayed: bool,
    pub rendered_log: Option<SubstitutionLog>,
    /// Additional events to attach (used by XSD retry materialization).
    pub additional_events: Vec<PipelineEvent>,
}

impl MainEffectHandler {
    /// Build the development prompt for `SameAgentRetry` mode.
    ///
    /// Prepends retry preamble to the last prepared prompt for this phase,
    /// preserving any XSD retry / continuation context if present.
    pub(super) fn prompt_mode_same_agent_retry(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<PromptModeResult> {
        let continuation_state = &self.state.continuation;
        let retry_preamble =
            super::super::super::retry_guidance::same_agent_retry_preamble(continuation_state);
        let inputs = self
            .state
            .prompt_inputs
            .development
            .as_ref()
            .filter(|p| p.iteration == iteration)
            .ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;

        let prompt_ref = match &inputs.prompt.representation {
            PromptInputRepresentation::Inline => {
                let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                    ErrorEvent::WorkspaceReadFailed {
                        path: "PROMPT.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                })?;
                PromptContentReference::inline(prompt_md)
            }
            PromptInputRepresentation::FileReference { path } => PromptContentReference::file_path(
                path.clone(),
                "Original user requirements from PROMPT.md",
            ),
        };

        let plan_ref = match &inputs.plan.representation {
            PromptInputRepresentation::Inline => {
                let plan_md = ctx
                    .workspace
                    .read(Path::new(".agent/PLAN.md"))
                    .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                        path: ".agent/PLAN.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?;
                PlanContentReference::Inline(plan_md)
            }
            PromptInputRepresentation::FileReference { path } => {
                PlanContentReference::ReadFromFile {
                    primary_path: path.clone(),
                    fallback_path: Some(Path::new(".agent/tmp/plan.xml").to_path_buf()),
                    description: format!(
                        "Plan is {} bytes (exceeds {} limit)",
                        inputs.plan.final_bytes, MAX_INLINE_CONTENT_SIZE
                    ),
                }
            }
        };

        let refs = PromptContentReferences {
            prompt: Some(prompt_ref),
            plan: Some(plan_ref),
            diff: None,
        };

        let scope_key = PromptScopeKey::for_development(
            iteration,
            None,
            RetryMode::SameAgent {
                count: continuation_state.same_agent_retry_count,
            },
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let mut should_validate = false;
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            None,
            || {
                let (base_prompt, local_should_validate) = ctx
                    .workspace
                    .read(Path::new(".agent/tmp/development_prompt.txt"))
                    .map_or_else(
                        |_| {
                            (
                                crate::prompts::prompt_developer_iteration_xml_with_references(
                                    ctx.template_context,
                                    &refs,
                                    ctx.workspace,
                                ),
                                true,
                            )
                        },
                        |previous_prompt| {
                            (
                                super::super::super::retry_guidance::strip_existing_same_agent_retry_preamble(
                                    &previous_prompt,
                                )
                                .to_string(),
                                false,
                            )
                        },
                    );
                should_validate = local_should_validate;
                format!("{retry_preamble}\n{base_prompt}")
            },
        );

        let rendered_log = if should_validate && !was_replayed {
            let rendered = crate::prompts::prompt_developer_iteration_xml_with_references_and_log(
                ctx.template_context,
                &refs,
                ctx.workspace,
                "developer_iteration_xml",
            );
            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::Development,
                    "developer_iteration_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(
                    PipelineEvent::agent_template_variables_invalid(
                        crate::agents::AgentRole::Developer,
                        "developer_iteration_xml".to_string(),
                        missing,
                        Vec::new(),
                    ),
                );
                return Ok(PromptModeResult::EarlyReturn(result));
            }
            Some(rendered.log)
        } else {
            None
        };

        Ok(PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration_xml",
            prompt_key: Some(prompt_key),
            was_replayed,
            rendered_log,
            additional_events: Vec::new(),
        }))
    }

    /// Build the development prompt for `Normal` mode.
    ///
    /// Generates or replays the first-attempt prompt for the given iteration,
    /// using the `developer_iteration_xml` template with inlined or referenced
    /// PROMPT.md and PLAN.md.
    pub(super) fn prompt_mode_normal(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<PromptModeResult> {
        let inputs = self
            .state
            .prompt_inputs
            .development
            .as_ref()
            .filter(|p| p.iteration == iteration)
            .ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;

        let prompt_md = match &inputs.prompt.representation {
            PromptInputRepresentation::Inline => {
                let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                    ErrorEvent::WorkspaceReadFailed {
                        path: "PROMPT.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                })?;
                Some(prompt_md)
            }
            PromptInputRepresentation::FileReference { .. } => None,
        };
        let plan_md = match &inputs.plan.representation {
            PromptInputRepresentation::Inline => {
                let plan_md = ctx
                    .workspace
                    .read(Path::new(".agent/PLAN.md"))
                    .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                        path: ".agent/PLAN.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?;
                Some(plan_md)
            }
            PromptInputRepresentation::FileReference { .. } => None,
        };

        let scope_key = PromptScopeKey::for_development(
            iteration,
            None,
            RetryMode::Normal,
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let prompt_ref = match &inputs.prompt.representation {
            PromptInputRepresentation::Inline => {
                let prompt_md =
                    prompt_md.ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;
                PromptContentReference::inline(prompt_md)
            }
            PromptInputRepresentation::FileReference { path } => PromptContentReference::file_path(
                path.clone(),
                "Original user requirements from PROMPT.md",
            ),
        };
        let plan_ref = match &inputs.plan.representation {
            PromptInputRepresentation::Inline => {
                let plan_md =
                    plan_md.ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;
                PlanContentReference::Inline(plan_md)
            }
            PromptInputRepresentation::FileReference { path } => {
                PlanContentReference::ReadFromFile {
                    primary_path: path.clone(),
                    fallback_path: Some(Path::new(".agent/tmp/plan.xml").to_path_buf()),
                    description: format!(
                        "Plan is {} bytes (exceeds {} limit)",
                        inputs.plan.final_bytes, MAX_INLINE_CONTENT_SIZE
                    ),
                }
            }
        };
        let (prompt, was_replayed) =
            get_stored_or_generate_prompt(&scope_key, &self.state.prompt_history, None, || {
                let prompt_ref = prompt_ref.clone();
                let plan_ref = plan_ref.clone();
                let refs = PromptContentReferences {
                    prompt: Some(prompt_ref),
                    plan: Some(plan_ref),
                    diff: None,
                };
                // Use log-based rendering
                let rendered =
                    crate::prompts::prompt_developer_iteration_xml_with_references_and_log(
                        ctx.template_context,
                        &refs,
                        ctx.workspace,
                        "developer_iteration_xml",
                    );
                rendered.content
            });

        // Validate freshly generated prompts (not replayed ones)
        let rendered_log = if was_replayed {
            None
        } else {
            let refs = PromptContentReferences {
                prompt: Some(prompt_ref),
                plan: Some(plan_ref),
                diff: None,
            };
            let rendered = crate::prompts::prompt_developer_iteration_xml_with_references_and_log(
                ctx.template_context,
                &refs,
                ctx.workspace,
                "developer_iteration_xml",
            );

            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::Development,
                    "developer_iteration_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(
                    PipelineEvent::agent_template_variables_invalid(
                        crate::agents::AgentRole::Developer,
                        "developer_iteration_xml".to_string(),
                        missing,
                        Vec::new(),
                    ),
                );
                return Ok(PromptModeResult::EarlyReturn(result));
            }
            Some(rendered.log)
        };

        Ok(PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration_xml",
            prompt_key: Some(prompt_key),
            was_replayed,
            rendered_log,
            additional_events: Vec::new(),
        }))
    }
}
