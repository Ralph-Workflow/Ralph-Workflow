use mcp_server::io::heartbeat::{HeartbeatDecision, HeartbeatMonitor, HeartbeatPolicy};
use std::time::{Duration, Instant};

fn small_policy() -> HeartbeatPolicy {
    HeartbeatPolicy::new(Duration::from_millis(10), 2, Duration::from_millis(30))
}

#[test]
fn monitor_enters_grace_window_after_missed_pings() {
    let mut monitor = HeartbeatMonitor::new(small_policy());
    let start = Instant::now();
    monitor.record_heartbeat(start);

    // Advance just beyond two intervals without new heartbeat.
    let now = start + Duration::from_millis(25);
    let decision = monitor.check(now);
    let HeartbeatDecision::GraceWindow { deadline, .. } = decision else {
        panic!("expected GraceWindow decision");
    };
    assert_eq!(deadline, start + Duration::from_millis(50));
}

#[test]
fn monitor_terminates_after_reconnect_window_expires() {
    let mut monitor = HeartbeatMonitor::new(small_policy());
    let start = Instant::now();
    monitor.record_heartbeat(start);

    // Jump far enough to trigger termination (grace window + reconnect window).
    let now = start + Duration::from_millis(1000);
    let decision = monitor.check(now);
    assert_eq!(decision, HeartbeatDecision::Terminate);
}

#[test]
fn heartbeat_inside_grace_resets_monitor() {
    let policy = small_policy();
    let mut monitor = HeartbeatMonitor::new(policy);
    let start = Instant::now();
    monitor.record_heartbeat(start);

    let now = start + Duration::from_millis(25);
    let decision = monitor.check(now);
    assert!(matches!(decision, HeartbeatDecision::GraceWindow { .. }));

    // Send heartbeat before reconnect window closes.
    let heartbeat_time = now + Duration::from_millis(5);
    monitor.record_heartbeat(heartbeat_time);

    let decision = monitor.check(heartbeat_time + Duration::from_millis(5));
    assert_eq!(decision, HeartbeatDecision::Healthy);
}
