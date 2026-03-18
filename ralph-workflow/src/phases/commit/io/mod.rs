// Boundary module for commit phase I/O operations.
// This module contains string processing and file I/O that requires mutation.

pub mod diff_truncation;

pub use diff_truncation::{
    effective_model_budget_bytes, model_budget_bytes_for_agent_name, truncate_diff_to_model_budget,
};
