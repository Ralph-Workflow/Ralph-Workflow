// Boundary module for commit phase I/O operations.
// This module contains string processing and file I/O that requires mutation.

pub mod diff_truncation;

pub use diff_truncation::{
    effective_model_budget_bytes, model_budget_bytes_for_agent_name, truncate_diff_if_large,
    truncate_diff_to_model_budget, truncate_lines_to_fit, CLAUDE_MAX_PROMPT_SIZE,
    GLM_MAX_PROMPT_SIZE, MAX_SAFE_PROMPT_SIZE,
};
