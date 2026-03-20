use super::{touch_activity, SharedActivityTimestamp};

/// A reader wrapper that updates an activity timestamp on every read.
///
/// Wraps any `Read` implementation and updates a shared atomic timestamp
/// whenever data is successfully read. This allows external monitoring of
/// read activity for idle timeout detection.
pub struct ActivityTrackingReader<R: std::io::Read> {
    inner: R,
    activity_timestamp: SharedActivityTimestamp,
}

impl<R: std::io::Read> ActivityTrackingReader<R> {
    /// Create a new activity-tracking reader.
    ///
    /// The provided timestamp will be updated to the current time
    /// whenever data is successfully read from the inner reader.
    pub fn new(inner: R, activity_timestamp: SharedActivityTimestamp) -> Self {
        touch_activity(&activity_timestamp);
        Self {
            inner,
            activity_timestamp,
        }
    }
}

impl<R: std::io::Read> std::io::Read for ActivityTrackingReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n = std::io::Read::read(&mut self.inner, buf)?;
        if n > 0 {
            touch_activity(&self.activity_timestamp);
        }
        Ok(n)
    }
}

/// A reader wrapper for stderr that updates an activity timestamp on every read.
///
/// This is similar to `ActivityTrackingReader` but designed specifically for
/// stderr tracking in a separate thread. It shares the same activity timestamp
/// as the stdout tracker, ensuring that any output (stdout OR stderr) prevents
/// idle timeout kills.
pub struct StderrActivityTracker<R: std::io::Read> {
    inner: R,
    activity_timestamp: SharedActivityTimestamp,
}

impl<R: std::io::Read> StderrActivityTracker<R> {
    pub const fn new(inner: R, activity_timestamp: SharedActivityTimestamp) -> Self {
        Self {
            inner,
            activity_timestamp,
        }
    }
}

impl<R: std::io::Read> std::io::Read for StderrActivityTracker<R> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n = std::io::Read::read(&mut self.inner, buf)?;
        if n > 0 {
            touch_activity(&self.activity_timestamp);
        }
        Ok(n)
    }
}
