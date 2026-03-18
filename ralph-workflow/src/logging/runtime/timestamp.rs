//! Runtime timestamp access for logging.
//!
//! This module satisfies the dylint boundary-module check for code that
//! accesses system clocks.

use chrono::Utc;

/// Get the current timestamp in RFC3339 format.
///
/// This is a boundary function that wraps `Utc::now().to_rfc3339()`.
pub fn get_current_timestamp() -> String {
    Utc::now().to_rfc3339()
}
