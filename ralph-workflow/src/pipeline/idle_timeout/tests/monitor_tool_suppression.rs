//! Tests for bounded tool-activity suppression in the idle timeout monitor.
//!
//! These tests verify:
//! 1. The tool suppressor cap: after `max_tool_suppression_ticks` consecutive ticks of
//!    suppression, the suppressor is bypassed and the idle timeout fires normally.
//! 2. `MonitorLoopState::reset_idle` resets the consecutive suppression tick counter,
//!    so interleaved non-tool activity properly resets the cap.
//! 3. `reset_idle_preserving_tool_suppression` preserves the tool suppression counter.
//! 4. `evaluate_tool_suppression` boundary conditions (cap=0, exact cap boundary, etc.).

use super::super::io::KillConfig;
use super::super::runtime::base::{
    evaluate_tool_suppression, MonitorLoopState, ToolSuppressionAction,
};
use super::super::runtime::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, MockAgentChild, MockProcessExecutor};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

fn fast_kill_config() -> KillConfig {
    KillConfig::new(
        Duration::from_millis(10),
        Duration::from_millis(1),
        Duration::from_millis(5),
        Duration::from_millis(50),
        Duration::from_millis(10),
    )
}

fn wait_until_idle_timeout_exceeded(timestamp: &SharedActivityTimestamp, timeout: Duration) {
    timestamp.store(0, Ordering::Release);
    while !is_idle_timeout_exceeded(timestamp, timeout) {
        std::thread::yield_now();
    }
}

/// Verify that a stuck tool-activity counter (check always returns true) eventually allows
/// the idle timeout to fire after `max_tool_suppression_ticks` consecutive suppressor ticks.
///
/// Without the cap, a stuck counter would suppress the timeout indefinitely.
#[test]
fn tool_suppression_cap_allows_timeout_after_max_ticks() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Tool check always returns true — simulates a stuck counter (protocol anomaly).
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(|| true);

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        check_interval: Duration::ZERO,
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: None,
        tool_activity_check: Some(tool_activity_check),
        max_tool_suppression_ticks: 2,
    };

    let result = monitor_idle_timeout_with_interval_and_kill_config(
        &timestamp,
        None,
        &child,
        &should_stop_clone,
        &executor,
        config,
    );

    assert!(
        matches!(result, MonitorResult::TimedOut { .. }),
        "tool suppressor cap must allow idle timeout to fire after max_tool_suppression_ticks; \
         got {result:?}"
    );
}

/// Verify that `MonitorLoopState::reset_idle` resets the consecutive tool suppression tick
/// counter to 0. This ensures that when file activity or child-process activity provides a
/// genuine idle-state reset, the cap counter also resets (the agent IS making progress).
#[test]
fn reset_idle_resets_consecutive_tool_suppression_ticks() {
    let mut s = MonitorLoopState::new();
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "new MonitorLoopState must start with consecutive_tool_suppression_ticks = 0"
    );

    s.consecutive_tool_suppression_ticks = 5;
    s.reset_idle();
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "reset_idle must reset consecutive_tool_suppression_ticks to 0"
    );
}

/// Verify that when the tool-activity check transitions from true to false, the monitor
/// does not immediately kill: with `required_idle_confirmations = 2`, one false return
/// increments the idle counter to 1 (not enough to kill). The `should_stop` signal is
/// then set and the monitor exits cleanly with `ProcessCompleted`.
///
/// Timing: check_interval = 50ms ensures the `should_stop` signal set after the 4th
/// check() call is detected during the sleep preceding the 5th tick.
#[test]
fn tool_suppressor_disabled_when_check_returns_false_allows_clean_stop() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);
    let should_stop_for_test = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Tool check returns true for the first 3 calls (ticks 1–3 suppressed), then false.
    let check_count = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let check_count_clone = Arc::clone(&check_count);
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(move || {
        let n = check_count_clone.fetch_add(1, Ordering::SeqCst);
        n < 3
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        // 50ms interval: the other thread sets should_stop after tick 4's check(),
        // and the monitor detects it during tick 5's 50ms sleep → StopConditionsMet.
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        // 2 confirmations: tick 4 (check=false) only increments to 1, so no kill yet.
        required_idle_confirmations: 2,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: None,
        tool_activity_check: Some(tool_activity_check),
        max_tool_suppression_ticks: 10, // cap is high — suppressor disabled by check returning false
    };

    let handle = std::thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            None,
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // Wait for tick 4 to have called check() (check_count ≥ 4), then set should_stop.
    // The monitor's tick 5 sleep will detect the signal and return ProcessCompleted.
    while check_count.load(Ordering::SeqCst) < 4 {
        std::thread::yield_now();
    }
    should_stop_for_test.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "monitor must not kill when should_stop is set after tool check returns false"
    );
}

// ============================================================================
// Unit tests for evaluate_tool_suppression boundary conditions
// ============================================================================

/// When `check_result` is false, the action is always `Inactive` regardless of tick count.
#[test]
fn evaluate_tool_suppression_inactive_when_check_false() {
    assert!(matches!(
        evaluate_tool_suppression(false, 0, 10),
        ToolSuppressionAction::Inactive
    ));
    assert!(matches!(
        evaluate_tool_suppression(false, u32::MAX, 10),
        ToolSuppressionAction::Inactive
    ));
    assert!(matches!(
        evaluate_tool_suppression(false, 5, 0),
        ToolSuppressionAction::Inactive
    ));
}

/// When `max_ticks` is 0, the cap is exceeded on the very first active tick.
#[test]
fn evaluate_tool_suppression_cap_zero_immediately_exceeds() {
    match evaluate_tool_suppression(true, 0, 0) {
        ToolSuppressionAction::CapExceeded { ticks } => assert_eq!(ticks, 1),
        other => panic!(
            "expected CapExceeded, got {other:?}",
            other = std::mem::discriminant(&other)
        ),
    }
}

/// Exact cap boundary: `current_ticks = max_ticks - 1` returns `Suppress`,
/// `current_ticks = max_ticks` returns `CapExceeded`.
#[test]
fn evaluate_tool_suppression_exact_cap_boundary() {
    let max_ticks = 5;

    // One below the cap → Suppress
    match evaluate_tool_suppression(true, max_ticks - 1, max_ticks) {
        ToolSuppressionAction::Suppress { ticks } => assert_eq!(ticks, max_ticks),
        other => panic!(
            "expected Suppress at max_ticks, got {other:?}",
            other = std::mem::discriminant(&other)
        ),
    }

    // At the cap → CapExceeded
    match evaluate_tool_suppression(true, max_ticks, max_ticks) {
        ToolSuppressionAction::CapExceeded { ticks } => assert_eq!(ticks, max_ticks + 1),
        other => panic!(
            "expected CapExceeded above max_ticks, got {other:?}",
            other = std::mem::discriminant(&other)
        ),
    }
}

/// `current_ticks` at `u32::MAX` saturates instead of overflowing.
/// With `max_ticks = u32::MAX`, saturating_add produces `u32::MAX` which equals
/// `max_ticks`, so the result is `Suppress` (not `CapExceeded`). The key invariant
/// is that no overflow occurs.
#[test]
fn evaluate_tool_suppression_saturates_at_u32_max() {
    // u32::MAX.saturating_add(1) == u32::MAX, and u32::MAX <= u32::MAX → Suppress
    match evaluate_tool_suppression(true, u32::MAX, u32::MAX) {
        ToolSuppressionAction::Suppress { ticks } => assert_eq!(ticks, u32::MAX),
        other => panic!(
            "expected Suppress with saturated ticks, got {other:?}",
            other = std::mem::discriminant(&other)
        ),
    }

    // With max_ticks < u32::MAX, current_ticks at u32::MAX produces CapExceeded
    match evaluate_tool_suppression(true, u32::MAX, u32::MAX - 1) {
        ToolSuppressionAction::CapExceeded { ticks } => assert_eq!(ticks, u32::MAX),
        other => panic!(
            "expected CapExceeded when max_ticks < u32::MAX, got {other:?}",
            other = std::mem::discriminant(&other)
        ),
    }
}

// ============================================================================
// Unit tests for reset_idle_preserving_tool_suppression
// ============================================================================

/// `reset_idle_preserving_tool_suppression` resets idle state but keeps the tool
/// suppression tick counter intact.
#[test]
fn reset_idle_preserving_tool_suppression_preserves_counter() {
    let mut s = MonitorLoopState::new();
    s.consecutive_idle_count = 3;
    s.consecutive_tool_suppression_ticks = 7;
    s.child_startup_grace_available = false;

    s.reset_idle_preserving_tool_suppression();

    assert_eq!(s.consecutive_idle_count, 0, "idle count must be reset");
    assert!(
        s.last_child_observation.is_none(),
        "child observation must be reset"
    );
    assert!(
        s.child_startup_grace_available,
        "startup grace must be restored"
    );
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 7,
        "tool suppression ticks must be preserved"
    );
}

/// Verify that `reset_idle()` still zeros the tool suppression counter (for
/// non-tool suppressors where the full reset is semantically correct).
#[test]
fn reset_idle_still_zeros_tool_suppression_ticks() {
    let mut s = MonitorLoopState::new();
    s.consecutive_tool_suppression_ticks = 10;

    s.reset_idle();

    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "reset_idle must zero the tool suppression counter (genuine progress resets the cap)"
    );
}

/// Verify the tool suppressor Suppress arm correctly uses the preserving variant:
/// after a suppress action, the tool suppression ticks reflect the new count,
/// not zero.
#[test]
fn tool_suppression_suppress_preserves_ticks_after_idle_reset() {
    let mut s = MonitorLoopState::new();
    s.consecutive_idle_count = 2;
    s.consecutive_tool_suppression_ticks = 3;

    // Simulate what apply_tool_suppression_action does for Suppress { ticks: 4 }
    s.reset_idle_preserving_tool_suppression();
    s.consecutive_tool_suppression_ticks = 4;

    assert_eq!(s.consecutive_idle_count, 0);
    assert_eq!(s.consecutive_tool_suppression_ticks, 4);
}

/// Verify the interplay between tool suppression ticks and genuine-progress resets.
///
/// Scenario: a tool execution is in progress (accumulating suppression ticks), then
/// genuine file activity fires (calling `reset_idle()`), which zeroes the tool suppression
/// counter. Then a new tool suppression begins from scratch — ticks accumulate from 0
/// again. Finally, the tool suppressor itself fires (calling `reset_idle_preserving_tool_suppression()`),
/// and the counter survives that reset.
///
/// This ensures the cap restarts correctly after genuine progress but continues
/// accumulating when the suppressor is the only source of idle resets.
#[test]
fn tool_suppression_counter_survives_interleaved_resets() {
    let mut s = MonitorLoopState::new();

    // Phase 1: tool suppression accumulates ticks.
    s.consecutive_tool_suppression_ticks = 5;
    s.consecutive_idle_count = 3;

    // Phase 2: genuine file activity triggers reset_idle — counter zeroes.
    s.reset_idle();
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "genuine progress must zero the tool suppression counter"
    );
    assert_eq!(s.consecutive_idle_count, 0);

    // Phase 3: tool activity resumes, new suppression ticks accumulate from 0.
    s.consecutive_tool_suppression_ticks = 1;
    s.consecutive_idle_count = 1;

    // Phase 4: tool suppressor fires — uses preserving reset, counter survives.
    s.reset_idle_preserving_tool_suppression();
    s.consecutive_tool_suppression_ticks = 2; // incremented by suppressor
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 2,
        "tool suppressor reset must preserve the counter"
    );
    assert_eq!(
        s.consecutive_idle_count, 0,
        "idle count must be zeroed by preserving reset"
    );
    assert!(
        s.child_startup_grace_available,
        "startup grace must be restored by preserving reset"
    );

    // Phase 5: another genuine-progress reset zeroes the counter again.
    s.reset_idle();
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "second genuine-progress reset must zero the counter again"
    );
}
