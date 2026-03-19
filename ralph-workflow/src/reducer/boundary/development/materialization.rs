//! Development input materialization.
//!
//! Handles reading PROMPT.md and PLAN.md from workspace and deciding whether
//! to inline or reference each based on the 16KB inline budget.
//!
//! If a file exceeds the inline budget, a backup file is created and referenced
//! by path instead of embedding the content. This prevents token budget exhaustion
//! while preserving full context access for the agent.

use super::super::MainEffectHandler;
use crate::phases::PhaseContext;
use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    /// Materialize development inputs (PROMPT.md and PLAN.md).
    ///
    /// Reads PROMPT.md and PLAN.md from the workspace, determines whether to inline
    /// or reference each based on the 16KB inline budget, and emits a
    /// `DevelopmentInputsMaterialized` event.
    ///
    /// If either file exceeds the inline budget, a backup file is created and referenced
    /// by path instead of embedding the content. This prevents token budget exhaustion
    /// while preserving full context access for the agent.
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace access
    /// * `iteration` - Current development iteration number
    ///
    /// # Returns
    ///
    /// `EffectResult` with `DevelopmentInputsMaterialized` event, plus optional oversize
    /// detection events if either input exceeds the inline budget.
    pub(in crate::reducer::boundary) fn materialize_development_inputs(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
            ErrorEvent::WorkspaceReadFailed {
                path: "PROMPT.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;

        let plan_md = ctx
            .workspace
            .read(Path::new(".agent/PLAN.md"))
            .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                path: ".agent/PLAN.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();

        let prompt_backup_path = Path::new(".agent/PROMPT.md.backup");
        let (prompt_representation, prompt_reason) = if prompt_md.len() as u64 > inline_budget_bytes
        {
            crate::files::create_prompt_backup_with_workspace(ctx.workspace).map_err(|err| {
                ErrorEvent::WorkspaceWriteFailed {
                    path: prompt_backup_path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
            ctx.logger.warn(&format!(
                "PROMPT size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
                (prompt_md.len() as u64) / 1024,
                inline_budget_bytes / 1024,
                prompt_backup_path.display()
            ));
            (
                PromptInputRepresentation::FileReference {
                    path: prompt_backup_path.to_path_buf(),
                },
                PromptMaterializationReason::InlineBudgetExceeded,
            )
        } else {
            (
                PromptInputRepresentation::Inline,
                PromptMaterializationReason::WithinBudgets,
            )
        };

        let plan_path = Path::new(".agent/PLAN.md");
        let (plan_representation, plan_reason) = if plan_md.len() as u64 > inline_budget_bytes {
            ctx.logger.warn(&format!(
                "PLAN size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
                (plan_md.len() as u64) / 1024,
                inline_budget_bytes / 1024,
                plan_path.display()
            ));
            (
                PromptInputRepresentation::FileReference {
                    path: plan_path.to_path_buf(),
                },
                PromptMaterializationReason::InlineBudgetExceeded,
            )
        } else {
            (
                PromptInputRepresentation::Inline,
                PromptMaterializationReason::WithinBudgets,
            )
        };

        let prompt_input = MaterializedPromptInput {
            kind: PromptInputKind::Prompt,
            content_id_sha256: sha256_hex_str(&prompt_md),
            consumer_signature_sha256: consumer_signature_sha256.clone(),
            original_bytes: prompt_md.len() as u64,
            final_bytes: prompt_md.len() as u64,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation: prompt_representation,
            reason: prompt_reason,
        };
        let plan_input = MaterializedPromptInput {
            kind: PromptInputKind::Plan,
            content_id_sha256: sha256_hex_str(&plan_md),
            consumer_signature_sha256,
            original_bytes: plan_md.len() as u64,
            final_bytes: plan_md.len() as u64,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation: plan_representation,
            reason: plan_reason,
        };

        let result = EffectResult::event(PipelineEvent::development_inputs_materialized(
            iteration,
            prompt_input.clone(),
            plan_input.clone(),
        ));
        let result = if prompt_input.original_bytes > inline_budget_bytes {
            let result = result.with_ui_event(UIEvent::AgentActivity {
                agent: "pipeline".to_string(),
                message: format!(
                    "Oversize PROMPT: {} KB > {} KB; using file reference",
                    prompt_input.original_bytes / 1024,
                    inline_budget_bytes / 1024
                ),
            });
            result.with_additional_event(PipelineEvent::prompt_input_oversize_detected(
                crate::reducer::event::PipelinePhase::Development,
                PromptInputKind::Prompt,
                prompt_input.content_id_sha256.clone(),
                prompt_input.original_bytes,
                inline_budget_bytes,
                "inline-embedding".to_string(),
            ))
        } else {
            result
        };
        if plan_input.original_bytes > inline_budget_bytes {
            let result = result.with_ui_event(UIEvent::AgentActivity {
                agent: "pipeline".to_string(),
                message: format!(
                    "Oversize PLAN: {} KB > {} KB; using file reference",
                    plan_input.original_bytes / 1024,
                    inline_budget_bytes / 1024
                ),
            });
            return Ok(result.with_additional_event(
                PipelineEvent::prompt_input_oversize_detected(
                    crate::reducer::event::PipelinePhase::Development,
                    PromptInputKind::Plan,
                    plan_input.content_id_sha256.clone(),
                    plan_input.original_bytes,
                    inline_budget_bytes,
                    "inline-embedding".to_string(),
                ),
            ));
        }

        Ok(result)
    }
}
