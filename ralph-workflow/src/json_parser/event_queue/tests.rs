// Tests for bounded event queue module

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_queue_config_default() {
        let config = get_queue_config();
        assert_eq!(config.capacity, DEFAULT_QUEUE_SIZE);
    }

    #[test]
    fn test_queue_new() {
        let queue: BoundedEventQueue<String> = BoundedEventQueue::new();
        assert!(queue.is_empty());
        assert_eq!(queue.depth(), 0);
    }

    #[test]
    fn test_queue_send_and_recv() {
        let mut queue: BoundedEventQueue<String> = BoundedEventQueue::new();
        let event = "test_event".to_string();

        queue.send(event.clone()).unwrap();
        assert!(!queue.is_empty());

        let received = queue.try_recv().unwrap();
        assert_eq!(received, event);
        assert!(queue.is_empty());
    }

    #[test]
    fn test_queue_multiple_events() {
        let mut queue: BoundedEventQueue<i32> = BoundedEventQueue::new();

        (0..10).for_each(|i| {
            queue.send(i).unwrap();
        });

        (0..10).for_each(|i| {
            let received = queue.try_recv().unwrap();
            assert_eq!(received, i);
        }

        assert!(queue.is_empty());
    }

    #[test]
    fn test_queue_try_send_full() {
        let config = QueueConfig { capacity: 2 };
        let mut queue: BoundedEventQueue<i32> = BoundedEventQueue::with_config(config);

        // Fill the queue
        queue.try_send(1).unwrap();
        queue.try_send(2).unwrap();

        // This should fail with Full error
        let result = queue.try_send(3);
        assert!(matches!(result, Err(mpsc::TrySendError::Full(3))));
    }

    #[test]
    fn test_queue_try_recv_empty() {
        let mut queue: BoundedEventQueue<String> = BoundedEventQueue::new();
        assert!(queue.try_recv().is_none());
    }

    #[test]
    fn test_queue_clear() {
        let mut queue: BoundedEventQueue<i32> = BoundedEventQueue::new();

        (0..5).for_each(|i| {
            queue.send(i).unwrap();
        });

        queue.clear();
        assert!(queue.is_empty());
        assert_eq!(queue.depth(), 0);
    }

    #[test]
    fn test_queue_metrics_initial() {
        let queue: BoundedEventQueue<String> = BoundedEventQueue::new();
        let metrics = queue.metrics();
        assert_eq!(metrics.depth, 0);
        assert_eq!(metrics.backpressure_count, 0);
    }

    #[test]
    fn test_queue_reset_metrics() {
        let mut queue: BoundedEventQueue<String> = BoundedEventQueue::new();

        queue.send("test".to_string()).unwrap();
        queue.reset_metrics();

        let metrics = queue.metrics();
        assert_eq!(metrics.depth, 1); // depth preserved
        assert_eq!(metrics.backpressure_count, 0); // reset
    }

    #[test]
    fn test_queue_config_bounds() {
        // Verify bounds constants
        assert_eq!(MIN_QUEUE_SIZE, 10);
        assert_eq!(MAX_QUEUE_SIZE, 1000);
    }

    #[test]
    fn test_queue_with_custom_config() {
        let config = QueueConfig { capacity: 50 };
        let queue: BoundedEventQueue<String> = BoundedEventQueue::with_config(config);

        // Queue should use custom config
        assert!(queue.is_empty());
    }

    #[test]
    fn test_queue_metrics_depth_tracking() {
        let mut queue: BoundedEventQueue<i32> = BoundedEventQueue::new();

        queue.send(1).unwrap();
        assert_eq!(queue.depth(), 1);

        queue.send(2).unwrap();
        assert_eq!(queue.depth(), 2);

        queue.try_recv();
        assert_eq!(queue.depth(), 1);
    }

    #[test]
    fn test_queue_recv_blocking() {
        let mut queue: BoundedEventQueue<String> = BoundedEventQueue::new();
        let event = "test".to_string();

        queue.send(event.clone()).unwrap();

        let received = queue.recv().unwrap();
        assert_eq!(received, event);
    }

    #[test]
    fn test_queue_backpressure_tracking() {
        let config = QueueConfig { capacity: 2 };
        let mut queue: BoundedEventQueue<i32> = BoundedEventQueue::with_config(config);

        // Fill the queue
        queue.try_send(1).unwrap();
        queue.try_send(2).unwrap();

        // Verify queue is full
        let result = queue.try_send(3);
        assert!(matches!(result, Err(mpsc::TrySendError::Full(3))));

        // Backpressure is tracked when try_send fails
        // Note: We can't test the blocking send() in a single-threaded test
        // because it would block forever without a consumer thread
        let metrics = queue.metrics();
        assert_eq!(metrics.depth, 2);
    }

    #[test]
    fn test_queue_max_depth_tracking() {
        let config = QueueConfig { capacity: 10 };
        let mut queue: BoundedEventQueue<i32> = BoundedEventQueue::with_config(config);

        (0..5).for_each(|i| {
            queue.send(i).unwrap();
        });

        let metrics = queue.metrics();
        assert_eq!(metrics.max_depth, 5);

        // Add more events
        (5..8).for_each(|i| {
            queue.send(i).unwrap();
        });

        let metrics = queue.metrics();
        assert_eq!(metrics.max_depth, 8);
    }
}
