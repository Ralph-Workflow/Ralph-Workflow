//! Planning output processing.
//!
//! Handles writing the validated plan to PLAN.md, archiving the XML file,
//! and applying the final planning outcome.

use super::super::MainEffectHandler;
use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, PipelinePhase, WorkspaceIoErrorKind};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    pub(in crate::reducer::handler) fn write_planning_markdown(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        let markdown = self
            .state
            .planning_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.iteration == iteration)
            .and_then(|outcome| outcome.markdown.clone())
            .ok_or(ErrorEvent::ValidatedPlanningMarkdownMissing { iteration })?;

        ctx.workspace
            .write(Path::new(".agent/PLAN.md"), &markdown)
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: ".agent/PLAN.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        Ok(EffectResult::event(
            PipelineEvent::planning_markdown_written(iteration),
        ))
    }

    pub(in crate::reducer::handler) fn archive_planning_xml(
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::PLAN_XML));
        EffectResult::event(PipelineEvent::planning_xml_archived(iteration))
    }

    pub(in crate::reducer::handler) fn apply_planning_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
        iteration: u32,
        valid: bool,
    ) -> EffectResult {
        let ui_events: Vec<_> = valid
            .then(|| self.phase_transition_ui(PipelinePhase::Development))
            .into_iter()
            .collect();
        EffectResult::with_ui(
            PipelineEvent::plan_generation_completed(iteration, valid),
            ui_events,
        )
    }
}
