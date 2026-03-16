use super::*;

#[derive(Debug)]
struct CountingReader {
    data: Vec<u8>,
    pos: usize,
    total_read: Arc<AtomicUsize>,
}

impl CountingReader {
    fn new(data: Vec<u8>, total_read: Arc<AtomicUsize>) -> Self {
        Self {
            data,
            pos: 0,
            total_read,
        }
    }
}

impl Read for CountingReader {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.pos >= self.data.len() {
            return Ok(0);
        }
        let remaining = self.data.len() - self.pos;
        let n = remaining.min(buf.len());
        buf[..n].copy_from_slice(&self.data[self.pos..self.pos + n]);
        self.pos += n;
        self.total_read.fetch_add(n, Ordering::SeqCst);
        Ok(n)
    }
}

#[test]
fn test_collect_stderr_with_cap_drains_to_eof() {
    let total_read = Arc::new(AtomicUsize::new(0));
    let data = (0..100u8).collect::<Vec<u8>>();
    let reader = CountingReader::new(data.clone(), Arc::clone(&total_read));

    let cancel = AtomicBool::new(false);
    let result = collect_stderr_with_cap_and_drain(reader, 10, &cancel).unwrap();
    assert!(result.contains("<stderr truncated>"));
    assert_eq!(total_read.load(Ordering::SeqCst), data.len());
}

#[test]
fn test_collect_stderr_with_cap_and_drain_retries_on_wouldblock() {
    // Non-blocking stderr reads can return WouldBlock when no data is available.
    // The collector must treat this as "no data yet" rather than a fatal error.
    #[derive(Debug)]
    struct WouldBlockThenEof {
        remaining_wouldblock: usize,
    }

    impl Read for WouldBlockThenEof {
        fn read(&mut self, _buf: &mut [u8]) -> io::Result<usize> {
            if self.remaining_wouldblock > 0 {
                self.remaining_wouldblock -= 1;
                return Err(io::Error::from(io::ErrorKind::WouldBlock));
            }
            Ok(0)
        }
    }

    let reader = WouldBlockThenEof {
        remaining_wouldblock: 3,
    };

    let cancel = AtomicBool::new(false);
    let out = collect_stderr_with_cap_and_drain(reader, 1024, &cancel)
        .expect("stderr collector should not fail on WouldBlock");
    assert!(out.is_empty());
}

#[test]
fn test_cancel_and_join_stderr_collector_does_not_drop_handle_on_timeout() {
    // Regression test: if join times out, we must not drop the JoinHandle.
    // Dropping detaches a potentially-blocked thread, which can leak resources
    // until EOF.
    let cancel = Arc::new(AtomicBool::new(false));
    let stop = Arc::new(AtomicBool::new(false));
    let stop_for_thread = Arc::clone(&stop);

    let mut join_handle = Some(std::thread::spawn(move || -> io::Result<String> {
        // Simulate a blocked stderr read that does not observe cancellation.
        while !stop_for_thread.load(Ordering::Acquire) {
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        Ok(String::new())
    }));

    super::super::stderr_collector::cancel_and_join_stderr_collector(
        &cancel,
        &mut join_handle,
        std::time::Duration::from_millis(10),
    );

    assert!(
        join_handle.is_some(),
        "expected JoinHandle to be preserved when join times out"
    );

    stop.store(true, Ordering::Release);
    if let Some(h) = join_handle.take() {
        let _ = h.join();
    }
}
