use super::*;

#[test]
fn test_is_prompt_md_path_matches_by_file_name() {
    assert!(is_prompt_md_path(Path::new("PROMPT.md")));
    assert!(is_prompt_md_path(Path::new("./PROMPT.md")));
    assert!(is_prompt_md_path(Path::new("dir/PROMPT.md")));
    assert!(is_prompt_md_path(Path::new("/tmp/PROMPT.md")));

    assert!(!is_prompt_md_path(Path::new("PROMPT.md.backup")));
    assert!(!is_prompt_md_path(Path::new("PROMPT.mdx")));
}

#[test]
fn test_check_and_restore_returns_and_clears_flag() {
    let monitor = PromptMonitor {
        restoration_detected: Arc::new(AtomicBool::new(true)),
        stop_signal: Arc::new(AtomicBool::new(false)),
        monitor_thread: None,
        warnings: Arc::new(Mutex::new(Vec::new())),
    };

    assert!(monitor.check_and_restore());
    assert!(!monitor.check_and_restore());
}

#[test]
fn test_notify_event_queue_is_bounded() {
    let (tx, _rx) = bounded_event_queue::<u8>();

    for i in 0..NOTIFY_EVENT_QUEUE_CAPACITY {
        tx.try_send(u8::try_from(i % 255).expect("value fits in u8"))
            .expect("expected send within capacity");
    }

    assert!(
        matches!(tx.try_send(0), Err(std::sync::mpsc::TrySendError::Full(_))),
        "expected bounded queue to apply backpressure when full"
    );
}

#[test]
fn test_stop_reports_monitor_thread_panic_as_warning() {
    let handle = std::thread::spawn(|| panic!("boom"));
    let monitor = PromptMonitor {
        restoration_detected: Arc::new(AtomicBool::new(false)),
        stop_signal: Arc::new(AtomicBool::new(false)),
        monitor_thread: Some(handle),
        warnings: Arc::new(Mutex::new(Vec::new())),
    };

    let warnings = monitor.stop();
    assert!(
        warnings.iter().any(|w| w.contains("panicked")),
        "expected a warning about thread panic"
    );
    assert!(
        warnings.iter().any(|w| w.contains("boom")),
        "expected panic payload to be captured"
    );
}
