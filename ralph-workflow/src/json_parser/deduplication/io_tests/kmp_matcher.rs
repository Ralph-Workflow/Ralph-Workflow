    // Tests for KMPMatcher

    #[test]
    fn test_kmp_find_pattern_exists() {
        let kmp = KMPMatcher::new("World");
        assert_eq!(kmp.find("Hello World"), Some(6));
        assert_eq!(kmp.find("WorldHello"), Some(0));
    }

    #[test]
    fn test_kmp_find_pattern_not_exists() {
        let kmp = KMPMatcher::new("XYZ");
        assert_eq!(kmp.find("Hello World"), None);
    }

    #[test]
    fn test_kmp_find_pattern_empty() {
        let kmp = KMPMatcher::new("");
        assert_eq!(kmp.find("Hello"), None);
    }

    #[test]
    fn test_kmp_find_text_shorter_than_pattern() {
        let kmp = KMPMatcher::new("Hello World");
        assert_eq!(kmp.find("Hello"), None);
    }

    #[test]
    fn test_kmp_find_all() {
        let kmp = KMPMatcher::new("ab");
        let positions = kmp.find_all("ababab");
        assert_eq!(positions, vec![0, 2, 4]);
    }

    #[test]
    fn test_kmp_find_all_no_matches() {
        let kmp = KMPMatcher::new("xyz");
        let positions = kmp.find_all("abcabc");
        assert!(positions.is_empty());
    }

    #[test]
    fn test_kmp_find_overlapping_patterns() {
        let kmp = KMPMatcher::new("aa");
        let positions = kmp.find_all("aaa");
        assert_eq!(positions, vec![0, 1]);
    }

    #[test]
    fn test_kmp_failure_function() {
        let kmp = KMPMatcher::new("abab");
        // lps = [0, 0, 1, 2]
        assert_eq!(kmp.failure, vec![0, 0, 1, 2]);
    }

    #[test]
    fn test_kmp_pattern_len() {
        let kmp = KMPMatcher::new("Hello");
        assert_eq!(kmp.pattern_len(), 5);
    }

    #[test]
    fn test_kmp_is_empty() {
        let kmp = KMPMatcher::new("");
        assert!(kmp.is_empty());

        let kmp = KMPMatcher::new("Hello");
        assert!(!kmp.is_empty());
    }

