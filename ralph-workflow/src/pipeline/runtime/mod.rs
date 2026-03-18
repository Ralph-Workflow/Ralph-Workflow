//! Runtime boundary for pipeline.
//!
//! This module contains imperative code that must be kept separate from
//! pure domain logic. Clock access, process execution, and other
//! runtime operations belong here.

pub mod timer;

pub use timer::Timer;
