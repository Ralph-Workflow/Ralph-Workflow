//! Analysis agent prompt generation.
//!
//! The analysis agent produces an objective assessment of development progress
//! by comparing the git diff against the original PLAN.md.

mod fix_analysis_prompt;
mod system_prompt;

pub use fix_analysis_prompt::generate_fix_analysis_prompt;
pub use system_prompt::generate_analysis_prompt;
