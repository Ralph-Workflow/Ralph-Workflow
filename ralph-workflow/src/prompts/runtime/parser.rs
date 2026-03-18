//! Template parsing runtime - byte-by-byte parsing that belongs in boundary code.
//!
//! Re-exports the pure helpers from the io boundary module.

pub use crate::prompts::io::extract_metadata;
pub use crate::prompts::io::extract_partials;
pub use crate::prompts::io::extract_variables;
