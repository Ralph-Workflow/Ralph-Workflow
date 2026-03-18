//! Runtime boundary for cloud heartbeat background task.
//!
//! This module contains the imperative thread-spawning code that cannot be
//! expressed functionally. The HeartbeatGuard spawns a background thread
//! to periodically send heartbeat signals to the cloud API.

use crate::cloud::types::{heartbeat_drop_join_timeout, heartbeat_should_join_thread};
use crate::cloud::CloudReporter;
use std::sync::mpsc;
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

pub struct HeartbeatGuard {
    stop_tx: Option<mpsc::Sender<()>>,
    done_rx: Option<mpsc::Receiver<()>>,
    handle: Option<JoinHandle<()>>,
}

impl HeartbeatGuard {
    pub fn start(reporter: Arc<dyn CloudReporter>, interval: Duration) -> Self {
        let (stop_tx, stop_rx) = mpsc::channel::<()>();
        let (done_tx, done_rx) = mpsc::channel::<()>();

        let handle = thread::spawn(move || {
            std::iter::successors(Some(interval), |_| Some(interval))
                .filter_map(|timeout| match stop_rx.recv_timeout(timeout) {
                    Err(mpsc::RecvTimeoutError::Timeout) => Some(()),
                    _ => None,
                })
                .for_each(|_| {
                    let _ = reporter.heartbeat();
                });

            let _ = done_tx.send(());
        });

        Self {
            stop_tx: Some(stop_tx),
            done_rx: Some(done_rx),
            handle: Some(handle),
        }
    }
}

impl Drop for HeartbeatGuard {
    fn drop(&mut self) {
        let timeout = heartbeat_drop_join_timeout();

        if let Some(tx) = self.stop_tx.take() {
            let _ = tx.send(());
        }

        if let (Some(rx), Some(h)) = (self.done_rx.take(), self.handle.take()) {
            let done_received = rx.recv_timeout(timeout).is_ok();
            if heartbeat_should_join_thread(done_received) {
                let _ = h.join();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cloud::mock::MockCloudReporter;
    use std::time::Instant;

    #[test]
    fn test_heartbeat_sends_periodic_signals() {
        let reporter = Arc::new(MockCloudReporter::new());
        let reporter_clone = Arc::clone(&reporter);

        let _guard = HeartbeatGuard::start(reporter_clone, Duration::from_millis(25));

        let deadline = Instant::now() + Duration::from_millis(750);
        while reporter.heartbeat_count() < 3 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }

        let count = reporter.heartbeat_count();
        assert!(count >= 3, "Expected at least 3 heartbeats, got {count}");
    }

    #[test]
    fn test_heartbeat_stops_on_drop() {
        let reporter = Arc::new(MockCloudReporter::new());
        let reporter_clone = Arc::clone(&reporter);

        {
            let _guard = HeartbeatGuard::start(reporter_clone, Duration::from_millis(25));
            thread::sleep(Duration::from_millis(100));
        }

        let count_at_drop = reporter.heartbeat_count();
        thread::sleep(Duration::from_millis(100));
        let count_after_drop = reporter.heartbeat_count();

        assert_eq!(
            count_at_drop, count_after_drop,
            "Heartbeats should stop after guard is dropped"
        );
    }

    #[test]
    fn test_drop_does_not_block_for_full_interval() {
        let reporter = Arc::new(MockCloudReporter::new());
        let reporter_clone = Arc::clone(&reporter);

        let start = Instant::now();
        {
            let _guard = HeartbeatGuard::start(reporter_clone, Duration::from_secs(5));
            thread::sleep(Duration::from_millis(50));
        }
        let elapsed = start.elapsed();

        assert!(
            elapsed < Duration::from_millis(500),
            "drop should return promptly; elapsed={elapsed:?}"
        );
    }

    #[test]
    fn test_drop_does_not_block_when_heartbeat_call_is_stalled() {
        use crate::cloud::types::{CloudError, PipelineResult, ProgressUpdate};
        use std::sync::mpsc;

        struct BlockingReporter {
            entered_tx: mpsc::Sender<()>,
        }

        impl CloudReporter for BlockingReporter {
            fn report_progress(&self, _update: &ProgressUpdate) -> Result<(), CloudError> {
                Ok(())
            }

            fn heartbeat(&self) -> Result<(), CloudError> {
                let _ = self.entered_tx.send(());
                thread::sleep(Duration::from_millis(300));
                Ok(())
            }

            fn report_completion(&self, _result: &PipelineResult) -> Result<(), CloudError> {
                Ok(())
            }
        }

        let (tx, rx) = mpsc::channel::<()>();
        let reporter = Arc::new(BlockingReporter { entered_tx: tx });
        let reporter_clone = Arc::clone(&reporter);

        let start = Instant::now();
        {
            let _guard = HeartbeatGuard::start(reporter_clone, Duration::from_millis(1));
            let _ = rx.recv_timeout(Duration::from_millis(250));
        }
        let elapsed = start.elapsed();

        assert!(
            elapsed < Duration::from_millis(150),
            "drop should not block on stalled heartbeat; elapsed={elapsed:?}"
        );
    }
}
