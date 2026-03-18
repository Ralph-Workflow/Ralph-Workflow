//! Clock trait and implementations for idle timeout monitoring.
//! This module re-exports from the runtime boundary module.

pub use super::runtime::clock::{
    is_idle_timeout_exceeded, is_idle_timeout_exceeded_with_clock, new_activity_timestamp,
    new_activity_timestamp_with_clock, new_file_activity_tracker, time_since_activity,
    time_since_activity_with_clock, touch_activity, touch_activity_with_clock, Clock,
    MonotonicClock, IDLE_TIMEOUT_SECS,
};

pub type SharedActivityTimestamp = super::runtime::clock::SharedActivityTimestamp;
pub type SharedFileActivityTracker = super::runtime::clock::SharedFileActivityTracker;
