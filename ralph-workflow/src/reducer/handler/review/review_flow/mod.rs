// Review phase workflow implementation.
//
// This module implements the complete review phase flow, organized into four logical concerns:
//
// - **Input Materialization** (`input_materialization.rs`) - Preparing PLAN and DIFF inputs
// - **Prompt Generation** (`prompt_generation.rs`) - Building reviewer prompts for different modes
// - **Validation** (`validation.rs`) - Parsing and validating XML output
// - **Output Rendering** (`output_rendering.rs`) - Converting XML to markdown and extracting snippets
//
// ## Review Phase Flow
//
// 1. **Context Preparation**: Compute git diff and backup PROMPT.md
// 2. **Input Materialization**: Read PLAN.md and DIFF.backup (or use sentinels/fallbacks)
// 3. **Prompt Preparation**: Build prompt based on mode (Normal/XsdRetry/SameAgentRetry)
// 4. **Agent Invocation**: Invoke reviewer agent with prepared prompt
// 5. **Cleanup & Extract**: Remove stale XML and check for new output
// 6. **Validation**: Parse and validate XML against schema
// 7. **Output Rendering**: Convert to markdown, extract snippets, archive XML
// 8. **Outcome Application**: Determine clean vs issues-found outcome
//
// ## Isolation Mode
//
// When `developer_iters=0` and `reviewer_reviews>0`, planning does not occur. The review
// phase uses sentinel PLAN content and proceeds without development artifacts.
//
// ## XSD Retry
//
// When XML validation fails, the pipeline enters XSD retry mode. The last invalid output
// is materialized as a file reference, and a retry prompt is generated with the XSD error.
//
// ## Handler Architecture Compliance
//
// All functions in this module are **handlers** (impure):
// - Use `ctx.workspace` for all file operations (never `std::fs`)
// - Emit events describing what happened (facts, not decisions)
// - Execute exactly ONE effect attempt per invocation
// - Do NOT contain retry loops (orchestrator controls retries)

// Re-export imports from parent module so included files have access
use std::fmt::Write;

use super::{
    sha256_hex_str, xml_paths, AgentEvent, DiffContentReference, EffectResult, ErrorEvent, HashSet,
    MainEffectHandler, MaterializedPromptInput, OnceLock, Path, PhaseContext, PipelineEvent,
    PlanContentReference, PromptContentReferences, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason, PromptMode, Regex, Result, UIEvent, WorkspaceIoErrorKind,
    XmlCodeSnippet, XmlOutputContext, XmlOutputType, MAX_INLINE_CONTENT_SIZE,
};

include!("input_materialization.rs");
include!("xsd_retry_materialization.rs");
include!("prompt_generation.rs");
include!("agent_invocation.rs");
include!("validation.rs");
include!("output_rendering.rs");
