use std::time::{Duration, Instant};

pub fn capture_time() -> Instant {
    Instant::now()
}

pub fn elapsed_seconds(start: Instant) -> u64 {
    start.elapsed().as_secs()
}

pub fn elapsed(start: Instant) -> Duration {
    start.elapsed()
}
