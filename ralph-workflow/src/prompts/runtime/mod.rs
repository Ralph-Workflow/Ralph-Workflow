//! Runtime boundary module for prompts.
//!
//! This module contains imperative code (template parsing, rendering) that cannot
//! be easily converted to functional style. It satisfies the dylint boundary-module
//! check.

pub mod template_engine;
