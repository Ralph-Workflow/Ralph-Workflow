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
impl<T> BoundedEventQueue<T> {
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
    pub fn send(&mut self, event: T) -> Result<(), mpsc::SendError<T>> {
        // Try to send without blocking first
        match self.sender.try_send(event) {
            Ok(()) => {
                // Update depth estimate
                self.metrics.depth = self.metrics.depth.saturating_add(1);
                self.metrics.max_depth = self.metrics.max_depth.max(self.metrics.depth);
                Ok(())
            }
            Err(mpsc::TrySendError::Full(event)) => {
                // Queue is full - this is backpressure
                self.metrics.backpressure_count = self.metrics.backpressure_count.saturating_add(1);

                // Block until space is available
                self.sender.send(event)?;
                self.metrics.depth = self.metrics.depth.saturating_add(1);
                self.metrics.max_depth = self.metrics.max_depth.max(self.metrics.depth);
                Ok(())
            }
            Err(mpsc::TrySendError::Disconnected(event)) => Err(mpsc::SendError(event)),
        }
    }

    // Try to send an event to the queue without blocking.
    //
    // Returns:
    // * `Ok(())` - Event was sent successfully
    // * `Err(mpsc::TrySendError::Full(_))` - Queue is full
    // * `Err(mpsc::TrySendError::Disconnected(_))` - Receiver was dropped
    //
    // Example:
    // ```ignore
    // match queue.try_send(event) {
    //     Ok(_) => println!("Sent"),
    //     Err(mpsc::TrySendError::Full(_)) => println!("Queue full"),
    //     Err(_) => println!("Receiver disconnected"),
    // }
    // ```
    pub fn try_send(&mut self, event: T) -> Result<(), mpsc::TrySendError<T>> {
        match self.sender.try_send(event) {
            Ok(()) => {
                self.metrics.depth = self.metrics.depth.saturating_add(1);
                self.metrics.max_depth = self.metrics.max_depth.max(self.metrics.depth);
                Ok(())
            }
            Err(e) => Err(e),
        }
    }

    // Try to receive an event without blocking.
    //
    // Returns:
    // * `Some(event)` - An event was available
    // * `None` - Queue is empty
    //
    // Example:
    // ```ignore
    // while let Some(event) = queue.try_recv() {
    //     process_event(event);
    // }
    // ```
    pub fn try_recv(&mut self) -> Option<T> {
        match self.receiver.try_recv() {
            Ok(event) => {
                self.metrics.depth = self.metrics.depth.saturating_sub(1);
                Some(event)
            }
            Err(_) => None,
        }
    }

    // Receive an event, blocking until one is available.
    //
    // Returns:
    // * `Ok(event)` - An event was received
    // * `Err(mpsc::RecvError)` - Sender was dropped
    //
    // Example:
    // ```ignore
    // let event = queue.recv()?;
    // ```
    pub fn recv(&mut self) -> Result<T, mpsc::RecvError> {
        let event = self.receiver.recv()?;
        self.metrics.depth = self.metrics.depth.saturating_sub(1);
        Ok(event)
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

    #[cfg(test)]
    pub(crate) fn receiver_mut(&mut self) -> &mut mpsc::Receiver<T> {
        &mut self.receiver
    }

    // Clear all events from the queue.
    //
    // This is useful for error recovery when invalid data is encountered.
    pub fn clear(&mut self) {
        super::boundary::clear_via_receiver(&mut self.receiver);
        self.metrics.depth = 0;
    }

    // Reset metrics while preserving queue contents.
    pub fn reset_metrics(&mut self) {
        self.metrics = QueueMetrics {
            depth: self.metrics.depth,
            ..Default::default()
        };
    }
}

#[cfg(test)]
impl<T> Default for BoundedEventQueue<T> {
    fn default() -> Self {
        Self::new()
    }
}
