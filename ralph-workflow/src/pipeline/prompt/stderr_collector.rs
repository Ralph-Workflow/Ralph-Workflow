use std::io::{self, Read};
use std::sync::Arc;

pub(super) fn collect_stderr_with_cap_and_drain<R: Read>(
    mut reader: R,
    max_bytes: usize,
    cancel: &std::sync::atomic::AtomicBool,
) -> io::Result<String> {
    let mut buf = [0u8; 8192];
    let mut collected = Vec::<u8>::new();
    let mut truncated = false;

    loop {
        if cancel.load(std::sync::atomic::Ordering::Acquire) {
            break;
        }

        let n = match reader.read(&mut buf) {
            Ok(n) => n,
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(std::time::Duration::from_millis(10));
                continue;
            }
            Err(e) => return Err(e),
        };
        if n == 0 {
            break;
        }

        if collected.len() < max_bytes {
            let remaining = max_bytes - collected.len();
            let to_take = remaining.min(n);
            collected.extend_from_slice(&buf[..to_take]);
            if to_take < n {
                truncated = true;
            }
        } else {
            truncated = true;
        }
    }

    let mut stderr_output = String::from_utf8_lossy(&collected).into_owned();
    if truncated {
        if !stderr_output.ends_with('\n') {
            stderr_output.push('\n');
        }
        stderr_output.push_str("<stderr truncated>");
    }

    Ok(stderr_output)
}

pub(super) fn cancel_and_join_stderr_collector(
    cancel: &Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<io::Result<String>>>,
    join_timeout: std::time::Duration,
) {
    use std::sync::atomic::Ordering;
    use std::time::{Duration, Instant};

    cancel.store(true, Ordering::Release);

    let deadline = Instant::now() + join_timeout;
    while Instant::now() < deadline {
        let finished = stderr_join_handle
            .as_ref()
            .is_none_or(std::thread::JoinHandle::is_finished);
        if finished {
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }

    let finished = stderr_join_handle
        .as_ref()
        .is_some_and(std::thread::JoinHandle::is_finished);
    if finished {
        let _ = stderr_join_handle.take().and_then(|h| h.join().ok());
    }
}
