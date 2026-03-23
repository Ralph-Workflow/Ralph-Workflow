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
        let queue: BoundedEventQueue<String> = BoundedEventQueue::new();
        let event = "test_event".to_string();

        let queue = queue.send(event.clone());
        assert!(!queue.is_empty());

        let (_queue, received) = queue.recv();
        assert_eq!(received.unwrap(), event);
    }

    #[test]
    fn test_queue_multiple_events() {
        let mut queue: BoundedEventQueue<i32> =
            (0..10).fold(BoundedEventQueue::new(), |q, i| q.send(i));

        for i in 0..10 {
            let (new_queue, received) = queue.recv();
            queue = new_queue;
            assert_eq!(received.unwrap(), i);
        }
    }

    #[test]
    fn test_queue_try_send_full() {
        let config = QueueConfig { capacity: 2 };
        let queue: BoundedEventQueue<i32> = BoundedEventQueue::with_config(config);

        let queue = queue.try_send(1);
        let queue = queue.try_send(2);

        let queue = queue.try_send(3);
        assert!(queue.metrics().backpressure_count > 0);
    }

    #[test]
    fn test_queue_clear() {
        let queue: BoundedEventQueue<i32> = (0..5).fold(BoundedEventQueue::new(), |q, i| q.send(i));

        let queue = queue.clear();
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
        let queue: BoundedEventQueue<String> = BoundedEventQueue::new().send("test".to_string());

        let queue = queue.reset_metrics();

        let metrics = queue.metrics();
        assert_eq!(metrics.depth, 1);
        assert_eq!(metrics.backpressure_count, 0);
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
        let queue: BoundedEventQueue<i32> = BoundedEventQueue::new().send(1).send(2);

        let (queue, _) = queue.recv();

        assert_eq!(queue.depth(), 1);
    }

    #[test]
    fn test_queue_recv_blocking() {
        let queue: BoundedEventQueue<String> = BoundedEventQueue::new().send("test".to_string());
        let (_, received) = queue.recv();
        assert_eq!(received.unwrap(), "test");
    }

    #[test]
    fn test_queue_backpressure_tracking() {
        let config = QueueConfig { capacity: 2 };
        let queue: BoundedEventQueue<i32> = BoundedEventQueue::with_config(config);

        let queue = queue.try_send(1);
        let queue = queue.try_send(2);

        let queue = queue.try_send(3);

        let metrics = queue.metrics();
        assert_eq!(metrics.depth, 2);
    }

    #[test]
    fn test_queue_max_depth_tracking() {
        let config = QueueConfig { capacity: 10 };
        let queue: BoundedEventQueue<i32> = BoundedEventQueue::with_config(config);

        let queue = (0..5).fold(queue, |q, i| q.send(i));

        let metrics = queue.metrics();
        assert_eq!(metrics.max_depth, 5);

        let queue = (5..8).fold(queue, |q, i| q.send(i));

        let metrics = queue.metrics();
        assert_eq!(metrics.max_depth, 8);
    }
}
