//! Idle-timeout monitor thread.
//!
//! Split into focused modules:
//! - `base`: MonitorConfig, MonitorResult, MonitorParams, MonitorLoopState
//! - `sleep`: sleep utilities
//! - `core`: enforcement and policy logic

pub mod base;
pub mod core;
pub mod sleep;

// Re-export public types from submodules for convenient access
pub use base::FileActivityConfig;
pub use base::MonitorConfig;
pub use base::MonitorResult;

use crate::executor::AgentChild;
use crate::pipeline::idle_timeout::SharedActivityTimestamp;

/// Default check interval for the idle monitor (30 seconds).
const DEFAULT_CHECK_INTERVAL: std::time::Duration = std::time::Duration::from_secs(30);

/// Monitors activity and kills a process if idle timeout is exceeded.
pub fn monitor_idle_timeout(
    activity_timestamp: &SharedActivityTimestamp,
    child: &std::sync::Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    timeout: std::time::Duration,
    should_stop: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    executor: &std::sync::Arc<dyn crate::executor::ProcessExecutor>,
) -> MonitorResult {
    monitor_idle_timeout_with_interval_and_kill_config(
        activity_timestamp,
        None,
        child,
        should_stop,
        executor,
        MonitorConfig {
            timeout,
            check_interval: DEFAULT_CHECK_INTERVAL,
            kill_config: crate::pipeline::idle_timeout::io::DEFAULT_KILL_CONFIG,
            ..Default::default()
        },
    )
}

/// Like [`monitor_idle_timeout`] but with a configurable check interval.
pub fn monitor_idle_timeout_with_interval(
    activity_timestamp: &SharedActivityTimestamp,
    child: &std::sync::Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    timeout: std::time::Duration,
    should_stop: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    executor: &std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    check_interval: std::time::Duration,
) -> MonitorResult {
    monitor_idle_timeout_with_interval_and_kill_config(
        activity_timestamp,
        None,
        child,
        should_stop,
        executor,
        MonitorConfig {
            timeout,
            check_interval,
            kill_config: crate::pipeline::idle_timeout::io::DEFAULT_KILL_CONFIG,
            ..Default::default()
        },
    )
}

pub fn monitor_idle_timeout_with_interval_and_kill_config(
    activity_timestamp: &SharedActivityTimestamp,
    file_activity_config: Option<&FileActivityConfig>,
    child: &std::sync::Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    should_stop: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    executor: &std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    config: MonitorConfig,
) -> MonitorResult {
    monitor_idle_timeout_with_interval_and_kill_config_and_observer(
        activity_timestamp,
        file_activity_config,
        child,
        should_stop,
        executor,
        config,
        None,
    )
}

pub fn monitor_idle_timeout_with_interval_and_kill_config_and_observer(
    activity_timestamp: &SharedActivityTimestamp,
    file_activity_config: Option<&FileActivityConfig>,
    child: &std::sync::Arc<std::sync::Mutex<Box<dyn AgentChild>>>,
    should_stop: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    executor: &std::sync::Arc<dyn crate::executor::ProcessExecutor>,
    config: MonitorConfig,
    child_activity_suppressed: Option<
        &std::sync::Arc<std::sync::Mutex<Option<crate::executor::ChildProcessInfo>>>,
    >,
) -> MonitorResult {
    let params = base::MonitorParams {
        activity_timestamp,
        file_activity_config,
        child,
        should_stop,
        executor,
        child_activity_suppressed,
        timeout: config.timeout,
        check_interval: config.check_interval,
        kill_config: config.kill_config,
        required_idle_confirmations: config.required_idle_confirmations,
        check_child_processes: config.check_child_processes,
        completion_check: config.completion_check,
        partial_completion_check: config.partial_completion_check,
        tool_activity_check: config.tool_activity_check,
    };
    run_monitor_loop(&params)
}

fn run_monitor_loop(params: &base::MonitorParams<'_>) -> MonitorResult {
    let mut s = base::MonitorLoopState::new();
    loop {
        if let base::MonitorLoopAction::Return(r) = core::handle_enforcement_tick(params, &mut s) {
            return r;
        }
    }
}
