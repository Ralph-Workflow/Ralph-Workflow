// Split from the legacy monolithic reducer/handler.rs test module.
// Individual test modules will be added here as the handler implementation is
// decomposed into single-task effects.

mod common;

mod analysis_handler;
mod capability_gate_enforcement;
mod cloud;
mod cloud_push_policy;
mod commit_handler;
mod completion_marker;
mod context_cleanup;
mod development_outcome;
mod development_prompt;
mod extract_missing;
mod fix_outcome;
mod git_auth;
mod gitignore_handler;
mod invoke_prompt;
mod json_artifact_fail_closed;
mod json_xml_parity;
mod phase_contract_chain;
mod planning_markdown;
mod planning_prompt;
mod prompt_permissions;
mod review_prompt;
mod review_validation;

mod stale_xml_cleanup;
mod timeout_context;
