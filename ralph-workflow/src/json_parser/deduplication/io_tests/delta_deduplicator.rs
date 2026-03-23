    // Tests for DeltaDeduplicator

    #[test]
    fn test_dedup_extract_new_content_snapshot() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello");

        // Snapshot: "Hello World"
        assert_eq!(
            DeltaDeduplicator::extract_new_content("Hello World", "Hello"),
            Some(" World")
        );
    }

    #[test]
    fn test_dedup_extract_new_content_genuine_delta() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello");

        // Genuine delta: " World"
        assert_eq!(
            DeltaDeduplicator::extract_new_content(" World", "Hello"),
            None
        );
    }

    #[test]
    fn test_dedup_extract_new_content_shorter_delta() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello World");

        // Delta shorter than accumulated - can't be snapshot
        assert_eq!(
            DeltaDeduplicator::extract_new_content("Hello", "Hello World"),
            None
        );
    }

    #[test]
    fn test_dedup_extract_new_content_equal_length() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello");

        // Equal length - if identical, return empty string (no new content)
        assert_eq!(
            DeltaDeduplicator::extract_new_content("Hello", "Hello"),
            Some("")
        );

        // Equal length but different content - not a snapshot
        assert_eq!(
            DeltaDeduplicator::extract_new_content("World", "Hello"),
            None
        );
    }

    #[test]
    fn test_dedup_extract_new_content_no_match() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello");

        // Different content entirely
        assert_eq!(
            DeltaDeduplicator::extract_new_content("World", "Hello"),
            None
        );
    }

    #[test]
    fn test_dedup_is_likely_snapshot() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello");

        // Actual snapshot
        assert!(DeltaDeduplicator::is_likely_snapshot(
            "Hello World",
            "Hello"
        ));

        // Not a snapshot
        assert!(!DeltaDeduplicator::is_likely_snapshot(" World", "Hello"));
    }

    #[test]
    fn test_dedup_clear() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello");
        assert!(!dedup.hash_window.is_empty());

        dedup.clear();
        assert!(dedup.hash_window.is_empty());
    }

    // Integration tests

    #[test]
    fn test_dedup_two_phase_algorithm() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("The quick brown fox");

        // Phase 1: Rolling hash will match
        assert!(DeltaDeduplicator::is_likely_snapshot(
            "The quick brown fox jumps",
            "The quick brown fox"
        ));

        // Phase 2: KMP verification confirms and extracts new portion
        assert_eq!(
            DeltaDeduplicator::extract_new_content(
                "The quick brown fox jumps",
                "The quick brown fox"
            ),
            Some(" jumps")
        );
    }

    #[test]
    fn test_dedup_handles_unicode() {
        let mut dedup = DeltaDeduplicator::new();
        dedup.add_accumulated("Hello 世界");

        // Should handle UTF-8 correctly
        assert_eq!(
            DeltaDeduplicator::extract_new_content("Hello 世界!", "Hello 世界"),
            Some("!")
        );
    }

    #[test]
    fn test_dedup_empty_accumulated() {
        // No accumulated content

        // Any delta is genuine
        assert_eq!(DeltaDeduplicator::extract_new_content("Hello", ""), None);
    }

