use super::super::io::kill_process;
use super::super::*;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Barrier, Mutex};
use std::thread;
use std::time::Duration;

fn config(
    sigterm_grace: Duration,
    poll_interval: Duration,
    sigkill_confirm_timeout: Duration,
    post_sigkill_hard_cap: Duration,
    sigkill_resend_interval: Duration,
) -> KillConfig {
    KillConfig::new(
        sigterm_grace,
        poll_interval,
        sigkill_confirm_timeout,
        post_sigkill_hard_cap,
        sigkill_resend_interval,
    )
}

#[test]
fn monitor_releases_child_lock_between_sigterm_checks_so_caller_can_access_child() {
    use crate::executor::MockAgentChild;

    #[derive(Debug)]
    struct CountingChild {
        inner: MockAgentChild,
        try_wait_calls: Arc<std::sync::atomic::AtomicUsize>,
        first_try_wait_gate: Arc<Barrier>,
        entered_first_try_wait: mpsc::Sender<()>,
    }

    impl crate::executor::AgentChild for CountingChild {
        fn id(&self) -> u32 {
            self.inner.id()
        }

        fn wait(&mut self) -> std::io::Result<std::process::ExitStatus> {
            self.inner.wait()
        }

        fn try_wait(&mut self) -> std::io::Result<Option<std::process::ExitStatus>> {
            self.try_wait_calls.fetch_add(1, Ordering::SeqCst);
            if self.try_wait_calls.load(Ordering::SeqCst) == 1 {
                let _ = self.entered_first_try_wait.send(());
                self.first_try_wait_gate.wait();
            }
            self.inner.try_wait()
        }
    }

    let (mock_child, running_controller) = MockAgentChild::new_running(0);
    let try_wait_calls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let (entered_tx, entered_rx) = mpsc::channel();
    let gate = Arc::new(Barrier::new(2));

    let child = Arc::new(Mutex::new(Box::new(CountingChild {
        inner: mock_child,
        try_wait_calls: Arc::clone(&try_wait_calls),
        first_try_wait_gate: Arc::clone(&gate),
        entered_first_try_wait: entered_tx,
    }) as Box<dyn crate::executor::AgentChild>));

    let timestamp = new_activity_timestamp();
    timestamp.store(0, Ordering::Release);

    let should_stop = Arc::new(AtomicBool::new(false));
    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(crate::executor::MockProcessExecutor::new());

    let monitor = thread::spawn({
        let child_for_monitor = Arc::clone(&child);
        let timestamp_for_monitor = timestamp;
        let should_stop_for_monitor = Arc::clone(&should_stop);
        move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp_for_monitor,
                None,
                &child_for_monitor,
                &should_stop_for_monitor,
                &executor,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(1),
                    kill_config: config(
                        Duration::from_secs(2),
                        Duration::from_millis(500),
                        Duration::from_millis(50),
                        Duration::from_secs(5),
                        Duration::from_secs(1),
                    ),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                },
            )
        }
    });

    entered_rx
        .recv_timeout(Duration::from_secs(5))
        .expect("expected monitor to call try_wait");

    assert!(
        child.try_lock().is_err(),
        "expected child mutex to be held during try_wait"
    );

    gate.wait();

    let acquired_after_gate = {
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        let mut acquired = false;
        while std::time::Instant::now() < deadline {
            if let Ok(_guard) = child.try_lock() {
                acquired = true;
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        acquired
    };

    should_stop.store(true, Ordering::Release);
    running_controller.store(false, Ordering::Release);
    let _ = monitor.join();

    assert!(
        acquired_after_gate,
        "expected to acquire child lock while monitor sleeps"
    );
    assert!(try_wait_calls.load(Ordering::Acquire) >= 1);
}

#[test]
#[cfg(unix)]
fn monitor_reports_timeout_even_if_sigkill_confirmation_times_out() {
    use crate::executor::MockAgentChild;

    let (mock_child, running_controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(
        Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
    ));

    let timestamp = new_activity_timestamp();
    timestamp.store(0, Ordering::Release);
    let should_stop = Arc::new(AtomicBool::new(false));

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_dyn: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let monitor_handle = thread::spawn({
        let child_for_monitor = Arc::clone(&child);
        let timestamp_for_monitor = timestamp;
        let should_stop_for_monitor = Arc::clone(&should_stop);
        move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp_for_monitor,
                None,
                &child_for_monitor,
                &should_stop_for_monitor,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(1),
                    kill_config: config(
                        Duration::from_millis(10),
                        Duration::from_millis(1),
                        Duration::from_millis(1),
                        Duration::from_secs(2),
                        Duration::from_millis(20),
                    ),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                },
            )
        }
    });

    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        let calls = executor.execute_calls_for("kill");
        if calls
            .iter()
            .any(|(_, args, _, _)| args.iter().any(|a| a == "-KILL"))
        {
            thread::sleep(Duration::from_millis(5));
            running_controller.store(false, Ordering::Release);
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }

    let result = monitor_handle.join().expect("Monitor thread panicked");
    assert!(matches!(
        result,
        MonitorResult::TimedOut {
            escalated: true,
            ..
        }
    ));
    assert!(!running_controller.load(Ordering::Acquire));
}

#[test]
#[cfg(unix)]
fn monitor_treats_try_wait_errors_as_status_unknown_and_continues_enforcement() {
    use std::io;

    #[derive(Debug)]
    struct TryWaitErrorsChild {
        first: bool,
    }

    impl crate::executor::AgentChild for TryWaitErrorsChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<std::process::ExitStatus> {
            Err(io::Error::other("wait should not be called in this test"))
        }

        fn try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>> {
            if self.first {
                self.first = false;
                return Ok(None);
            }
            Err(io::Error::other(
                "simulated already-reaped / status unavailable",
            ))
        }
    }

    let child = Arc::new(Mutex::new(
        Box::new(TryWaitErrorsChild { first: true }) as Box<dyn crate::executor::AgentChild>
    ));

    let timestamp = new_activity_timestamp();
    timestamp.store(0, Ordering::Release);
    let should_stop = Arc::new(AtomicBool::new(false));
    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(crate::executor::MockProcessExecutor::new());

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop,
        &executor,
        MonitorConfig {
            timeout: Duration::ZERO,
            check_interval: Duration::from_millis(1),
            kill_config: config(
                Duration::from_millis(10),
                Duration::from_millis(1),
                Duration::from_millis(10),
                Duration::from_millis(50),
                Duration::from_millis(20),
            ),
            required_idle_confirmations: 2,
            check_child_processes: true,
        },
    );

    assert!(matches!(
        result,
        MonitorResult::TimedOut {
            escalated: true,
            ..
        }
    ));
}

#[test]
#[cfg(unix)]
fn monitor_escalates_to_sigkill_when_sigterm_ignored() {
    use crate::executor::MockAgentChild;

    let (mock_child, running_controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(
        Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
    ));

    let timestamp = new_activity_timestamp();
    let should_stop = Arc::new(AtomicBool::new(false));

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_dyn: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    timestamp.store(0, Ordering::Release);

    let monitor_handle = thread::spawn({
        let child_clone = Arc::clone(&child);
        let timestamp_clone = timestamp;
        let should_stop_clone = Arc::clone(&should_stop);
        move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp_clone,
                None,
                &child_clone,
                &should_stop_clone,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(1),
                    kill_config: config(
                        Duration::from_millis(20),
                        Duration::from_millis(1),
                        Duration::from_millis(50),
                        Duration::from_secs(2),
                        Duration::from_millis(20),
                    ),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                },
            )
        }
    });

    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        let calls = executor.execute_calls_for("kill");
        if calls
            .iter()
            .any(|(_, args, _, _)| args.iter().any(|a| a == "-KILL"))
        {
            running_controller.store(false, Ordering::Release);
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }

    let result = monitor_handle.join().expect("Monitor thread panicked");
    assert!(matches!(
        result,
        MonitorResult::TimedOut {
            escalated: true,
            ..
        }
    ));
    assert!(!running_controller.load(Ordering::Acquire));
}

#[test]
#[cfg(unix)]
fn monitor_succeeds_with_sigterm_when_process_terminates() {
    use crate::executor::MockAgentChild;

    let (mock_child, running_controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(
        Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
    ));

    let timestamp = new_activity_timestamp();
    let should_stop = Arc::new(AtomicBool::new(false));

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_dyn: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    timestamp.store(0, Ordering::Release);

    let monitor_handle = thread::spawn({
        let child_clone = Arc::clone(&child);
        let timestamp_clone = timestamp;
        let should_stop_clone = Arc::clone(&should_stop);
        move || {
            monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp_clone,
                None,
                &child_clone,
                &should_stop_clone,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(1),
                    kill_config: config(
                        Duration::from_millis(200),
                        Duration::from_millis(1),
                        Duration::from_millis(50),
                        Duration::from_secs(2),
                        Duration::from_millis(20),
                    ),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                },
            )
        }
    });

    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        let calls = executor.execute_calls_for("kill");
        if calls
            .iter()
            .any(|(_, args, _, _)| args.iter().any(|a| a == "-TERM"))
        {
            running_controller.store(false, Ordering::Release);
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }

    let result = monitor_handle.join().expect("Monitor thread panicked");
    assert!(matches!(
        result,
        MonitorResult::TimedOut {
            escalated: false,
            ..
        }
    ));
}

#[test]
#[cfg(unix)]
fn monitor_reports_timeout_even_if_process_still_alive_after_force_kill_hard_cap() {
    use crate::executor::MockAgentChild;

    let (mock_child, controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(
        Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
    ));

    let timestamp = new_activity_timestamp();
    timestamp.store(0, Ordering::Release);

    let should_stop = Arc::new(AtomicBool::new(false));
    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_dyn: Arc<dyn crate::executor::ProcessExecutor> = executor.clone();

    let (tx, rx) = mpsc::channel();
    let monitor_handle = thread::spawn({
        let child_for_monitor = Arc::clone(&child);
        let should_stop_for_monitor = Arc::clone(&should_stop);
        move || {
            let result = monitor_idle_timeout_with_interval_and_kill_config(
                &timestamp,
                None,
                &child_for_monitor,
                &should_stop_for_monitor,
                &executor_dyn,
                MonitorConfig {
                    timeout: Duration::ZERO,
                    check_interval: Duration::from_millis(1),
                    kill_config: config(
                        Duration::from_millis(1),
                        Duration::from_millis(1),
                        Duration::from_millis(5),
                        Duration::from_millis(50),
                        Duration::from_millis(10),
                    ),
                    required_idle_confirmations: 2,
                    check_child_processes: true,
                },
            );
            let _ = tx.send(result);
        }
    });

    let result = rx
        .recv_timeout(Duration::from_secs(2))
        .expect("expected monitor to return within bounded time");
    assert!(matches!(
        result,
        MonitorResult::TimedOut {
            escalated: true,
            ..
        }
    ));

    assert!(
        controller.load(Ordering::Acquire),
        "expected process to still be running"
    );

    let kill_calls_before = executor
        .execute_calls_for("kill")
        .iter()
        .filter(|(_, args, _, _)| args.iter().any(|a| a == "-KILL"))
        .count();

    let deadline = std::time::Instant::now() + Duration::from_millis(200);
    let mut kill_calls_after = kill_calls_before;
    while std::time::Instant::now() < deadline && kill_calls_after == kill_calls_before {
        thread::sleep(Duration::from_millis(2));
        kill_calls_after = executor
            .execute_calls_for("kill")
            .iter()
            .filter(|(_, args, _, _)| args.iter().any(|a| a == "-KILL"))
            .count();
    }
    assert!(
        kill_calls_after > kill_calls_before,
        "expected background reaper to continue sending SIGKILL"
    );

    thread::sleep(Duration::from_millis(60));
    let kill_calls_after_reaper_window = executor
        .execute_calls_for("kill")
        .iter()
        .filter(|(_, args, _, _)| args.iter().any(|a| a == "-KILL"))
        .count();
    thread::sleep(Duration::from_millis(40));
    let kill_calls_final = executor
        .execute_calls_for("kill")
        .iter()
        .filter(|(_, args, _, _)| args.iter().any(|a| a == "-KILL"))
        .count();
    assert_eq!(
        kill_calls_final, kill_calls_after_reaper_window,
        "expected bounded reaper to stop sending SIGKILL after its time limit"
    );

    controller.store(false, Ordering::Release);
    let _ = monitor_handle.join();
}

#[test]
#[cfg(unix)]
fn kill_sends_signal_to_process_group_not_just_process() {
    use crate::executor::MockAgentChild;

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(
        Box::new(mock_child) as Box<dyn crate::executor::AgentChild>
    ));

    let executor = crate::executor::MockProcessExecutor::new();
    let pid = 12345;

    let _ = kill_process(
        pid,
        &executor,
        Some(&child),
        config(
            Duration::from_millis(0),
            Duration::from_millis(1),
            Duration::from_millis(0),
            Duration::from_millis(0),
            Duration::from_millis(1),
        ),
    );

    let calls = executor.execute_calls_for("kill");
    assert!(
        calls.iter().any(|(_, args, _, _)| {
            args.iter().any(|a| a == "-TERM") && args.iter().any(|a| a == "-12345")
        }),
        "expected SIGTERM to be sent to process group (-PID)"
    );
}
