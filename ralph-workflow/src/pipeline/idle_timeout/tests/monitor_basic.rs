use super::super::*;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

#[test]
fn monitor_result_variants_are_distinct() {
    assert_ne!(
        MonitorResult::ProcessCompleted,
        MonitorResult::TimedOut { escalated: false }
    );
    assert_ne!(
        MonitorResult::ProcessCompleted,
        MonitorResult::TimedOut { escalated: true }
    );
    assert_ne!(
        MonitorResult::TimedOut { escalated: false },
        MonitorResult::TimedOut { escalated: true }
    );
}

#[test]
fn monitor_stops_when_signaled() {
    use crate::executor::MockAgentChild;

    let timestamp = new_activity_timestamp();
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let mock_child = MockAgentChild::new(0);
    let child = Arc::new(Mutex::new(
        Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
    ));

    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(crate::executor::MockProcessExecutor::new());

    let check_interval = Duration::from_millis(10);
    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval(
            &timestamp,
            &child,
            Duration::from_secs(60),
            &should_stop_clone,
            &executor,
            check_interval,
        )
    });

    thread::sleep(Duration::from_millis(50));
    should_stop.store(true, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");
    assert_eq!(result, MonitorResult::ProcessCompleted);
}

#[test]
fn monitor_stops_promptly_even_with_long_check_interval() {
    use crate::executor::MockAgentChild;

    let timestamp = new_activity_timestamp();
    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(
        Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
    ));

    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(crate::executor::MockProcessExecutor::new());

    let check_interval = Duration::from_secs(1);
    let start = std::time::Instant::now();
    let handle = thread::spawn(move || {
        monitor_idle_timeout_with_interval(
            &timestamp,
            &child,
            Duration::from_secs(60),
            &should_stop_clone,
            &executor,
            check_interval,
        )
    });

    thread::sleep(Duration::from_millis(20));
    should_stop.store(true, Ordering::Release);
    controller.store(false, Ordering::Release);

    let result = handle.join().expect("Monitor thread panicked");
    assert_eq!(result, MonitorResult::ProcessCompleted);
    assert!(
        start.elapsed() < Duration::from_millis(300),
        "monitor should stop promptly after stop signal"
    );
}

#[test]
#[cfg(unix)]
fn kill_process_returns_failed_when_sigterm_command_exits_nonzero() {
    use std::io;
    use std::path::Path;
    use std::process::ExitStatus;

    #[cfg(unix)]
    use std::os::unix::process::ExitStatusExt;

    #[derive(Debug)]
    struct NonZeroKillExecutor;

    impl crate::executor::ProcessExecutor for NonZeroKillExecutor {
        fn execute(
            &self,
            _command: &str,
            _args: &[&str],
            _env: &[(String, String)],
            _workdir: Option<&Path>,
        ) -> io::Result<crate::executor::ProcessOutput> {
            Ok(crate::executor::ProcessOutput {
                status: ExitStatus::from_raw(1),
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }

    let executor = NonZeroKillExecutor;
    let result = super::super::kill::kill_process(12345, &executor, None, DEFAULT_KILL_CONFIG);
    assert_eq!(result, super::super::kill::KillResult::Failed);
}

#[test]
#[cfg(unix)]
fn kill_process_uses_double_dash_before_negative_pgid() {
    let executor = crate::executor::MockProcessExecutor::new();

    let _ = super::super::kill::kill_process(12345, &executor, None, DEFAULT_KILL_CONFIG);

    let calls = executor.execute_calls_for("kill");
    assert!(!calls.is_empty(), "expected at least one kill invocation");
    assert_eq!(calls[0].1, vec!["-TERM", "--", "-12345"]);
}

#[test]
#[cfg(unix)]
fn force_kill_best_effort_uses_double_dash_before_negative_pgid() {
    let executor = crate::executor::MockProcessExecutor::new();

    let ok = super::super::kill::force_kill_best_effort(12345, &executor);
    assert!(ok);

    let calls = executor.execute_calls_for("kill");
    assert!(!calls.is_empty(), "expected at least one kill invocation");
    assert_eq!(calls[0].1, vec!["-KILL", "--", "-12345"]);
}
