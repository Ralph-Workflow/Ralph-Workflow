//! Commit XML validation and outcome application.
//!
//! This module handles:
//! - XML structure validation using XSD schema
//! - Extracting commit message from valid XML
//! - Persisting XSD errors for retry prompts
//! - Applying validated outcomes to reducer state
//!
//! ## Validation Process
//!
//! 1. Read `.agent/tmp/commit_message.xml`
//! 2. Validate XML against commit message XSD schema
//! 3. If invalid, write error to `.agent/tmp/commit_xsd_error.txt`
//! 4. If valid, remove error file and extract commit message
//! 5. Emit UI event with XML content for observability
//!
//! ## XSD Error Persistence
//!
//! XSD validation errors are persisted to `.agent/tmp/commit_xsd_error.txt` so that:
//! - XSD retry prompts can include specific error context
//! - Same error is not repeated if validation is re-run
//! - Error file is cleaned up once validation succeeds

use super::super::MainEffectHandler;
use super::{current_commit_attempt, COMMIT_XSD_ERROR_PATH};
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::files::llm_output_extraction::try_extract_xml_commit_document_with_trace;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::ui_event::{UIEvent, XmlOutputType};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    /// Validate commit message XML.
    ///
    /// Reads `.agent/tmp/commit_message.xml` and validates it against the
    /// commit message XSD schema. Extracts commit message on success.
    ///
    /// # Events Emitted
    ///
    /// - `commit_xml_validated` - XML valid, commit message extracted
    /// - `commit_xml_validation_failed` - XML invalid or missing
    /// - `UIEvent::XmlOutput` - XML content for observability (always emitted)
    ///
    /// # XSD Error Persistence
    ///
    /// - On failure: Writes error to `.agent/tmp/commit_xsd_error.txt`
    /// - On success: Removes `.agent/tmp/commit_xsd_error.txt` if present
    pub(in crate::reducer::boundary) fn validate_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        let attempt = current_commit_attempt(&self.state.commit);
        let commit_xml = Path::new(xml_paths::COMMIT_MESSAGE_XML);

        let Ok(xml_content) = ctx.workspace.read(commit_xml) else {
            let reason =
                "XML output missing or invalid; agent must write .agent/tmp/commit_message.xml";
            let event = PipelineEvent::commit_xml_validation_failed(reason.to_string(), attempt);
            return EffectResult::with_ui(
                event,
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::CommitMessage,
                    content: reason.to_string(),
                    context: None,
                }],
            );
        };

        let (message, skip_reason, files, excluded_files, detail) =
            try_extract_xml_commit_document_with_trace(&xml_content);

        // Check for skip first
        if let Some(reason) = skip_reason {
            // AI determined no commit needed - emit Skipped event
            ctx.logger.info(&format!("Commit skipped by AI: {reason}"));
            let _ = ctx
                .workspace
                .remove_if_exists(Path::new(COMMIT_XSD_ERROR_PATH));
            return EffectResult::with_ui(
                PipelineEvent::commit_skipped(reason),
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::CommitMessage,
                    content: xml_content,
                    context: None,
                }],
            );
        }

        if message.is_none() {
            // Persist XSD error context for the XSD retry prompt.
            let _ = ctx
                .workspace
                .write(Path::new(COMMIT_XSD_ERROR_PATH), &detail);
        } else {
            let _ = ctx
                .workspace
                .remove_if_exists(Path::new(COMMIT_XSD_ERROR_PATH));
        }
        let event = message.map_or_else(
            || PipelineEvent::commit_xml_validation_failed(detail, attempt),
            |msg| PipelineEvent::commit_xml_validated(msg, files, excluded_files, attempt),
        );

        EffectResult::with_ui(
            event,
            vec![UIEvent::XmlOutput {
                xml_type: XmlOutputType::CommitMessage,
                content: xml_content,
                context: None,
            }],
        )
    }

    /// Apply commit message outcome from validation.
    ///
    /// Reads the validated commit outcome from `self.state.commit_validated_outcome`
    /// and emits the appropriate event.
    ///
    /// # Events Emitted
    ///
    /// - `commit_message_generated` - Valid commit message ready
    /// - `commit_message_validation_failed` - Validation failed with reason
    /// - `commit_generation_failed` - Outcome missing both message and reason
    ///
    /// # Errors
    ///
    /// - `ValidatedCommitOutcomeMissing` - No validated outcome in state
    pub(in crate::reducer::boundary) fn apply_commit_message_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        let attempt = current_commit_attempt(&self.state.commit);
        let outcome = self
            .state
            .commit_validated_outcome
            .as_ref()
            .ok_or(ErrorEvent::ValidatedCommitOutcomeMissing { attempt })?;

        let event = match (&outcome.message, &outcome.reason) {
            (Some(message), _) => {
                PipelineEvent::commit_message_generated(message.clone(), outcome.attempt)
            }
            (None, Some(reason)) => {
                PipelineEvent::commit_message_validation_failed(reason.clone(), outcome.attempt)
            }
            _ => PipelineEvent::commit_generation_failed(
                "Commit validation outcome missing message and reason".to_string(),
            ),
        };

        Ok(EffectResult::event(event))
    }
}
