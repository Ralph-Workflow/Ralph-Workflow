//! Commit message generation phase.
//!
//! This module generates commit messages using a single agent attempt per
//! reducer effect. All validation and retry decisions are handled by the
//! reducer via events; this code does not implement fallback chains or
//! in-session XSD retries.

use super::commit_logging::{AttemptOutcome, CommitLogSession, ExtractionAttempt};
use super::context::PhaseContext;
use crate::agents::AgentRegistry;
use crate::files::llm_output_extraction::{
    archive_xml_file_with_workspace, try_extract_from_file_with_workspace,
    try_extract_xml_commit_with_trace, xml_paths, CommitExtractionResult,
};
use crate::pipeline::{run_with_prompt, PipelineRuntime, PromptCommand};
use crate::prompts::TemplateContext;
use crate::workspace::Workspace;
use anyhow::Context as _;
use std::path::Path;

include!("commit/diff_truncation.rs");
include!("commit/prompt.rs");
include!("commit/extraction.rs");
include!("commit/runner.rs");

#[cfg(test)]
include!("commit/tests.rs");
