//! XSD retry state transition tests
//!
//! Tests for XSD validation retry logic:
//! - `basic_retry` - XSD retry pending flag and retry count tracking
//! - `exhaustion` - Max retry limit and exhaustion behavior
//! - `fallback` - Agent chain advancement when retries exhausted
//! - `loop_recovery` - Loop detection threshold and recovery reset

mod basic_retry;
mod exhaustion;
mod fallback;
mod loop_recovery;

use super::*;
use crate::reducer::state::{CommitValidatedOutcome, PlanningValidatedOutcome};
