//! Re-export from boundary module.
//!
//! This module exists for backwards compatibility with code that imports from
//! `crate::utils::io`. The actual implementation is in `crate::common::io::secret_like_regex`.

pub use crate::common::io::secret_like_regex;
