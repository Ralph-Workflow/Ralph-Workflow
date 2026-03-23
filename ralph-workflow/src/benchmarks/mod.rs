//! Performance benchmarks for memory profiling
//!
//! This module contains benchmark tests that measure memory usage and performance
//! characteristics of the pipeline. These are NOT pass/fail tests - they establish
//! baseline metrics for future comparison and regression detection.
//!
//! # Running Benchmarks
//!
//! ```bash
//! cargo test -p ralph-workflow benchmarks -- --nocapture
//! ```
//!
//! # Benchmark Categories
//!
//! - `memory_usage` - Memory growth during pipeline execution
//! - `checkpoint_serialization` - Checkpoint serialization performance
//! - `regression_tests` - Performance regression detection tests

#[cfg(test)]
mod io_memory_usage;

#[cfg(test)]
mod io_checkpoint_serialization;

#[cfg(test)]
mod io_regression_tests;

// Baselines module is public for use in integration tests
pub mod io_baselines;
