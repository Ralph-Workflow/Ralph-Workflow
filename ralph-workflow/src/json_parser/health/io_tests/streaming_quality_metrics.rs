    // Tests for StreamingQualityMetrics

    #[test]
    fn test_streaming_quality_metrics_empty() {
        let metrics = StreamingQualityMetrics::from_sizes(std::iter::empty());
        assert_eq!(metrics.total_deltas, 0);
        assert_eq!(metrics.avg_delta_size, 0);
        assert_eq!(metrics.min_delta_size, 0);
        assert_eq!(metrics.max_delta_size, 0);
        assert_eq!(metrics.pattern, StreamingPattern::Empty);
    }

    #[test]
    fn test_streaming_quality_metrics_single_delta() {
        let metrics = StreamingQualityMetrics::from_sizes([42].into_iter());
        assert_eq!(metrics.total_deltas, 1);
        assert_eq!(metrics.avg_delta_size, 42);
        assert_eq!(metrics.min_delta_size, 42);
        assert_eq!(metrics.max_delta_size, 42);
        // Single delta defaults to Normal pattern
        assert_eq!(metrics.pattern, StreamingPattern::Normal);
    }

    #[test]
    fn test_streaming_quality_metrics_uniform_sizes() {
        // All deltas same size - should be Smooth pattern
        let sizes = vec![10, 10, 10, 10, 10];
        let metrics = StreamingQualityMetrics::from_sizes(sizes.into_iter());
        assert_eq!(metrics.total_deltas, 5);
        assert_eq!(metrics.avg_delta_size, 10);
        assert_eq!(metrics.min_delta_size, 10);
        assert_eq!(metrics.max_delta_size, 10);
        assert_eq!(metrics.pattern, StreamingPattern::Smooth);
    }

    #[test]
    fn test_streaming_quality_metrics_varied_sizes() {
        // Moderately varied sizes - should be Normal pattern
        let sizes = vec![8, 10, 12, 9, 11];
        let metrics = StreamingQualityMetrics::from_sizes(sizes.into_iter());
        assert_eq!(metrics.total_deltas, 5);
        assert_eq!(metrics.avg_delta_size, 10);
        assert_eq!(metrics.min_delta_size, 8);
        assert_eq!(metrics.max_delta_size, 12);
        // Low variance, should be Smooth
        assert_eq!(metrics.pattern, StreamingPattern::Smooth);
    }

    #[test]
    fn test_streaming_quality_metrics_bursty() {
        // Highly varied sizes - should be Bursty pattern
        let sizes = vec![1, 100, 2, 200, 5];
        let metrics = StreamingQualityMetrics::from_sizes(sizes.into_iter());
        assert_eq!(metrics.total_deltas, 5);
        assert_eq!(metrics.min_delta_size, 1);
        assert_eq!(metrics.max_delta_size, 200);
        assert_eq!(metrics.pattern, StreamingPattern::Bursty);
    }

    #[test]
    fn test_streaming_quality_metrics_format() {
        let sizes = vec![10, 20, 15];
        let metrics = StreamingQualityMetrics::from_sizes(sizes.into_iter());
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("3 deltas"));
        assert!(formatted.contains("avg 15 bytes"));
        assert!(formatted.contains("min 10"));
        assert!(formatted.contains("max 20"));
    }

    #[test]
    fn test_streaming_quality_metrics_format_empty() {
        let metrics = StreamingQualityMetrics::from_sizes(std::iter::empty());
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("No deltas recorded"));
    }

    #[test]
    fn test_streaming_quality_metrics_format_with_snapshot_repairs() {
        let mut metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());
        metrics.snapshot_repairs_count = 2;
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("3 deltas"));
        assert!(formatted.contains("snapshot repairs: 2"));
    }

    #[test]
    fn test_streaming_quality_metrics_format_with_large_deltas() {
        let mut metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());
        metrics.large_delta_count = 5;
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("3 deltas"));
        assert!(formatted.contains("large deltas: 5"));
    }

    #[test]
    fn test_streaming_quality_metrics_format_with_protocol_violations() {
        let mut metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());
        metrics.protocol_violations = 1;
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("3 deltas"));
        assert!(formatted.contains("protocol violations: 1"));
    }

    #[test]
    fn test_streaming_quality_metrics_format_with_all_metrics() {
        let mut metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());
        metrics.snapshot_repairs_count = 2;
        metrics.large_delta_count = 5;
        metrics.protocol_violations = 1;
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("3 deltas"));
        assert!(formatted.contains("snapshot repairs: 2"));
        assert!(formatted.contains("large deltas: 5"));
        assert!(formatted.contains("protocol violations: 1"));
    }

    #[test]
    fn test_streaming_quality_metrics_new_fields_zero_by_default() {
        let metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());

        assert_eq!(metrics.snapshot_repairs_count, 0);
        assert_eq!(metrics.large_delta_count, 0);
        assert_eq!(metrics.protocol_violations, 0);
        assert_eq!(metrics.queue_depth, 0);
        assert_eq!(metrics.queue_dropped_events, 0);
        assert_eq!(metrics.queue_backpressure_count, 0);
    }

    #[test]
    fn test_streaming_quality_metrics_queue_metrics() {
        let mut metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());

        // Set queue metrics
        metrics.queue_depth = 5;
        metrics.queue_dropped_events = 2;
        metrics.queue_backpressure_count = 10;

        assert_eq!(metrics.queue_depth, 5);
        assert_eq!(metrics.queue_dropped_events, 2);
        assert_eq!(metrics.queue_backpressure_count, 10);
    }

    #[test]
    fn test_streaming_quality_metrics_format_with_queue_metrics() {
        let mut metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());
        metrics.queue_depth = 5;
        metrics.queue_dropped_events = 2;
        metrics.queue_backpressure_count = 10;
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("queue:"));
        assert!(formatted.contains("depth: 5"));
        assert!(formatted.contains("dropped: 2"));
        assert!(formatted.contains("backpressure: 10"));
    }

    #[test]
    fn test_streaming_quality_metrics_format_queue_depth_only() {
        let mut metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());
        metrics.queue_depth = 3;
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        assert!(formatted.contains("queue: depth: 3"));
    }

    #[test]
    fn test_streaming_quality_metrics_format_no_queue_metrics() {
        let metrics = StreamingQualityMetrics::from_sizes([10, 20, 15].into_iter());
        let colors = Colors { enabled: false };
        let formatted = metrics.format(colors);

        // Should not mention queue when all queue metrics are zero
        assert!(!formatted.contains("queue:"));
    }
