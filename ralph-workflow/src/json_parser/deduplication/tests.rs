// Tests for deduplication module.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json_parser::deduplication::kmp_matcher::KMPMatcher;

    include!("io_tests/rolling_hash_window.rs");
    include!("io_tests/kmp_matcher.rs");
    include!("io_tests/delta_deduplicator.rs");
    include!("io_tests/overlap_thresholds.rs");
}
