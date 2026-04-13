//! Commit message generation phase.
//!
//! This module generates commit messages using a single agent attempt per
//! reducer effect. All validation and retry decisions are handled by the
//! reducer via events; this code does not implement fallback chains or
//! in-session XSD retries.

use super::commit_logging::CommitLogSession;
use crate::agents::AgentRegistry;
use crate::files::artifact_paths;
use crate::pipeline::{run_with_prompt, PipelineRuntime, PromptCommand};
use crate::prompts::TemplateContext;
use crate::workspace::Workspace;
use std::path::Path;

pub mod diff_truncation;

pub use diff_truncation::{
    effective_model_budget_bytes, model_budget_bytes_for_agent_name, truncate_diff_to_model_budget,
};

include!("commit/prompt.rs");
include!("commit/extraction.rs");
include!("commit/runner.rs");

#[cfg(test)]
include!("commit/tests.rs");
