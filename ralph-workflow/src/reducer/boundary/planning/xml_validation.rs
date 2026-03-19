//! Planning XML extraction and validation.
//!
//! Handles extraction of planning XML from the canonical workspace path and
//! validation against the plan XSD schema.

use super::super::MainEffectHandler;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::files::llm_output_extraction::validate_plan_xml;
use crate::phases::development::format_plan_as_markdown;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::PipelineEvent;
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    pub(in crate::reducer::handler) fn extract_planning_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        let plan_xml = Path::new(xml_paths::PLAN_XML);
        let content = ctx.workspace.read(plan_xml);

        match content {
            Ok(_) => EffectResult::event(PipelineEvent::planning_xml_extracted(iteration)),
            Err(_) => EffectResult::event(PipelineEvent::planning_xml_missing(
                iteration,
                self.state.continuation.invalid_output_attempts,
            )),
        }
    }

    pub(in crate::reducer::handler) fn validate_planning_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        let Ok(plan_xml) = ctx.workspace.read(Path::new(xml_paths::PLAN_XML)) else {
            return Ok(EffectResult::event(
                PipelineEvent::planning_output_validation_failed(
                    iteration,
                    self.state.continuation.invalid_output_attempts,
                ),
            ));
        };
        validate_plan_xml(&plan_xml).map_or_else(
            |_| {
                Ok(EffectResult::event(
                    PipelineEvent::planning_output_validation_failed(
                        iteration,
                        self.state.continuation.invalid_output_attempts,
                    ),
                ))
            },
            |elements| {
                let markdown = format_plan_as_markdown(&elements);
                Ok(EffectResult::with_ui(
                    PipelineEvent::planning_xml_validated(iteration, true, Some(markdown)),
                    vec![UIEvent::XmlOutput {
                        xml_type: XmlOutputType::DevelopmentPlan,
                        content: plan_xml,
                        context: Some(XmlOutputContext {
                            iteration: Some(iteration),
                            pass: None,
                            snippets: Vec::new(),
                        }),
                    }],
                ))
            },
        )
    }
}
