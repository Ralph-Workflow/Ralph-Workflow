//! Workflow integration tests
//!
//! This module contains tests for the complete workflow including:
//! - Prompt backup and restore (backup.rs)
//! - Cleanup and error recovery (cleanup.rs)
//! - Commit behavior tests (`commit_tests.rs`)
//! - Config and initialization (config.rs)
//! - Agent fallback chain tests (fallback.rs)
//! - Baseline management tests (baseline.rs)
//! - PLAN workflow tests (plan.rs)
//! - Review workflow tests (review.rs)
//! - Resume/checkpoint tests (resume/)
//! - Development XML tests (`development_xml.rs`)
//! - Continuation handling tests (continuation.rs)
//! - Independent result analysis tests (analysis.rs)
//! - Iteration counter invariant tests (`iteration_counter.rs`)
//! - Premature exit prevention tests (`no_premature_exit.rs`)
//! - Continuation budget enforcement tests (`continuation_budget.rs`)
//! - Summary consistency tests (`summary_consistency.rs`)
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (file changes, CLI output, git state)
//! - Uses `MockAppEffectHandler` for git/filesystem isolation
//! - Tests are deterministic and black-box (test the workflow as a user would run it)

pub mod analysis;
pub mod backup;
pub mod baseline;
pub mod cleanup;
pub mod commit_residuals;
pub mod commit_tests;
pub mod config;
pub mod config_test;
pub mod continuation;
pub mod continuation_budget;
pub mod development_xml;
pub mod fallback;
pub mod iteration_counter;
pub mod no_premature_exit;
pub mod oversize_prompt;
pub mod plan;
pub mod resume;
pub mod review;
pub mod summary_consistency;
