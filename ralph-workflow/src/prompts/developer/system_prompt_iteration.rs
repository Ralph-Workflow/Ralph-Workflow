// System prompt template and generation (developer iteration).
//
// Contains functions for generating developer iteration prompts and XSD retry prompts.
// Split into sub-files by concern:
//   - system_prompt_iteration_xml.rs: core XML iteration prompt functions
//   - system_prompt_iteration_xsd_retry.rs: XSD validation retry prompt functions
//   - system_prompt_iteration_continuation.rs: continuation prompt functions

include!("system_prompt_iteration_xml.rs");
include!("system_prompt_iteration_xsd_retry.rs");
include!("system_prompt_iteration_continuation.rs");
