//! Review phase execution.
//!
//! This module handles the review and fix phase of the Ralph pipeline. It runs
//! a configurable number of review-fix cycles, where each cycle:
//! 1. Reviews the code and creates ISSUES.md
//! 2. Fixes the issues found
//! 3. Cleans up ISSUES.md (in isolation mode)
//!
//! Most implementation details are split into sub-modules to keep files small
//! and make the phase easier to reason about.

pub(crate) mod boundary_domain;
mod pass;
pub(crate) mod snippet_domain;
mod types;
mod validation;
mod xml_processing;

pub use pass::{run_fix_pass, run_review_pass};
pub use types::{FixPassResult, ReviewPassResult};
pub use validation::{
    post_flight_review_check, pre_flight_review_check, PostflightResult, PreflightResult,
};

#[cfg(test)]
#[path = "review/tests.rs"]
mod tests;
