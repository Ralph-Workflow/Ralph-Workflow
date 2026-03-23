use std::sync::mpsc;
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

use crate::cloud::types::{heartbeat_drop_join_timeout, heartbeat_should_join_thread};
use crate::cloud::CloudReporter;

pub mod io_redaction {
    use std::sync::LazyLock;

    pub static BEARER_TOKEN_RE: LazyLock<regex::Regex> =
        LazyLock::new(|| regex::Regex::new(r"(?i)(bearer\s+)\S+").expect("valid regex"));

    pub static COMMON_QUERY_RE: LazyLock<regex::Regex> = LazyLock::new(|| {
        const KEYS: [&str; 5] = [
            "access_token=",
            "token=",
            "password=",
            "passwd=",
            "oauth_token=",
        ];
        let pattern = format!("(?i)({})([^&\\s]*)", KEYS.join("|"));
        regex::Regex::new(&pattern).expect("valid regex")
    });

    pub static TOKEN_LIKE_RE: LazyLock<regex::Regex> = LazyLock::new(|| {
        const PREFIXES: [&str; 6] = ["ghp_", "github_pat_", "glpat-", "xoxb-", "xapp-", "ya29."];
        let pattern = format!(
            "({})[A-Za-z0-9_\\-\\.]+",
            PREFIXES
                .iter()
                .map(|&s| regex::escape(s))
                .collect::<Vec<_>>()
                .join("|")
        );
        regex::Regex::new(&pattern).expect("valid regex")
    });

    pub fn redact_bearer_tokens(input: &str) -> String {
        BEARER_TOKEN_RE
            .replace_all(input, "$1<redacted>")
            .to_string()
    }

    pub fn redact_common_query_params(input: &str) -> String {
        COMMON_QUERY_RE
            .replace_all(input, |caps: &regex::Captures| {
                let key = caps.get(1).map_or("", |m| m.as_str());
                format!("{}<redacted>", key)
            })
            .to_string()
    }

    pub fn redact_token_like_substrings(input: &str) -> String {
        TOKEN_LIKE_RE.replace_all(input, "<redacted>").to_string()
    }
}

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
