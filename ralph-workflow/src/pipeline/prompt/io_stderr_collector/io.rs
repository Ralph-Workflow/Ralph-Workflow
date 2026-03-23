// pipeline/prompt/io_stderr_collector/io.rs — boundary module for stderr collection.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Stderr collection utilities.

use std::sync::Arc;

enum ReadChunkOutcome {
    Data(usize),
    Eof,
    WouldBlock,
}

fn read_chunk<R: std::io::Read>(
    reader: &mut R,
    buf: &mut [u8],
) -> std::io::Result<ReadChunkOutcome> {
    match std::io::Read::read(reader, buf) {
        Ok(0) => Ok(ReadChunkOutcome::Eof),
        Ok(n) => Ok(ReadChunkOutcome::Data(n)),
        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(ReadChunkOutcome::WouldBlock),
        Err(e) => Err(e),
    }
}

fn append_with_cap(collected: &mut Vec<u8>, max_bytes: usize, chunk: &[u8]) -> bool {
    if collected.len() >= max_bytes {
        return true;
    }
    let remaining = max_bytes - collected.len();
    let to_take = remaining.min(chunk.len());
    collected.extend_from_slice(&chunk[..to_take]);
    to_take < chunk.len()
}

fn build_stderr_string(collected: Vec<u8>, truncated: bool) -> String {
    let mut stderr_output = String::from_utf8_lossy(&collected).into_owned();
    if truncated {
        if !stderr_output.ends_with('\n') {
            stderr_output.push('\n');
        }
        stderr_output.push_str("<stderr truncated>");
    }
    stderr_output
}

enum StepOutcome {
    Continue,
    Done,
}

fn process_read_chunk(
    chunk: ReadChunkOutcome,
    collected: &mut Vec<u8>,
    max_bytes: usize,
    truncated: &mut bool,
    buf: &[u8],
) -> StepOutcome {
    match chunk {
        ReadChunkOutcome::Eof => StepOutcome::Done,
        ReadChunkOutcome::WouldBlock => {
            std::thread::sleep(std::time::Duration::from_millis(10));
            StepOutcome::Continue
        }
        ReadChunkOutcome::Data(n) => {
            *truncated |= append_with_cap(collected, max_bytes, &buf[..n]);
            StepOutcome::Continue
        }
    }
}

fn collect_cancelled(cancel: &std::sync::atomic::AtomicBool) -> bool {
    cancel.load(std::sync::atomic::Ordering::Acquire)
}

fn collect_one_chunk<R: std::io::Read>(
    reader: &mut R,
    buf: &mut [u8; 8192],
    collected: &mut Vec<u8>,
    max_bytes: usize,
    truncated: &mut bool,
) -> std::io::Result<StepOutcome> {
    let chunk = read_chunk(reader, buf)?;
    Ok(process_read_chunk(chunk, collected, max_bytes, truncated, buf))
}

/// One iteration of the stderr collection loop.
/// Returns `true` if collection is finished (cancelled or EOF).
fn stderr_loop_step<R: std::io::Read>(
    reader: &mut R,
    buf: &mut [u8; 8192],
    collected: &mut Vec<u8>,
    max_bytes: usize,
    truncated: &mut bool,
    cancel: &std::sync::atomic::AtomicBool,
) -> std::io::Result<bool> {
    if collect_cancelled(cancel) {
        return Ok(true);
    }
    let done = matches!(
        collect_one_chunk(reader, buf, collected, max_bytes, truncated)?,
        StepOutcome::Done
    );
    Ok(done)
}

pub fn collect_stderr_with_cap_and_drain<R: std::io::Read>(
    mut reader: R,
    max_bytes: usize,
    cancel: &std::sync::atomic::AtomicBool,
) -> std::io::Result<String> {
    let mut buf = [0u8; 8192];
    let mut collected = Vec::<u8>::new();
    let mut truncated = false;

    loop {
        if stderr_loop_step(&mut reader, &mut buf, &mut collected, max_bytes, &mut truncated, cancel)? {
            break;
        }
    }

    Ok(build_stderr_string(collected, truncated))
}

fn is_handle_finished(
    handle: &Option<std::thread::JoinHandle<std::io::Result<String>>>,
) -> bool {
    handle.as_ref().is_none_or(std::thread::JoinHandle::is_finished)
}

fn wait_for_handle_finish(
    stderr_join_handle: &Option<std::thread::JoinHandle<std::io::Result<String>>>,
    deadline: std::time::Instant,
) {
    use std::time::Duration;
    while std::time::Instant::now() < deadline && !is_handle_finished(stderr_join_handle) {
        std::thread::sleep(Duration::from_millis(10));
    }
}

pub fn cancel_and_join_stderr_collector(
    cancel: &Arc<std::sync::atomic::AtomicBool>,
    stderr_join_handle: &mut Option<std::thread::JoinHandle<std::io::Result<String>>>,
    join_timeout: std::time::Duration,
) {
    use std::sync::atomic::Ordering;

    cancel.store(true, Ordering::Release);

    let deadline = std::time::Instant::now() + join_timeout;
    wait_for_handle_finish(stderr_join_handle, deadline);

    let finished = stderr_join_handle
        .as_ref()
        .is_some_and(std::thread::JoinHandle::is_finished);
    if finished {
        let _ = stderr_join_handle.take().and_then(|h| h.join().ok());
    }
}
