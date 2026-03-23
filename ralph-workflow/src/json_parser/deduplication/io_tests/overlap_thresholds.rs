    // Tests for Strong Overlap Detection with Thresholds

    #[test]
    fn test_strong_overlap_meets_char_threshold() {
        // Overlap of 30+ chars with safe boundary should pass
        let accumulated = "The quick brown fox jumps over the lazy";
        let delta = "The quick brown fox jumps over the lazy dog!";

        assert!(
            DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should detect snapshot with 30+ char overlap"
        );
    }

    #[test]
    fn test_strong_overlap_meets_ratio_threshold() {
        // Overlap is 50%+ of delta length
        let accumulated = "The quick brown fox jumps over the lazy dog. ";
        let delta = "The quick brown fox jumps over the lazy dog. And more!";

        assert!(
            DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should detect snapshot when overlap is 50%+ of delta"
        );
    }

    #[test]
    fn test_strong_overlap_fails_char_threshold() {
        // Overlap < 30 chars, even if ratio is good
        let accumulated = "Hello";
        let delta = "Hello World!";

        assert!(
            !DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should NOT detect snapshot when overlap < 30 chars"
        );
    }

    #[test]
    fn test_strong_overlap_fails_ratio_threshold() {
        // Overlap < 50% of delta, even if char count is good
        let accumulated = "The quick brown fox jumps over the lazy dog. ";
        let delta = "The quick brown fox jumps over the lazy dog. And then a whole lot more text follows to make the ratio low!";

        assert!(
            !DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should NOT detect snapshot when overlap < 50% of delta"
        );
    }

    #[test]
    fn test_boundary_check_whitespace() {
        // Overlap ends at space (safe boundary)
        let accumulated = "The quick brown fox jumps over the lazy dog and ";
        let delta = "The quick brown fox jumps over the lazy dog and then more!";

        assert!(
            DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should detect snapshot when overlap ends at whitespace"
        );
    }

    #[test]
    fn test_boundary_check_punctuation() {
        // Overlap ends at punctuation (safe boundary)
        let accumulated = "The quick brown fox jumps over the lazy dog.";
        let delta = "The quick brown fox jumps over the lazy dog. How are you?";

        assert!(
            DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should detect snapshot when overlap ends at punctuation"
        );
    }

    #[test]
    fn test_boundary_check_mid_word_fails() {
        // Overlap ends mid-word (unsafe boundary)
        let accumulated = "Hello";
        let delta = "HelloWorld! This is a lot of text to ensure we have enough characters.";

        // Even though we have 30+ chars, the boundary check should fail
        // because the overlap ends mid-word (at 'W' of "World")
        assert!(
            !DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should NOT detect snapshot when overlap ends mid-word"
        );
    }

    #[test]
    fn test_short_chunk_never_deduped() {
        // Short chunks (< 20 chars) never deduped unless exact match
        let accumulated = "Hello";
        let delta = "Hello World!";

        // Even though "Hello World!" starts with "Hello", it's < 20 chars total
        // and not an exact match, so it should NOT be deduped
        assert!(
            !DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Short chunks should NOT be deduped unless exact match"
        );
    }

    #[test]
    fn test_short_chunk_exact_match_deduped() {
        // Short chunks (< 20 chars) ARE deduped if exact match
        let accumulated = "Hello";
        let delta = "Hello";

        assert!(
            DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Short chunk exact match SHOULD be deduped"
        );
    }

    #[test]
    fn test_extract_new_content_with_thresholds_strong_overlap() {
        let accumulated = "The quick brown fox jumps over the lazy dog. ";
        let delta = "The quick brown fox jumps over the lazy dog. More content here!";

        let result = DeltaDeduplicator::extract_new_content_with_thresholds(delta, accumulated);
        assert_eq!(result, Some("More content here!"));
    }

    #[test]
    fn test_extract_new_content_with_thresholds_weak_overlap() {
        // Weak overlap (< 30 chars) should return None
        let accumulated = "Hello";
        let delta = "Hello World! This is more content to exceed thresholds.";

        let result = DeltaDeduplicator::extract_new_content_with_thresholds(delta, accumulated);
        assert_eq!(result, None, "Weak overlap should return None");
    }

    #[test]
    fn test_extract_new_content_with_thresholds_short_chunk() {
        // Short chunk that's not an exact match should return None
        let accumulated = "Hi";
        let delta = "Hi there!";

        let result = DeltaDeduplicator::extract_new_content_with_thresholds(delta, accumulated);
        assert_eq!(
            result, None,
            "Short chunk should return None unless exact match"
        );
    }

    #[test]
    fn test_extract_new_content_with_thresholds_short_chunk_exact() {
        // Short chunk exact match should return empty string
        let accumulated = "Hello";
        let delta = "Hello";

        let result = DeltaDeduplicator::extract_new_content_with_thresholds(delta, accumulated);
        assert_eq!(result, Some(""));
    }

    #[test]
    fn test_strong_overlap_with_unicode() {
        // Test with Unicode characters
        let accumulated = "Hello 世界! This is a long enough string to meet thresholds. ";
        let delta = "Hello 世界! This is a long enough string to meet thresholds. More!";

        assert!(
            DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Should handle Unicode correctly with strong overlap"
        );

        let result = DeltaDeduplicator::extract_new_content_with_thresholds(delta, accumulated);
        assert_eq!(result, Some("More!"));
    }

    #[test]
    fn test_intentional_repetition_not_deduped() {
        // Simulate intentional repetition (e.g., "Hello World! Hello World!")
        // where the overlap is small relative to the total delta
        let accumulated = "Hello World!";
        let delta = "Hello World! Hello World! This is a lot of additional content to make the overlap ratio low enough that it won't be deduplicated!";

        assert!(
            !DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Intentional repetition should NOT be deduped when overlap ratio is low"
        );
    }

    #[test]
    fn test_snapshot_strong_overlap_deduped() {
        // Real snapshot scenario: agent sends full accumulated + new content
        let accumulated = "The quick brown fox jumps over the lazy dog. ";
        let delta = "The quick brown fox jumps over the lazy dog. And then some more!";

        assert!(
            DeltaDeduplicator::is_likely_snapshot_with_thresholds(delta, accumulated),
            "Actual snapshot SHOULD be detected and deduped"
        );

        let result = DeltaDeduplicator::extract_new_content_with_thresholds(delta, accumulated);
        assert_eq!(result, Some("And then some more!"));
    }

    #[test]
    fn test_overlap_score_meets_thresholds() {
        let thresholds = OverlapThresholds::default();

        // Strong overlap: 30+ chars, 50%+ ratio, safe boundary
        let score = OverlapScore {
            char_count: 50,
            ratio_met: true,
            is_safe_boundary: true,
        };

        assert!(score.meets_thresholds(&thresholds));
    }

    #[test]
    fn test_overlap_score_fails_char_count() {
        let thresholds = OverlapThresholds::default();

        // Char count too low
        let score = OverlapScore {
            char_count: 20,
            ratio_met: true,
            is_safe_boundary: true,
        };

        assert!(!score.meets_thresholds(&thresholds));
    }

    #[test]
    fn test_overlap_score_fails_ratio() {
        let thresholds = OverlapThresholds::default();

        // Ratio too low (met = false)
        let score = OverlapScore {
            char_count: 50,
            ratio_met: false,
            is_safe_boundary: true,
        };

        assert!(!score.meets_thresholds(&thresholds));
    }

    #[test]
    fn test_overlap_score_fails_boundary() {
        let thresholds = OverlapThresholds::default();

        // Boundary not safe
        let score = OverlapScore {
            char_count: 50,
            ratio_met: true,
            is_safe_boundary: false,
        };

        assert!(!score.meets_thresholds(&thresholds));
    }

    #[test]
    fn test_is_short_delta() {
        let thresholds = OverlapThresholds::default();

        assert!(OverlapScore::is_short_delta(10, &thresholds));
        assert!(!OverlapScore::is_short_delta(30, &thresholds));
    }

    #[test]
    fn test_is_safe_boundary_whitespace() {
        assert!(is_safe_boundary("Hello World", 5));
        assert!(is_safe_boundary("Hello\nWorld", 5));
        assert!(is_safe_boundary("Hello\tWorld", 5));
    }

    #[test]
    fn test_is_safe_boundary_punctuation() {
        assert!(is_safe_boundary("Hello, World!", 12)); // After "!"
        assert!(is_safe_boundary("Hello. World", 5)); // After "."
        assert!(is_safe_boundary("Hello; World", 5)); // After ";"
    }

    #[test]
    fn test_is_safe_boundary_end_of_string() {
        assert!(is_safe_boundary("Hello", 5));
        assert!(is_safe_boundary("Hello", 10)); // Beyond length
    }

    #[test]
    fn test_is_safe_boundary_mid_word() {
        assert!(!is_safe_boundary("HelloWorld", 5));
        assert!(!is_safe_boundary("Testing", 3));
    }

    #[test]
    fn test_score_overlap_with_snapshot() {
        let accumulated = "The quick brown fox jumps over the lazy dog. ";
        let delta = "The quick brown fox jumps over the lazy dog. And more!";

        let score = score_overlap(delta, accumulated);

        assert!(score.char_count > 30);
        assert!(score.ratio_met);
        assert!(score.is_safe_boundary);
    }

    #[test]
    fn test_score_overlap_with_genuine_delta() {
        let accumulated = "Hello";
        let delta = " World";

        let score = score_overlap(delta, accumulated);

        assert_eq!(score.char_count, 0);
    }

    #[test]
    fn test_get_overlap_thresholds_default() {
        let thresholds = get_overlap_thresholds();

        assert_eq!(thresholds.min_overlap_chars, 30);
        assert_eq!(thresholds.short_chunk_threshold, 20);
        assert_eq!(thresholds.consecutive_duplicate_threshold, 3);
    }

    #[test]
    fn test_consecutive_duplicate_threshold_default() {
        let thresholds = OverlapThresholds::default();
        assert_eq!(
            thresholds.consecutive_duplicate_threshold, DEFAULT_CONSECUTIVE_DUPLICATE_THRESHOLD,
            "Default consecutive_duplicate_threshold should match constant"
        );
        assert_eq!(
            thresholds.consecutive_duplicate_threshold, 3,
            "Default consecutive_duplicate_threshold should be 3"
        );
    }

    /// Mock environment for testing threshold parsing.
    struct MockThresholdEnv {
        vars: std::collections::HashMap<String, String>,
    }

    impl MockThresholdEnv {
        fn new() -> Self {
            Self {
                vars: std::collections::HashMap::new(),
            }
        }

        fn with_var(mut self, key: &str, value: &str) -> Self {
            self.vars.insert(key.to_string(), value.to_string());
            self
        }
    }

    impl ThresholdEnvironment for MockThresholdEnv {
        fn get_var(&self, name: &str) -> Option<String> {
            self.vars.get(name).cloned()
        }
    }

    #[test]
    fn test_threshold_env_parsing_min_overlap_chars() {
        // Test valid custom value
        let env = MockThresholdEnv::new().with_var("RALPH_STREAMING_MIN_OVERLAP_CHARS", "50");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.min_overlap_chars, 50);

        // Test out of range (too low) - should use default
        let env = MockThresholdEnv::new().with_var("RALPH_STREAMING_MIN_OVERLAP_CHARS", "5");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.min_overlap_chars, DEFAULT_MIN_OVERLAP_CHARS);

        // Test out of range (too high) - should use default
        let env = MockThresholdEnv::new().with_var("RALPH_STREAMING_MIN_OVERLAP_CHARS", "200");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.min_overlap_chars, DEFAULT_MIN_OVERLAP_CHARS);

        // Test invalid value - should use default
        let env = MockThresholdEnv::new().with_var("RALPH_STREAMING_MIN_OVERLAP_CHARS", "invalid");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.min_overlap_chars, DEFAULT_MIN_OVERLAP_CHARS);
    }

    #[test]
    fn test_threshold_env_parsing_short_chunk_threshold() {
        // Test valid custom value
        let env = MockThresholdEnv::new().with_var("RALPH_STREAMING_SHORT_CHUNK_THRESHOLD", "10");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.short_chunk_threshold, 10);

        // Test boundary values
        let env = MockThresholdEnv::new().with_var("RALPH_STREAMING_SHORT_CHUNK_THRESHOLD", "5");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.short_chunk_threshold, 5); // Min boundary

        let env = MockThresholdEnv::new().with_var("RALPH_STREAMING_SHORT_CHUNK_THRESHOLD", "50");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.short_chunk_threshold, 50); // Max boundary
    }

    #[test]
    fn test_threshold_env_parsing_consecutive_duplicate() {
        // Test valid custom value
        let env = MockThresholdEnv::new()
            .with_var("RALPH_STREAMING_CONSECUTIVE_DUPLICATE_THRESHOLD", "5");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.consecutive_duplicate_threshold, 5);

        // Test min boundary
        let env = MockThresholdEnv::new()
            .with_var("RALPH_STREAMING_CONSECUTIVE_DUPLICATE_THRESHOLD", "2");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.consecutive_duplicate_threshold, 2);

        // Test max boundary
        let env = MockThresholdEnv::new()
            .with_var("RALPH_STREAMING_CONSECUTIVE_DUPLICATE_THRESHOLD", "10");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.consecutive_duplicate_threshold, 10);

        // Test out of range - should use default
        let env = MockThresholdEnv::new()
            .with_var("RALPH_STREAMING_CONSECUTIVE_DUPLICATE_THRESHOLD", "1");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(
            thresholds.consecutive_duplicate_threshold,
            DEFAULT_CONSECUTIVE_DUPLICATE_THRESHOLD
        );

        let env = MockThresholdEnv::new()
            .with_var("RALPH_STREAMING_CONSECUTIVE_DUPLICATE_THRESHOLD", "15");
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(
            thresholds.consecutive_duplicate_threshold,
            DEFAULT_CONSECUTIVE_DUPLICATE_THRESHOLD
        );
    }

    #[test]
    fn test_threshold_env_empty_returns_defaults() {
        let env = MockThresholdEnv::new();
        let thresholds = get_overlap_thresholds_with_env(&env);
        assert_eq!(thresholds.min_overlap_chars, DEFAULT_MIN_OVERLAP_CHARS);
        assert_eq!(
            thresholds.short_chunk_threshold,
            DEFAULT_SHORT_CHUNK_THRESHOLD
        );
        assert_eq!(
            thresholds.consecutive_duplicate_threshold,
            DEFAULT_CONSECUTIVE_DUPLICATE_THRESHOLD
        );
    }

    #[test]
    fn test_threshold_bounds_constants() {
        // Verify bounds constants are correct (pure constant tests, no env var manipulation)
        assert_eq!(
            MIN_CONSECUTIVE_DUPLICATE_THRESHOLD, 2,
            "Minimum threshold should be 2"
        );
        assert_eq!(
            MAX_CONSECUTIVE_DUPLICATE_THRESHOLD, 10,
            "Maximum threshold should be 10"
        );
        assert_eq!(MIN_MIN_OVERLAP_CHARS, 10);
        assert_eq!(MAX_MIN_OVERLAP_CHARS, 100);
        assert_eq!(MIN_SHORT_CHUNK_THRESHOLD, 5);
        assert_eq!(MAX_SHORT_CHUNK_THRESHOLD, 50);
    }
