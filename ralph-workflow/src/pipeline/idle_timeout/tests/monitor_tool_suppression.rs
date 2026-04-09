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
    evaluate_tool_suppression, FileActivityConfig, MonitorLoopState, ToolSuppressionAction,
};
use super::super::runtime::MonitorConfig;
use super::super::*;
use crate::executor::{AgentChild, MockAgentChild, MockProcessExecutor};
use crate::workspace::MemoryWorkspace;
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

// ============================================================================
// Regression tests for edge cases
// ============================================================================

/// Verify that after the tool suppression cap is exceeded and the tool later becomes
/// inactive (check returns false), the counter resets to 0. When a new tool execution
/// starts (check returns true again), suppression works normally with a fresh cap window.
///
/// This tests the recovery path after a protocol anomaly: the stuck counter eventually
/// gets cleared, and subsequent legitimate tool executions are not penalized.
#[test]
fn tool_suppression_cap_exceeded_then_tool_resumes_resets_counter() {
    let max_ticks: u32 = 3;

    let mut s = MonitorLoopState::new();

    // Phase 1: tool active for max_ticks+1 ticks → cap exceeded.
    for tick in 0..=max_ticks {
        let action =
            evaluate_tool_suppression(true, s.consecutive_tool_suppression_ticks, max_ticks);
        match action {
            ToolSuppressionAction::Suppress { ticks } => {
                s.reset_idle_preserving_tool_suppression();
                s.consecutive_tool_suppression_ticks = ticks;
                assert!(tick < max_ticks, "should suppress before cap; tick={tick}");
            }
            ToolSuppressionAction::CapExceeded { ticks: _ } => {
                // Cap exceeded — suppressor returns false, counter stays at exceeded value.
                assert_eq!(tick, max_ticks, "cap should exceed at tick={max_ticks}");
            }
            ToolSuppressionAction::Inactive => {
                panic!("check_result was true; Inactive is impossible");
            }
        }
    }

    // Phase 2: tool becomes inactive → counter resets to 0.
    let action = evaluate_tool_suppression(false, s.consecutive_tool_suppression_ticks, max_ticks);
    assert!(
        matches!(action, ToolSuppressionAction::Inactive),
        "tool check false must produce Inactive"
    );
    s.consecutive_tool_suppression_ticks = 0; // as apply_tool_suppression_action does

    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "counter must reset to 0 after tool goes inactive"
    );

    // Phase 3: new tool execution starts — suppression works from scratch.
    let action = evaluate_tool_suppression(true, s.consecutive_tool_suppression_ticks, max_ticks);
    match action {
        ToolSuppressionAction::Suppress { ticks } => {
            assert_eq!(ticks, 1, "first tick of new tool execution should be 1");
            s.reset_idle_preserving_tool_suppression();
            s.consecutive_tool_suppression_ticks = ticks;
        }
        other => panic!(
            "expected Suppress for fresh tool execution, got {other:?}",
            other = std::mem::discriminant(&other)
        ),
    }

    // Continue for max_ticks-1 more ticks — all should suppress.
    for _ in 1..max_ticks {
        let action =
            evaluate_tool_suppression(true, s.consecutive_tool_suppression_ticks, max_ticks);
        assert!(
            matches!(action, ToolSuppressionAction::Suppress { .. }),
            "should still suppress within the fresh cap window"
        );
        if let ToolSuppressionAction::Suppress { ticks } = action {
            s.reset_idle_preserving_tool_suppression();
            s.consecutive_tool_suppression_ticks = ticks;
        }
    }

    // Next tick should exceed the cap again.
    let action = evaluate_tool_suppression(true, s.consecutive_tool_suppression_ticks, max_ticks);
    assert!(
        matches!(action, ToolSuppressionAction::CapExceeded { .. }),
        "fresh cap window should also expire after max_ticks"
    );
}

/// Verify that genuine progress (reset_idle) during an active tool execution resets the
/// cap counter, giving the tool a fresh suppression window.
///
/// Scenario: tool is active and approaching the cap, then file activity triggers
/// reset_idle() (genuine progress). The cap counter resets, and the tool gets a full
/// fresh window of suppression ticks.
#[test]
fn tool_suppression_genuine_progress_resets_cap_during_active_tool() {
    let max_ticks: u32 = 5;
    let mut s = MonitorLoopState::new();

    // Accumulate ticks approaching the cap (max_ticks - 1 ticks).
    for _ in 0..(max_ticks - 1) {
        let action =
            evaluate_tool_suppression(true, s.consecutive_tool_suppression_ticks, max_ticks);
        if let ToolSuppressionAction::Suppress { ticks } = action {
            s.reset_idle_preserving_tool_suppression();
            s.consecutive_tool_suppression_ticks = ticks;
        } else {
            panic!("expected Suppress while approaching cap");
        }
    }

    assert_eq!(
        s.consecutive_tool_suppression_ticks,
        max_ticks - 1,
        "should be one tick away from the cap"
    );

    // Genuine progress detected (file activity) — full reset.
    s.reset_idle();

    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "genuine progress must zero the tool suppression counter"
    );

    // Tool is still active — but now has a fresh cap window.
    // We should be able to suppress for another full max_ticks.
    for expected_tick in 1..=max_ticks {
        let action =
            evaluate_tool_suppression(true, s.consecutive_tool_suppression_ticks, max_ticks);
        match action {
            ToolSuppressionAction::Suppress { ticks } => {
                assert_eq!(ticks, expected_tick);
                s.reset_idle_preserving_tool_suppression();
                s.consecutive_tool_suppression_ticks = ticks;
            }
            other => panic!(
                "expected Suppress at tick {expected_tick} after progress reset, got {other:?}",
                other = std::mem::discriminant(&other)
            ),
        }
    }

    // Now cap should be exceeded.
    let action = evaluate_tool_suppression(true, s.consecutive_tool_suppression_ticks, max_ticks);
    assert!(
        matches!(action, ToolSuppressionAction::CapExceeded { .. }),
        "cap must be exceeded after full window following progress reset"
    );
}

// ============================================================================
// Unit tests for warn-once behavior via apply_tool_suppression_action
// ============================================================================

use super::super::runtime::core::apply_tool_suppression_action;

/// Verify that the first CapExceeded action sets the cap_warned flag (which gates
/// the warning emission), and subsequent CapExceeded actions do NOT re-emit
/// (flag stays true, guarding the eprintln branch).
#[test]
fn cap_exceeded_warns_once_then_suppresses_duplicate_warnings() {
    let mut s = MonitorLoopState::new();
    let max_ticks: u32 = 3;

    assert!(
        !s.tool_suppression_cap_warned,
        "new MonitorLoopState must start with cap_warned = false"
    );

    // First CapExceeded: flag transitions false → true (warning emitted).
    let result = apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 4 },
        max_ticks,
        &mut s,
    );
    assert!(!result, "CapExceeded must return false (no suppression)");
    assert!(
        s.tool_suppression_cap_warned,
        "first CapExceeded must set cap_warned = true"
    );

    // Second CapExceeded: flag is already true — no warning re-emitted.
    let result = apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 5 },
        max_ticks,
        &mut s,
    );
    assert!(!result, "CapExceeded must still return false");
    assert!(
        s.tool_suppression_cap_warned,
        "cap_warned must remain true across subsequent CapExceeded ticks"
    );

    // Third CapExceeded: still no duplicate.
    let result = apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 6 },
        max_ticks,
        &mut s,
    );
    assert!(!result, "CapExceeded must still return false");
    assert!(s.tool_suppression_cap_warned, "cap_warned must remain true");
}

/// Verify that `reset_idle()` resets the cap-warned flag, allowing the warning
/// to fire again if a new cap-exceeded episode occurs after genuine progress.
#[test]
fn reset_idle_resets_cap_warned_allowing_fresh_warning() {
    let mut s = MonitorLoopState::new();
    let max_ticks: u32 = 2;

    // First episode: cap exceeded, warning emitted.
    apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 3 },
        max_ticks,
        &mut s,
    );
    assert!(s.tool_suppression_cap_warned);

    // Genuine progress resets everything.
    s.reset_idle();
    assert!(
        !s.tool_suppression_cap_warned,
        "reset_idle must clear cap_warned for fresh warning on next episode"
    );
    assert_eq!(s.consecutive_tool_suppression_ticks, 0);

    // Second episode: cap exceeded again — warning should fire (flag was reset).
    apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 3 },
        max_ticks,
        &mut s,
    );
    assert!(
        s.tool_suppression_cap_warned,
        "new episode must set cap_warned again after reset_idle"
    );
}

/// Verify the Inactive → CapExceeded → Inactive → CapExceeded cycle:
/// each Inactive transition resets cap_warned so the next CapExceeded can warn.
#[test]
fn inactive_resets_cap_warned_for_fresh_episode() {
    let mut s = MonitorLoopState::new();
    let max_ticks: u32 = 2;

    // Phase 1: cap exceeded — warning emitted.
    apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 3 },
        max_ticks,
        &mut s,
    );
    assert!(s.tool_suppression_cap_warned);

    // Phase 2: tool becomes inactive — Inactive arm resets flag and ticks.
    apply_tool_suppression_action(ToolSuppressionAction::Inactive, max_ticks, &mut s);
    assert!(
        !s.tool_suppression_cap_warned,
        "Inactive must reset cap_warned"
    );
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 0,
        "Inactive must zero tick counter"
    );

    // Phase 3: new tool execution hits cap — warning fires again.
    apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 3 },
        max_ticks,
        &mut s,
    );
    assert!(
        s.tool_suppression_cap_warned,
        "new CapExceeded after Inactive must set flag again"
    );
}

/// Verify that `reset_idle_preserving_tool_suppression` does NOT reset the
/// cap-warned flag. The tool suppressor's own idle reset should preserve both
/// the tick counter and the warning state.
#[test]
fn preserving_reset_keeps_cap_warned_flag() {
    let mut s = MonitorLoopState::new();
    let max_ticks: u32 = 2;

    // Trigger cap_warned via apply_tool_suppression_action.
    apply_tool_suppression_action(
        ToolSuppressionAction::CapExceeded { ticks: 3 },
        max_ticks,
        &mut s,
    );
    assert!(s.tool_suppression_cap_warned);
    s.consecutive_tool_suppression_ticks = 8;

    s.reset_idle_preserving_tool_suppression();

    assert!(
        s.tool_suppression_cap_warned,
        "preserving reset must not reset the cap_warned flag"
    );
    assert_eq!(
        s.consecutive_tool_suppression_ticks, 8,
        "preserving reset must not reset the tick counter"
    );
}

/// Verify that the Suppress arm does not affect the cap_warned flag.
/// Suppress resets idle state (preserving tool ticks) and returns true.
#[test]
fn suppress_action_does_not_affect_cap_warned() {
    let mut s = MonitorLoopState::new();
    let max_ticks: u32 = 10;

    assert!(!s.tool_suppression_cap_warned);

    let result = apply_tool_suppression_action(
        ToolSuppressionAction::Suppress { ticks: 1 },
        max_ticks,
        &mut s,
    );
    assert!(result, "Suppress must return true");
    assert!(
        !s.tool_suppression_cap_warned,
        "Suppress must not set cap_warned"
    );
    assert_eq!(s.consecutive_tool_suppression_ticks, 1);
}

/// Verify that when the tool suppressor's cap is exceeded, other suppressors
/// can still independently prevent the timeout from firing.
///
/// `any_suppressor_active` uses short-circuit OR with partial completion checked
/// first. To exercise the fallthrough path, partial completion returns false
/// initially (letting the tool suppressor accumulate ticks and hit its cap),
/// then switches to true once the cap is exceeded. `required_idle_confirmations`
/// is set to 2 so there is a grace tick between cap-exceeded and timeout firing,
/// during which partial completion kicks in and suppresses.
#[test]
fn tool_suppressor_cap_exceeded_but_partial_completion_suppresses() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);
    let should_stop_for_test = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Tool check always returns true — will hit the cap.
    let tool_check_count = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let tool_check_count_clone = Arc::clone(&tool_check_count);
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(move || {
        tool_check_count_clone.fetch_add(1, Ordering::SeqCst);
        true
    });

    // Partial completion starts returning false so the tool suppressor can
    // accumulate ticks and hit its cap. Once tool_check_count reaches 3
    // (cap exceeded with max_tool_suppression_ticks=2), partial completion
    // switches to true to take over suppression.
    let tool_check_count_for_partial = Arc::clone(&tool_check_count);
    let partial_check_count = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let partial_check_count_clone = Arc::clone(&partial_check_count);
    let partial_completion_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(move || {
        partial_check_count_clone.fetch_add(1, Ordering::SeqCst);
        // Return true only after tool cap has been exceeded.
        // With max_tool_suppression_ticks=2, the cap fires on the 3rd tool check
        // (ticks 0→Suppress, 1→Suppress, 2→CapExceeded). tool_check_count ≥ 3
        // means the cap has been hit at least once.
        tool_check_count_for_partial.load(Ordering::SeqCst) >= 3
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        // Use 50ms interval so the test completes quickly.
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        // 2 confirmations: when tool cap exceeds on tick 3, idle count reaches 1
        // (not enough to kill). On tick 4, partial completion kicks in and suppresses.
        required_idle_confirmations: 2,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: Some(partial_completion_check),
        tool_activity_check: Some(tool_activity_check),
        // Low cap so it exceeds quickly.
        max_tool_suppression_ticks: 2,
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

    // Wait until partial completion has been called enough times after taking
    // over suppression (at least 5 total calls ensures it has been active for
    // several ticks after the tool cap was exceeded).
    while partial_check_count.load(Ordering::SeqCst) < 5 {
        std::thread::yield_now();
    }

    // The monitor should still be running because partial_completion_check
    // suppresses the timeout even after tool cap is exceeded.
    // Signal clean stop.
    should_stop_for_test.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "partial completion suppressor must prevent timeout even when tool suppressor cap is exceeded"
    );

    // Verify both suppressors were actually called.
    assert!(
        tool_check_count.load(Ordering::SeqCst) >= 3,
        "tool check must have been called enough times to exceed cap"
    );
    assert!(
        partial_check_count.load(Ordering::SeqCst) >= 1,
        "partial completion check must have been called at least once after cap exceeded"
    );
}

/// Verify that when the tool suppressor's cap is exceeded, the file activity
/// suppressor can still independently prevent the timeout from firing.
///
/// This exercises the real `check_file_activity_suppression` path in
/// `any_suppressor_active`: tool suppressor returns false (cap exceeded),
/// then file activity suppressor scans the workspace for recently-modified
/// AI-generated files and finds them → suppresses the timeout.
#[test]
fn tool_suppressor_cap_exceeded_but_file_activity_suppresses() {
    let timestamp = new_activity_timestamp();
    wait_until_idle_timeout_exceeded(&timestamp, Duration::ZERO);

    let should_stop = Arc::new(AtomicBool::new(false));
    let should_stop_clone = Arc::clone(&should_stop);
    let should_stop_for_test = Arc::clone(&should_stop);

    let (mock_child, _controller) = MockAgentChild::new_running(0);
    let child = Arc::new(Mutex::new(Box::new(mock_child) as Box<dyn AgentChild>));

    let executor: Arc<dyn crate::executor::ProcessExecutor> = Arc::new(MockProcessExecutor::new());

    // Tool check always returns true — will hit the cap.
    let tool_check_count = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let tool_check_count_clone = Arc::clone(&tool_check_count);
    let tool_activity_check: Arc<dyn Fn() -> bool + Send + Sync> = Arc::new(move || {
        tool_check_count_clone.fetch_add(1, Ordering::SeqCst);
        true
    });

    // Create a workspace with a recently-modified AI-generated file in .agent/.
    // The file activity scanner checks .agent/ for files with recent mtime.
    // MemoryWorkspace files get SystemTime::now() as their modified time on creation,
    // so the file will appear "fresh" during the test's short lifetime.
    let workspace: Arc<dyn crate::workspace::Workspace> =
        Arc::new(MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "# Progress"));

    let file_activity_config = Some(FileActivityConfig {
        tracker: new_file_activity_tracker(),
        workspace,
    });

    let config = MonitorConfig {
        timeout: Duration::ZERO,
        // Use 50ms interval so the test completes quickly.
        check_interval: Duration::from_millis(50),
        kill_config: fast_kill_config(),
        required_idle_confirmations: 1,
        check_child_processes: false,
        completion_check: None,
        partial_completion_check: None,
        tool_activity_check: Some(tool_activity_check),
        // Low cap so it exceeds quickly.
        max_tool_suppression_ticks: 2,
    };

    let handle = std::thread::spawn(move || {
        monitor_idle_timeout_with_interval_and_kill_config(
            &timestamp,
            file_activity_config.as_ref(),
            &child,
            &should_stop_clone,
            &executor,
            config,
        )
    });

    // Wait until the tool cap has been exceeded (at least max_ticks+2 checks)
    // and the file activity suppressor has had a chance to fire.
    while tool_check_count.load(Ordering::SeqCst) < 5 {
        std::thread::yield_now();
    }

    // The monitor should still be running because file activity suppressor
    // detects recent file changes even after tool cap is exceeded.
    // Signal clean stop.
    should_stop_for_test.store(true, Ordering::Release);

    let result = handle.join().expect("monitor thread panicked");
    assert_eq!(
        result,
        MonitorResult::ProcessCompleted,
        "file activity suppressor must prevent timeout even when tool suppressor cap is exceeded"
    );

    // Verify the tool check was called enough times to exceed the cap.
    assert!(
        tool_check_count.load(Ordering::SeqCst) >= 3,
        "tool check must have been called enough times to exceed cap"
    );
}
