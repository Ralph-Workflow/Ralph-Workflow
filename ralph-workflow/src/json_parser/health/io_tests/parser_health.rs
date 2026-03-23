    #[test]
    fn test_parser_health_new() {
        let health = ParserHealth::new();
        assert_eq!(health.total_events, 0);
        assert_eq!(health.parsed_events, 0);
        assert_eq!(health.ignored_events, 0);
    }

    #[test]
    fn test_parser_health_record_parsed() {
        let mut health = ParserHealth::new();
        health.record_parsed();
        assert_eq!(health.total_events, 1);
        assert_eq!(health.parsed_events, 1);
        assert_eq!(health.ignored_events, 0);
    }

    #[test]
    fn test_parser_health_record_ignored() {
        let mut health = ParserHealth::new();
        health.record_ignored();
        assert_eq!(health.total_events, 1);
        assert_eq!(health.parsed_events, 0);
        assert_eq!(health.ignored_events, 1);
    }

    #[test]
    fn test_parser_health_is_concerning() {
        let mut health = ParserHealth::new();
        // Not concerning with few events
        for _ in 0..3 {
            health.record_ignored();
        }
        assert!(!health.is_concerning());

        // Unknown events should NOT trigger concerning state (they're valid JSON)
        for _ in 0..20 {
            health.record_unknown_event();
        }
        assert!(!health.is_concerning()); // Even with many unknown events, not concerning

        // Only parse errors trigger concerning state
        let mut health2 = ParserHealth::new();
        for _ in 0..10 {
            health2.record_parsed();
        }
        for _ in 0..15 {
            health2.record_parse_error();
        }
        assert!(health2.is_concerning()); // 25 total, 60% parse errors

        // Not concerning when most are parsed or unknown (but few parse errors)
        let mut health3 = ParserHealth::new();
        for _ in 0..15 {
            health3.record_parsed();
        }
        for _ in 0..10 {
            health3.record_unknown_event();
        }
        for _ in 0..2 {
            health3.record_parse_error();
        }
        assert!(!health3.is_concerning()); // 27 total, only 7% parse errors
    }

    #[test]
    fn test_parser_health_unknown_events() {
        let mut health = ParserHealth::new();
        assert_eq!(health.unknown_events, 0);

        health.record_unknown_event();
        health.record_unknown_event();
        assert_eq!(health.unknown_events, 2);
        assert_eq!(health.ignored_events, 2); // unknown counts as ignored
        assert_eq!(health.parse_errors, 0); // but not as parse error

        // Unknown events don't make it concerning
        assert!(!health.is_concerning());
    }

    #[test]
    fn test_health_monitor() {
        let monitor = HealthMonitor::new("claude");

        monitor.record_parsed();
        monitor.record_parsed();
        monitor.record_ignored();

        let colors = Colors { enabled: false };
        // Behavioral test: monitor should not warn for healthy parsing
        assert!(monitor.check_and_warn(colors).is_none());

        // Behavioral test: creating a new monitor gives fresh state (instead of reset)
        let fresh_monitor = HealthMonitor::new("claude");
        // Fresh monitor should not have warned yet
        assert!(fresh_monitor.check_and_warn(colors).is_none());
    }

    #[test]
    fn test_health_monitor_warns_once() {
        let monitor = HealthMonitor::new("test");
        let colors = Colors { enabled: false };

        // Add enough parse errors to trigger warning (unknown events shouldn't trigger)
        for _ in 0..15 {
            monitor.record_parse_error();
        }

        let warning1 = monitor.check_and_warn(colors);
        assert!(warning1.is_some());

        let warning2 = monitor.check_and_warn(colors);
        assert!(warning2.is_none()); // Already warned
    }

    #[test]
    fn test_health_monitor_many_unknown_no_warning() {
        let monitor = HealthMonitor::new("test");
        let colors = Colors { enabled: false };

        // Add many unknown events (simulating 97.5% unknown like the bug report)
        for _ in 0..2049 {
            monitor.record_unknown_event();
        }
        for _ in 0..53 {
            monitor.record_parsed();
        }

        let warning = monitor.check_and_warn(colors);
        assert!(warning.is_none()); // Should NOT warn even with 97.5% unknown events
    }

    #[test]
    fn test_health_monitor_mixed_unknown_and_parse_errors() {
        let monitor = HealthMonitor::new("test");
        let colors = Colors { enabled: false };

        // Mix of unknown and parse errors - only parse errors count for warning
        for _ in 0..100 {
            monitor.record_unknown_event();
        }
        for _ in 0..20 {
            monitor.record_parse_error();
        }
        for _ in 0..20 {
            monitor.record_parsed();
        }

        // 140 total events, 20 parse errors = ~14% (not concerning)
        let warning = monitor.check_and_warn(colors);
        assert!(warning.is_none());

        // Add more parse errors to trigger warning
        for _ in 0..30 {
            monitor.record_parse_error();
        }

        // 170 total events, 50 parse errors = ~29% (still not concerning)
        let warning = monitor.check_and_warn(colors);
        assert!(warning.is_none());

        // Add even more parse errors
        for _ in 0..60 {
            monitor.record_parse_error();
        }

        // 230 total events, 110 parse errors = ~48% (close to threshold)
        let warning = monitor.check_and_warn(colors);
        assert!(warning.is_none());

        // Push it over 50%
        for _ in 0..30 {
            monitor.record_parse_error();
        }

        // 260 total events, 140 parse errors = ~54% (concerning!)
        let warning = monitor.check_and_warn(colors);
        assert!(warning.is_some());
    }

    #[test]
    fn test_parser_health_parse_error_percentage() {
        let mut health = ParserHealth::new();
        assert!((health.parse_error_percentage() - 0.0).abs() < f64::EPSILON);

        // Parse errors only
        for _ in 0..5 {
            health.record_parse_error();
        }
        assert!((health.parse_error_percentage() - 100.0).abs() < f64::EPSILON);

        // Add parsed events
        let mut health2 = ParserHealth::new();
        for _ in 0..5 {
            health2.record_parse_error();
        }
        for _ in 0..5 {
            health2.record_parsed();
        }
        assert!((health2.parse_error_percentage() - 50.0).abs() < f64::EPSILON);

        // Unknown events don't affect parse error percentage
        let mut health3 = ParserHealth::new();
        for _ in 0..5 {
            health3.record_parse_error();
        }
        for _ in 0..10 {
            health3.record_unknown_event();
        }
        for _ in 0..5 {
            health3.record_parsed();
        }
        // 20 total, 5 parse errors = 25%
        assert!((health3.parse_error_percentage() - 25.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_parser_health_control_events() {
        let mut health = ParserHealth::new();
        assert_eq!(health.control_events, 0);

        health.record_control_event();
        health.record_control_event();
        health.record_control_event();
        assert_eq!(health.control_events, 3);
        assert_eq!(health.total_events, 3);
        // Control events do NOT count as ignored
        assert_eq!(health.ignored_events, 0);
        assert_eq!(health.unknown_events, 0);

        // Control events don't make it concerning
        assert!(!health.is_concerning());
    }

    #[test]
    fn test_parser_health_control_events_with_other_types() {
        let mut health = ParserHealth::new();

        // Mix of control, parsed, and unknown events
        for _ in 0..100 {
            health.record_control_event();
        }
        for _ in 0..50 {
            health.record_parsed();
        }
        for _ in 0..30 {
            health.record_unknown_event();
        }

        // 180 total events
        assert_eq!(health.total_events, 180);
        assert_eq!(health.control_events, 100);
        assert_eq!(health.parsed_events, 50);
        assert_eq!(health.unknown_events, 30);
        assert_eq!(health.ignored_events, 30); // only unknown counts as ignored

        // Not concerning - no parse errors
        assert!(!health.is_concerning());
        assert!((health.parse_error_percentage() - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_health_monitor_control_events() {
        let monitor = HealthMonitor::new("test");
        let colors = Colors { enabled: false };

        // Add many control events (like MessageStart, ContentBlockStart, etc.)
        for _ in 0..2000 {
            monitor.record_control_event();
        }
        // Add some parsed events
        for _ in 0..50 {
            monitor.record_parsed();
        }

        // Behavioral test: control events don't trigger warnings
        // The monitor has many control events but few parsed events
        let warning = monitor.check_and_warn(colors);
        // Should NOT warn even with many "non-displayed" events
        // because they're control events, not ignored/parse errors
        assert!(warning.is_none());
    }

    #[test]
    fn test_health_monitor_warning_includes_control_events() {
        let monitor = HealthMonitor::new("test");
        let colors = Colors { enabled: false };

        // Add parse errors to trigger warning
        for _ in 0..15 {
            monitor.record_parse_error();
        }
        // Add some control events
        for _ in 0..10 {
            monitor.record_control_event();
        }

        let warning = monitor.check_and_warn(colors);
        assert!(warning.is_some());

        let warning_text = warning.unwrap();
        // Warning should mention control events
        assert!(warning_text.contains("10 control events"));
    }

    #[test]
    fn test_parser_health_partial_events() {
        let mut health = ParserHealth::new();
        assert_eq!(health.partial_events, 0);

        health.record_partial_event();
        health.record_partial_event();
        health.record_partial_event();
        assert_eq!(health.partial_events, 3);
        assert_eq!(health.total_events, 3);
        // Partial events do NOT count as ignored
        assert_eq!(health.ignored_events, 0);
        assert_eq!(health.unknown_events, 0);

        // Partial events don't make it concerning
        assert!(!health.is_concerning());
    }

    #[test]
    fn test_parser_health_partial_events_with_other_types() {
        let mut health = ParserHealth::new();

        // Mix of partial, control, parsed, and unknown events
        for _ in 0..100 {
            health.record_partial_event();
        }
        for _ in 0..50 {
            health.record_control_event();
        }
        for _ in 0..30 {
            health.record_parsed();
        }
        for _ in 0..20 {
            health.record_unknown_event();
        }

        // 200 total events
        assert_eq!(health.total_events, 200);
        assert_eq!(health.partial_events, 100);
        assert_eq!(health.control_events, 50);
        assert_eq!(health.parsed_events, 30);
        assert_eq!(health.unknown_events, 20);
        assert_eq!(health.ignored_events, 20); // only unknown counts as ignored

        // Not concerning - no parse errors
        assert!(!health.is_concerning());
        assert!((health.parse_error_percentage() - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_health_monitor_partial_events() {
        let monitor = HealthMonitor::new("test");
        let colors = Colors { enabled: false };

        // Add many partial events (simulating streaming deltas)
        for _ in 0..2049 {
            monitor.record_partial_event();
        }
        // Add some parsed events
        for _ in 0..53 {
            monitor.record_parsed();
        }

        // Behavioral test: partial events don't trigger warnings
        // The monitor has many partial events but few parsed events
        let warning = monitor.check_and_warn(colors);
        // Should NOT warn even with many "partial" events
        // because partial events are valid streaming content, not errors
        assert!(warning.is_none());
    }

    #[test]
    fn test_health_monitor_warning_includes_partial_events() {
        let monitor = HealthMonitor::new("test");
        let colors = Colors { enabled: false };

        // Add parse errors to trigger warning (need >50% of total)
        for _ in 0..15 {
            monitor.record_parse_error();
        }
        // Add some partial events (these don't count toward parse error %)
        for _ in 0..10 {
            monitor.record_partial_event();
        }
        // Add some control events (these also don't count toward parse error %)
        for _ in 0..2 {
            monitor.record_control_event();
        }

        let warning = monitor.check_and_warn(colors);
        assert!(warning.is_some());

        let warning_text = warning.unwrap();
        // Warning should mention both control and partial events
        assert!(warning_text.contains("2 control events"));
        assert!(warning_text.contains("10 partial events"));
    }

