use super::super::runtime::MonitorConfig;
use super::super::*;

#[test]
#[cfg(unix)]
fn monitor_does_not_skip_timeout_enforcement_when_try_wait_errors_before_kill() {
    use std::io;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    #[derive(Debug)]
    struct AlwaysErrorsChild;

    impl crate::executor::AgentChild for AlwaysErrorsChild {
        fn id(&self) -> u32 {
            12345
        }

        fn wait(&mut self) -> io::Result<std::process::ExitStatus> {
            Err(io::Error::other("wait should not be called"))
        }

        fn try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>> {
            Err(io::Error::other("simulated transient try_wait failure"))
        }
    }

    let child = Arc::new(Mutex::new(
        Box::new(AlwaysErrorsChild) as Box<dyn crate::executor::AgentChild>
    ));
    let timestamp = new_activity_timestamp();
    timestamp.store(0, Ordering::Release);
    let should_stop = Arc::new(AtomicBool::new(false));
    let executor: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::new(crate::executor::MockProcessExecutor::new());

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None, // No file activity config
        &child,
        &should_stop,
        &executor,
        MonitorConfig {
            timeout: Duration::ZERO,
            check_interval: Duration::from_millis(1),
            kill_config: KillConfig::new(
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(1),
                Duration::from_millis(20),
                Duration::from_millis(5),
            ),
            required_idle_confirmations: 2,
            check_child_processes: true,
            completion_check: None,

            partial_completion_check: None,
            tool_activity_check: None,
        },
    );

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "expected timeout enforcement even if try_wait errors; got {result:?}"
    );
}
