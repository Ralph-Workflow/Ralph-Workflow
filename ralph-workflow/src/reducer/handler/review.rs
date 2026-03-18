use super::MainEffectHandler;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::prompts::content_builder::PromptContentReferences;
use crate::prompts::content_reference::{
    DiffContentReference, PlanContentReference, MAX_INLINE_CONTENT_SIZE,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::PromptMode;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::{UIEvent, XmlCodeSnippet, XmlOutputContext, XmlOutputType};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    const DIFF_BASELINE_PATH: &str = ".agent/DIFF.base";
}

#[path = "review/review_flow/mod.rs"]
mod review_flow;
include!("review/fix_flow.rs");
