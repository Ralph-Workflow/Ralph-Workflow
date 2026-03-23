    // Tests for RollingHashWindow

    #[test]
    fn test_rolling_hash_compute_hash() {
        let hash1 = RollingHashWindow::compute_hash("Hello");
        let hash2 = RollingHashWindow::compute_hash("Hello");
        let hash3 = RollingHashWindow::compute_hash("World");

        // Same input produces same hash
        assert_eq!(hash1, hash2);
        // Different input likely produces different hash
        assert_ne!(hash1, hash3);
    }

    #[test]
    fn test_rolling_hash_add_content() {
        let mut window = RollingHashWindow::new();
        assert!(window.is_empty());

        window.add_content("Hello");
        assert_eq!(window.len(), 5);
        assert!(!window.is_empty());
    }

    #[test]
    fn test_rolling_hash_get_window_hashes() {
        let mut window = RollingHashWindow::new();
        window.add_content("HelloWorld");

        // Get hashes for 5-character windows
        let hashes = window.get_window_hashes(5);
        assert_eq!(hashes.len(), 6); // "Hello", "elloW", "lloWo", "loWor", "oWorl", "World"

        // Verify positions
        assert_eq!(hashes[0].0, 0); // First window starts at 0
        assert_eq!(hashes[5].0, 5); // Last window starts at 5
    }

    #[test]
    fn test_rolling_hash_contains_hash() {
        let mut window = RollingHashWindow::new();
        window.add_content("HelloWorld");

        let world_hash = RollingHashWindow::compute_hash("World");
        let xyz_hash = RollingHashWindow::compute_hash("XYZ");

        // "World" exists in the content
        assert!(window.contains_hash(world_hash, 5).is_some());
        // "XYZ" doesn't exist
        assert!(window.contains_hash(xyz_hash, 3).is_none());
    }

    #[test]
    fn test_rolling_hash_clear() {
        let mut window = RollingHashWindow::new();
        window.add_content("Hello");
        assert_eq!(window.len(), 5);

        window.clear();
        assert!(window.is_empty());
    }

