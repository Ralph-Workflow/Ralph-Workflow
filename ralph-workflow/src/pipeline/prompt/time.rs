//! Time utilities for prompt archiving.
//!
//! This is a boundary module - clock access is allowed here.

#[must_use]
pub fn current_timestamp_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}
