// BoundedEventQueue Implementation
//
// This file contains the BoundedEventQueue struct, QueueMetrics, and all
// methods for the bounded event queue.

// ============================================================================
// Bounded Event Queue
// ============================================================================

// A bounded event queue with semaphore-based backpressure.
//
// This queue uses Rust's `sync_channel` which provides a bounded channel
// that blocks the sender when the buffer is full. This provides natural
// backpressure to prevent the producer from outpacing the consumer.
//
// Type Parameters:
//
// * `T` - The type of events in the queue (typically `String` for JSON events)
//
// Example:
//
// ```ignore
// let mut queue = BoundedEventQueue::<String>::new();
//
// // Producer: send events (blocks when full)
// queue.send("{\"type\": \"delta\"}".to_string()).unwrap();
//
// // Consumer: receive events (non-blocking)
// if let Some(event) = queue.try_recv() {
//     println!("Got event: {}", event);
// }
//
// // Get metrics
// let metrics = queue.metrics();
// println!("Queue depth: {}", metrics.depth);
// ```
#[derive(Debug)]
#[cfg(test)]
pub struct BoundedEventQueue<T> {
    sender: mpsc::SyncSender<T>,
    receiver: mpsc::Receiver<T>,
    metrics: QueueMetrics,
}

// Metrics tracking queue health and performance.
#[derive(Debug, Clone, Default)]
#[cfg(test)]
pub struct QueueMetrics {
    // Current number of events in the queue
    pub depth: usize,
    // Number of times backpressure was triggered (send blocked on full queue)
    pub backpressure_count: usize,
    // Maximum observed queue depth
    pub max_depth: usize,
}

#[cfg(test)]
impl<T: std::fmt::Debug> BoundedEventQueue<T> {
    // Create a new bounded event queue with default configuration.
    //
    // Example:
    // ```ignore
    // let queue: BoundedEventQueue<String> = BoundedEventQueue::new();
    // ```
    pub fn new() -> Self {
        let config = get_queue_config();
        Self::with_config(config)
    }

    // Create a new bounded event queue with specific configuration.
    //
    // Arguments:
    // * `config` - Queue configuration (capacity)
    //
    // Example:
    // ```ignore
    // let config = QueueConfig { capacity: 500 };
    // let queue: BoundedEventQueue<String> = BoundedEventQueue::with_config(config);
    // ```
    pub fn with_config(config: QueueConfig) -> Self {
        let (sender, receiver) = mpsc::sync_channel(config.capacity);
        Self {
            sender,
            receiver,
            metrics: QueueMetrics::default(),
        }
    }

    // Send an event to the queue, blocking if full.
    //
    // Behavior:
    //
    // - If queue has space: Event is sent immediately
    // - If queue is full: Blocks until space is available (backpressure)
    //
    // Arguments:
    // * `event` - The event to send
    //
    // Returns:
    // * `Ok(())` - Event was sent successfully
    // * `Err(mpsc::SendError(_))` - Receiver was dropped
    //
    // Example:
    // ```ignore
    // queue.send(event)?;
    // ```
    pub fn send(self, event: T) -> Self {
        match self.sender.send(event) {
            Ok(()) => {
                let new_depth = self.metrics.depth.saturating_add(1);
                Self {
                    sender: self.sender,
                    receiver: self.receiver,
                    metrics: QueueMetrics {
                        depth: new_depth,
                        backpressure_count: self.metrics.backpressure_count,
                        max_depth: self.metrics.max_depth.max(new_depth),
                    },
                }
            }
            Err(mpsc::SendError(event)) => {
                panic!("Receiver dropped unexpectedly: {:?}", event);
            }
        }
    }

    pub fn try_send(mut self, event: T) -> Self {
        match self.sender.try_send(event) {
            Ok(()) => {
                self.metrics.depth = self.metrics.depth.saturating_add(1);
                self.metrics.max_depth = self.metrics.max_depth.max(self.metrics.depth);
                self
            }
            Err(e) => {
                panic!("Try send failed: {:?}", e);
            }
        }
    }

    // Receive an event, blocking until one is available.
    //
    // Returns:
    // * `(Self, Ok(event))` - An event was received, along with the updated queue
    // * `(Self, Err(mpsc::RecvError))` - Sender was dropped
    //
    // Example:
    // ```ignore
    // let (queue, result) = queue.recv();
    // let event = result?;
    // ```
    pub fn recv(self) -> (Self, Result<T, mpsc::RecvError>) {
        match self.receiver.recv() {
            Ok(event) => {
                let mut new_self = self;
                new_self.metrics.depth = new_self.metrics.depth.saturating_sub(1);
                (new_self, Ok(event))
            }
            Err(e) => (self, Err(e)),
        }
    }

    // Get the current queue metrics.
    //
    // Example:
    // ```ignore
    // let metrics = queue.metrics();
    // println!("Depth: {}, Backpressure: {}", metrics.depth, metrics.backpressure_count);
    // ```
    #[must_use]
    pub const fn metrics(&self) -> &QueueMetrics {
        &self.metrics
    }

    // Get the current queue depth (number of pending events).
    //
    // This is an estimate that may not be perfectly accurate due to
    // concurrent access, but is sufficient for monitoring purposes.
    #[must_use]
    pub const fn depth(&self) -> usize {
        self.metrics.depth
    }

    // Check if the queue is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.depth() == 0
    }

    // Clear all events from the queue.
    //
    // This is useful for error recovery when invalid data is encountered.
    pub fn clear(self) -> Self {
        while self.receiver.try_recv().is_ok() {}
        Self {
            sender: self.sender,
            receiver: self.receiver,
            metrics: QueueMetrics {
                depth: 0,
                backpressure_count: self.metrics.backpressure_count,
                max_depth: self.metrics.max_depth,
            },
        }
    }

    // Reset metrics while preserving queue contents.
    pub fn reset_metrics(self) -> Self {
        Self {
            metrics: QueueMetrics {
                depth: self.metrics.depth,
                ..Default::default()
            },
            ..self
        }
    }
}

#[cfg(test)]
impl<T: std::fmt::Debug> Default for BoundedEventQueue<T> {
    fn default() -> Self {
        Self::new()
    }
}
