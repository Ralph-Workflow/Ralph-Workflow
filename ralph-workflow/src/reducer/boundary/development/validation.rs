//! Development XML validation and extraction.
//!
//! This module handles:
//! - Extracting `development_result.xml` from workspace
//! - Validating XML against XSD schema
//! - Parsing status, summary, `files_changed`, and `next_steps`
//! - Writing XSD error context for retry

use super::super::MainEffectHandler;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::PipelineEvent;
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use std::path::Path;

const DEVELOPMENT_XSD_ERROR_PATH: &str = ".agent/tmp/development_xsd_error.txt";

impl MainEffectHandler {
    /// Extract development XML output from workspace.
    ///
    /// Checks for the presence of `.agent/tmp/development_result.xml` in the workspace.
    /// If found, emits `DevelopmentXmlExtracted` with the content in a `UIEvent`.
    /// If missing, emits `DevelopmentXmlMissing` (triggers invalid output handling).
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace access
    /// * `iteration` - Current development iteration number
    ///
    /// # Returns
    ///
    /// `EffectResult` with `DevelopmentXmlExtracted` or `DevelopmentXmlMissing` event,
    /// plus `IterationProgress` UI event.
    pub(in crate::reducer::handler) fn extract_development_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        let xml_path = Path::new(xml_paths::DEVELOPMENT_RESULT_XML);
        let initial_event = UIEvent::IterationProgress {
            current: iteration,
            total: self.state.total_iterations,
        };

        match ctx.workspace.read(xml_path) {
            Ok(content) => EffectResult::with_ui(
                PipelineEvent::development_xml_extracted(iteration),
                vec![
                    initial_event,
                    UIEvent::XmlOutput {
                        xml_type: XmlOutputType::DevelopmentResult,
                        content,
                        context: Some(XmlOutputContext {
                            iteration: Some(iteration),
                            pass: None,
                            snippets: Vec::new(),
                        }),
                    },
                ],
            ),
            Err(_) => EffectResult::with_ui(
                PipelineEvent::development_xml_missing(
                    iteration,
                    self.state.continuation.invalid_output_attempts,
                ),
                vec![initial_event],
            ),
        }
    }

    /// Validate development XML against XSD schema.
    ///
    /// Reads `.agent/tmp/development_result.xml` and validates it against the
    /// development result XSD schema. On success, parses the status, summary,
    /// `files_changed`, and `next_steps` elements. On failure, writes the XSD error
    /// to `.agent/tmp/development_xsd_error.txt` for inclusion in retry prompt.
    ///
    /// # Status Mapping
    ///
    /// - `<status>completed</status>` → `DevelopmentStatus::Completed`
    /// - `<status>partial</status>` → `DevelopmentStatus::Partial` (triggers continuation)
    /// - `<status>failed</status>` or invalid XML → `DevelopmentStatus::Failed` (triggers retry)
    ///
    /// # XSD Retry Context
    ///
    /// When validation fails, the XSD error is formatted for AI retry and written to
    /// `.agent/tmp/development_xsd_error.txt`. This file is referenced in the XSD retry prompt
    /// to provide context about what went wrong.
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace access
    /// * `iteration` - Current development iteration number
    ///
    /// # Returns
    ///
    /// `EffectResult` with `DevelopmentXmlValidated` (on success) or
    /// `DevelopmentOutputValidationFailed` (on XSD error or missing file).
    pub(in crate::reducer::handler) fn validate_development_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::{
            validate_continuation_development_result_xml, validate_development_result_xml,
        };

        let Ok(xml) = ctx
            .workspace
            .read(Path::new(xml_paths::DEVELOPMENT_RESULT_XML))
        else {
            return EffectResult::event(PipelineEvent::development_output_validation_failed(
                iteration,
                self.state.continuation.invalid_output_attempts,
            ));
        };

        let validation_result = if self.state.continuation.is_continuation() {
            validate_continuation_development_result_xml(&xml)
        } else {
            validate_development_result_xml(&xml)
        };

        match validation_result {
            Ok(elements) => {
                let _ = ctx
                    .workspace
                    .remove_if_exists(Path::new(DEVELOPMENT_XSD_ERROR_PATH));
                let status = if elements.is_completed() {
                    crate::reducer::state::DevelopmentStatus::Completed
                } else if elements.is_partial() {
                    crate::reducer::state::DevelopmentStatus::Partial
                } else {
                    crate::reducer::state::DevelopmentStatus::Failed
                };

                let files_changed = elements
                    .files_changed
                    .as_ref()
                    .map(|f| f.lines().map(std::string::ToString::to_string).collect());

                EffectResult::event(PipelineEvent::development_xml_validated(
                    iteration,
                    status,
                    elements.summary.clone(),
                    files_changed,
                    elements.next_steps,
                ))
            }
            Err(err) => {
                let _ = ctx.workspace.write(
                    Path::new(DEVELOPMENT_XSD_ERROR_PATH),
                    &err.format_for_ai_retry(),
                );
                EffectResult::event(PipelineEvent::development_output_validation_failed(
                    iteration,
                    self.state.continuation.invalid_output_attempts,
                ))
            }
        }
    }
}
