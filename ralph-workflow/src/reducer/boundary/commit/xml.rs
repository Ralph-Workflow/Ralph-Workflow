//! Commit XML lifecycle management.
//!
//! This module handles XML file operations for commit message generation:
//! - Extraction - Check if agent wrote XML output
//! - Archiving - Move XML to archive after processing
//!
//! ## File Paths
//!
//! - `.agent/tmp/commit_message.xml` - Primary XML output from agent
//! - `.agent/archive/commit_message_*.xml` - Archived outputs with timestamps

use super::super::MainEffectHandler;
use super::current_commit_attempt;
use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::PipelineEvent;
use std::path::Path;

impl MainEffectHandler {
    /// Extract commit XML output.
    ///
    /// Checks if `.agent/tmp/commit_message.xml` exists after agent invocation.
    ///
    /// # Events Emitted
    ///
    /// - `commit_xml_extracted` - XML file found
    /// - `commit_xml_missing` - XML file not found (agent didn't write output)
    pub(in crate::reducer::handler) fn extract_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        let attempt = current_commit_attempt(&self.state.commit);
        let commit_xml = Path::new(xml_paths::COMMIT_MESSAGE_XML);

        match ctx.workspace.read(commit_xml) {
            Ok(_) => EffectResult::event(PipelineEvent::commit_xml_extracted(attempt)),
            Err(_) => EffectResult::event(PipelineEvent::commit_xml_missing(attempt)),
        }
    }

    /// Archive commit XML after processing.
    ///
    /// Moves `.agent/tmp/commit_message.xml` to `.agent/archive/` with timestamp.
    ///
    /// # Events Emitted
    ///
    /// - `commit_xml_archived` - XML file archived successfully
    pub(in crate::reducer::handler) fn archive_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        let attempt = current_commit_attempt(&self.state.commit);
        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));
        EffectResult::event(PipelineEvent::commit_xml_archived(attempt))
    }
}
