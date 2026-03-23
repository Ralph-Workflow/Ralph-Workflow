use super::super::*;
use std::io::Cursor;
use std::io::Read;
use std::sync::atomic::Ordering;

#[test]
fn activity_tracking_reader_updates_on_read() {
    let data = b"hello world";
    let cursor = Cursor::new(data.to_vec());
    let timestamp = new_activity_timestamp();

    let mut reader = ActivityTrackingReader::new(cursor, timestamp.clone());

    timestamp.store(u64::MAX, Ordering::Release);
    assert_eq!(timestamp.load(Ordering::Acquire), u64::MAX);

    let mut buf = [0u8; 5];
    let n = reader.read(&mut buf).unwrap();
    assert_eq!(n, 5);
    assert_ne!(timestamp.load(Ordering::Acquire), u64::MAX);
}

#[test]
fn activity_tracking_reader_no_update_on_zero_read() {
    let cursor = Cursor::new(Vec::<u8>::new());
    let timestamp = new_activity_timestamp();
    let mut reader = ActivityTrackingReader::new(cursor, timestamp.clone());

    timestamp.store(0, Ordering::Release);
    assert_eq!(timestamp.load(Ordering::Acquire), 0);

    let mut buf = [0u8; 5];
    let n = reader.read(&mut buf).unwrap();
    assert_eq!(n, 0);
    assert_eq!(timestamp.load(Ordering::Acquire), 0);
}

#[test]
fn activity_tracking_reader_passes_through_data() {
    let data = b"hello world";
    let cursor = Cursor::new(data.to_vec());
    let timestamp = new_activity_timestamp();
    let mut reader = ActivityTrackingReader::new(cursor, timestamp);

    let mut buf = [0u8; 20];
    let n = reader.read(&mut buf).unwrap();

    assert_eq!(n, 11);
    assert_eq!(&buf[..n], b"hello world");
}

#[test]
fn stderr_activity_tracker_updates_timestamp() {
    let data = b"debug output\nmore output\n";
    let cursor = Cursor::new(data.to_vec());
    let timestamp = new_activity_timestamp();

    timestamp.store(u64::MAX, Ordering::Release);
    assert_eq!(timestamp.load(Ordering::Acquire), u64::MAX);

    let mut tracker = StderrActivityTracker::new(cursor, timestamp.clone());
    let mut buf = [0u8; 50];
    let n = tracker.read(&mut buf).unwrap();
    assert!(n > 0);

    assert_ne!(timestamp.load(Ordering::Acquire), u64::MAX);
}

#[test]
fn stderr_activity_tracker_no_update_on_zero_read() {
    let cursor = Cursor::new(Vec::<u8>::new());
    let timestamp = new_activity_timestamp();
    let mut tracker = StderrActivityTracker::new(cursor, timestamp.clone());

    timestamp.store(0, Ordering::Release);
    assert_eq!(timestamp.load(Ordering::Acquire), 0);

    let mut buf = [0u8; 10];
    let n = tracker.read(&mut buf).unwrap();
    assert_eq!(n, 0);

    assert_eq!(timestamp.load(Ordering::Acquire), 0);
}
