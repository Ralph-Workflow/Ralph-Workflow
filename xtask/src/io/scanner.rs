//! Re-export scanner from root module.
//!
//! This module exists for boundary module compatibility - code in io/ can import
//! from io::scanner while the actual implementation lives at the crate root.

pub use crate::scanner::*;
