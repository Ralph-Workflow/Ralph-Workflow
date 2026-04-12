//! Prompt Templates Module
//!
//! Generates context-controlled prompts for AI agents. Key design principle:
//! reviewers get minimal context for "fresh eyes" perspective.
//!
//! # Key Types
//!
//! - [`ContextLevel`] - Amount of context to include (Minimal, Normal)
//! - [`TemplateContext`] - User template overrides and customization
//! - [`Template`] - Rendered template with variable substitution
//!
//! # Template System
//!
//! Templates are stored as `.txt` files in `prompts/templates/` and support:
//! - Variables: `{{VARIABLE_NAME}}`
//! - Partials: `{{> shared/_partial_name}}`
//! - Comments: `{# comment #}`
//!
//! # Module Structure
//!
//! - `types` - Type definitions (`ContextLevel`, Role, Action)
//! - `developer` - Developer prompts (iteration, planning)
//! - [`reviewer`] - Reviewer prompts (review, comprehensive, security, incremental)
//! - `commit` - Fix and commit message prompts
//! - `rebase` - Conflict resolution prompts for auto-rebase
//! - [`partials`] - Shared template partials for composition
//! - `prompt_config` - Configuration types for prompt generation
//! - `resume_note` - Resume context note generation
//! - `prompt_dispatch` - Dispatch functions for prompt routing

pub mod analysis;
mod commit;
pub mod content_builder;
pub mod content_reference;
mod developer;
pub mod io;
pub mod partials;
pub mod prompt_history_entry;
pub mod prompt_scope_key;
mod rebase;
pub mod review;
pub mod reviewer;
pub mod runtime;
pub mod template_catalog;
pub mod template_context;
pub(crate) mod template_engine;
mod template_macros;
pub mod template_parsing;
pub mod template_registry;
mod template_validator;
pub mod template_variables;
mod types;

// Sub-modules for split functionality
mod prompt_config;
#[path = "prompt_dispatch.rs"]
mod prompt_dispatch;

// Re-export ResumeContext for use in prompts
pub use crate::checkpoint::restore::ResumeContext;

// Re-export SessionCapabilities for use in prompts
pub use template_variables::capability_template_variables_from_session;
pub use template_variables::default_caps_and_flags_for_drain;
pub use template_variables::SessionCapabilities;

// Backwards compatibility alias: registry used to be in prompts, now in agents
pub mod registry {
    pub use crate::agents::AgentRegistry;
}
pub use registry::AgentRegistry;

// Re-export items from split modules
pub use prompt_config::PromptConfig;
pub use prompt_dispatch::get_stored_or_generate_prompt;
pub use prompt_dispatch::prompt_for_agent;
pub use prompt_history_entry::PromptHistoryEntry;
pub use prompt_scope_key::{PromptPhase, PromptScopeKey, RetryMode};

#[must_use]
pub fn generate_resume_note(context: &ResumeContext) -> String {
    runtime::generate_resume_note(context)
}

// Re-export public items for API convenience
pub use commit::prompt_commit_xsd_retry_with_context;
pub use commit::prompt_commit_xsd_retry_with_log;
pub use commit::prompt_fix_with_context;
pub use commit::prompt_generate_commit_message_with_diff_with_context;
pub use commit::prompt_generate_commit_message_with_diff_with_log;

pub use developer::{
    prompt_developer_iteration_continuation_xml,
    prompt_developer_iteration_continuation_xml_with_log,
    prompt_developer_iteration_xml_with_context, prompt_developer_iteration_xml_with_references,
    prompt_developer_iteration_xml_with_references_and_log,
    prompt_developer_iteration_xsd_retry_with_context,
    prompt_developer_iteration_xsd_retry_with_context_files,
    prompt_developer_iteration_xsd_retry_with_context_files_and_log,
    prompt_parallel_dev_worker_with_context, prompt_parallel_planning_with_context,
    prompt_parallel_verifier_with_context, prompt_planning_xml_with_context,
    prompt_planning_xml_with_references, prompt_planning_xml_with_references_and_log,
    prompt_planning_xsd_retry_with_context, prompt_planning_xsd_retry_with_context_files,
    prompt_planning_xsd_retry_with_context_files_and_log,
};
pub use developer::{prompt_developer_iteration_with_context, prompt_plan_with_context};
pub use rebase::{
    build_conflict_resolution_prompt_with_context, collect_conflict_info_with_workspace,
    FileConflict,
};
pub use review::{
    prompt_fix_xml_with_context, prompt_fix_xml_with_log, prompt_fix_xsd_retry_with_context,
    prompt_fix_xsd_retry_with_context_files, prompt_fix_xsd_retry_with_log,
    prompt_review_xml_with_context, prompt_review_xml_with_references,
    prompt_review_xml_with_references_and_log, prompt_review_xsd_retry_with_context,
    prompt_review_xsd_retry_with_context_files, prompt_review_xsd_retry_with_context_files_and_log,
};

pub use rebase::build_enhanced_conflict_resolution_prompt;

// Types only used in tests
pub use rebase::{collect_branch_info, BranchInfo};

// Re-export non-context variants for test compatibility
#[cfg(test)]
pub use commit::{prompt_fix, prompt_generate_commit_message_with_diff};
#[cfg(test)]
pub use developer::{prompt_developer_iteration, prompt_plan};
pub use template_context::TemplateContext;
pub use template_engine::Template;
pub use template_validator::{
    extract_metadata, extract_partials, extract_variables, validate_template, RenderedPromptError,
    RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    TemplateVariablesInvalidError, ValidationError, ValidationWarning,
};
pub use types::ContextLevel;
pub use types::{Action, Role};

// Content reference types for oversized prompt handling
pub use content_builder::{PromptContentBuilder, PromptContentReferences};
pub use content_reference::{
    DiffContentReference, PlanContentReference, PromptContentReference, MAX_INLINE_CONTENT_SIZE,
};

#[cfg(test)]
mod io_tests;

#[cfg(test)]
mod snapshot_tests;

#[cfg(test)]
mod schema_contract_tests;
