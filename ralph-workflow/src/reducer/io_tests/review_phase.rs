//! Tests for review phase events (review passes, fix attempts).
//!
//! These tests validate the critical `review_issues_found` flag behavior that was
//! one of the 7 bugs we fixed in the reducer.

use super::*;
use crate::reducer::state::ContinuationState;

// Tests for review validation logic
include!("review_phase/validation_tests.rs");

// Tests for phase transition scenarios
include!("review_phase/transition_tests.rs");

// Tests for state management during review
include!("review_phase/state_tests.rs");
