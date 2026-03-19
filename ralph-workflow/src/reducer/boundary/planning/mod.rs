//! Planning phase handler.
//!
//! Handles all effects for the Planning phase:
//! - Input materialization (PROMPT.md size handling)
//! - Prompt preparation (normal, XSD retry, same-agent retry modes)
//! - Agent invocation and XML cleanup
//! - XML extraction and validation
//! - Output processing (PLAN.md writing, archiving)
//!
//! ## Architecture
//!
//! The planning handler follows the effect-handler pattern:
//! - Each handler method executes ONE effect attempt
//! - Handlers emit fact-shaped events describing outcomes
//! - No hidden retry loops - retries are orchestrated by the reducer
//! - Uses `ctx.workspace` for all filesystem operations
//!
//! ## Module Organization
//!
//! - `input_materialization` - PROMPT.md inline vs file reference handling
//! - `prompt_preparation` - Prompt building for different modes
//! - `agent_execution` - Agent invocation and XML cleanup
//! - `xml_validation` - XML extraction and schema validation
//! - `output_processing` - PLAN.md writing and XML archiving

mod agent_execution;
mod input_materialization;
mod output_processing;
mod prompt_preparation;
mod xml_validation;
