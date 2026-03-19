// Tests for deduplication module.

#[cfg(test)]
mod tests {
    use super::*;

    include!("io_tests/rolling_hash_window.rs");
    include!("io_tests/kmp_matcher.rs");
    include!("io_tests/delta_deduplicator.rs");
    include!("io_tests/overlap_thresholds.rs");
}
