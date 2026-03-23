pub fn capture_time() -> std::time::Instant {
    crate::phases::runtime::timing::capture_time()
}

pub fn elapsed(start: std::time::Instant) -> std::time::Duration {
    crate::phases::runtime::timing::elapsed(start)
}

pub fn elapsed_seconds(start: std::time::Instant) -> u64 {
    crate::phases::runtime::timing::elapsed_seconds(start)
}
