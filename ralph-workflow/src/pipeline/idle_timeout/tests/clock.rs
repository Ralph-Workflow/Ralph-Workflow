use super::super::*;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

/// Mock clock for testing time-related behavior without real delays.
struct MockClock {
    current_ms: AtomicU64,
}

impl MockClock {
    fn new(initial_ms: u64) -> Self {
        Self {
            current_ms: AtomicU64::new(initial_ms),
        }
    }

    fn advance(&self, delta_ms: u64) {
        self.current_ms.fetch_add(delta_ms, Ordering::SeqCst);
    }

    fn jump_back(&self, delta_ms: u64) {
        self.current_ms.fetch_sub(delta_ms, Ordering::SeqCst);
    }
}

impl Clock for MockClock {
    fn now_millis(&self) -> u64 {
        self.current_ms.load(Ordering::SeqCst)
    }
}

#[test]
fn new_activity_timestamp_with_clock_starts_at_now() {
    let clock = MockClock::new(1234);
    let timestamp = new_activity_timestamp_with_clock(&clock);
    assert_eq!(
        time_since_activity_with_clock(&timestamp, &clock),
        Duration::ZERO
    );
}

#[test]
fn touch_activity_with_clock_resets_elapsed_time() {
    let clock = MockClock::new(10_000);
    let timestamp = new_activity_timestamp_with_clock(&clock);

    clock.advance(50);
    assert_eq!(
        time_since_activity_with_clock(&timestamp, &clock),
        Duration::from_millis(50)
    );

    touch_activity_with_clock(&timestamp, &clock);
    assert_eq!(
        time_since_activity_with_clock(&timestamp, &clock),
        Duration::ZERO
    );
}

#[test]
fn is_idle_timeout_exceeded_false_when_recent() {
    let timestamp = new_activity_timestamp();
    assert!(!is_idle_timeout_exceeded(
        &timestamp,
        Duration::from_secs(1)
    ));
}

#[test]
fn is_idle_timeout_exceeded_true_after_timeout() {
    let clock = MockClock::new(100_000);
    let timestamp = new_activity_timestamp_with_clock(&clock);
    clock.advance(2000);
    assert!(is_idle_timeout_exceeded_with_clock(
        &timestamp,
        Duration::from_secs(1),
        &clock
    ));
}

#[test]
fn is_idle_timeout_exceeded_with_mock_clock() {
    let clock = MockClock::new(0);
    let timestamp = new_activity_timestamp_with_clock(&clock);

    assert!(!is_idle_timeout_exceeded_with_clock(
        &timestamp,
        Duration::from_secs(5),
        &clock
    ));

    clock.advance(3000);
    assert!(!is_idle_timeout_exceeded_with_clock(
        &timestamp,
        Duration::from_secs(5),
        &clock
    ));

    clock.advance(3000);
    assert!(is_idle_timeout_exceeded_with_clock(
        &timestamp,
        Duration::from_secs(5),
        &clock
    ));

    touch_activity_with_clock(&timestamp, &clock);
    assert!(!is_idle_timeout_exceeded_with_clock(
        &timestamp,
        Duration::from_secs(5),
        &clock
    ));
}

#[test]
fn idle_timeout_constant_is_five_minutes() {
    assert_eq!(IDLE_TIMEOUT_SECS, 300);
}

#[test]
fn clock_jump_back_does_not_cause_spurious_timeout() {
    let clock = MockClock::new(100_000);
    let timestamp = new_activity_timestamp_with_clock(&clock);

    touch_activity_with_clock(&timestamp, &clock);
    clock.jump_back(50_000);

    let elapsed = time_since_activity_with_clock(&timestamp, &clock);
    assert_eq!(elapsed, Duration::ZERO);
    assert!(!is_idle_timeout_exceeded_with_clock(
        &timestamp,
        Duration::from_secs(IDLE_TIMEOUT_SECS),
        &clock
    ));
}

#[test]
fn monotonic_clock_only_increases() {
    let clock = MonotonicClock::new();
    let t1 = clock.now_millis();
    let t2 = clock.now_millis();
    assert!(t2 >= t1, "Monotonic clock should never go backwards");
}
