//! Unit tests for XSD plan validation.
//!
//! Tests are organized by category to keep files manageable and enable parallel test execution.
//! Each module tests a specific aspect of plan validation.

use super::*;

// Test modules organized by category
mod cdata_escaping;
mod content_parsing;
mod edge_cases;
mod file_validation;
mod list_item_flexibility;
mod llm_output_patterns;
mod real_world_plan;
mod rich_content;
mod risk_verification;
mod scope_validation;
mod step_validation;
mod structure_tests;
mod tolerant_parsing;
