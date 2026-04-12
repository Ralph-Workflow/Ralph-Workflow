// Configuration and shared definitions for bounded event queue

#[cfg(test)]
use std::env;
#[cfg(test)]
use std::sync::mpsc;

/// Default queue size when `RALPH_STREAMING_QUEUE_SIZE` is not set.
#[cfg(test)]
pub(crate) const DEFAULT_QUEUE_SIZE: usize = 100;

/// Minimum supported queue size.
#[cfg(test)]
pub(crate) const MIN_QUEUE_SIZE: usize = 10;

/// Maximum supported queue size.
#[cfg(test)]
pub(crate) const MAX_QUEUE_SIZE: usize = 1000;

/// Queue configuration.
#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct QueueConfig {
    pub capacity: usize,
}

#[cfg(test)]
fn get_queue_config() -> QueueConfig {
    let capacity = env::var("RALPH_STREAMING_QUEUE_SIZE")
        .ok()
        .and_then(|raw| raw.trim().parse::<usize>().ok())
        .unwrap_or(DEFAULT_QUEUE_SIZE)
        .clamp(MIN_QUEUE_SIZE, MAX_QUEUE_SIZE);

    QueueConfig { capacity }
}
