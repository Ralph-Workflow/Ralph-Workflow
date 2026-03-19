// Split from the legacy monolithic reducer/handler.rs test module.
// Individual test modules will be added here as the handler implementation is
// decomposed into single-task effects.

mod common;

mod analysis_handler;
mod cloud;
mod commit_handler;
mod completion_marker;
mod context_cleanup;
mod development_outcome;
mod development_prompt;
mod extract_missing;
mod fix_outcome;
mod gitignore_handler;
mod invoke_prompt;
mod planning_markdown;
mod planning_prompt;
mod prompt_permissions;
mod review_prompt;
mod review_validation;
mod stale_xml_cleanup;
