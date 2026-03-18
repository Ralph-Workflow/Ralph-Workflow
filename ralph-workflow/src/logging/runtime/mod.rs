//! Runtime module for logging - contains OS-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that
//! accesses system clocks and other runtime resources.

pub mod timestamp;

pub use timestamp::get_current_timestamp;
