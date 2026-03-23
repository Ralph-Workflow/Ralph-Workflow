//! Streaming I/O utilities for runtime boundary.
//!
//! This module provides streaming readers for process output.

use std::io::{self, BufRead, BufReader, Read};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::Duration;

use crate::pipeline::idle_timeout::{ActivityTrackingReader, SharedActivityTimestamp};

/// A line-oriented reader that processes data as it arrives.
///
/// Unlike `BufReader::lines()`, this reader yields lines immediately when newlines are
/// encountered, without waiting for the buffer to fill. This enables real-time streaming
/// for agents that output NDJSON gradually.
///
/// # Buffer Size Limit
///
/// This reader enforces a hard cap for a single line (bytes since the last '\n') to
/// prevent memory exhaustion from malicious or malformed input that never contains
/// newlines.
pub struct StreamingLineReader<R: Read> {
    inner: BufReader<R>,
    buffer: Vec<u8>,
    consumed: usize,
}

/// Maximum line size in bytes.
///
/// Important: `BufRead::lines()` uses `read_line()` under the hood. Without a per-line
/// cap, `read_line()` can accumulate arbitrarily large `String`s even if `fill_buf()`
/// only ever returns small chunks.
///
/// The value of 1 MiB was chosen to:
/// - Handle most legitimate JSON documents (typically < 100KB)
/// - Allow for reasonably long single-line JSON outputs
/// - Prevent memory exhaustion from malicious input
/// - Keep the buffer size manageable for most systems
///
/// If your use case requires larger single-line JSON, consider:
/// - Modifying your agent to output NDJSON (newline-delimited JSON)
/// - Adjusting this constant and rebuilding
pub const MAX_BUFFER_SIZE: usize = 1024 * 1024; // 1 MiB

impl<R: Read> StreamingLineReader<R> {
    /// Create a new streaming line reader with a small buffer for low latency.
    pub fn new(inner: R) -> Self {
        const BUFFER_SIZE: usize = 1024;
        Self {
            inner: BufReader::with_capacity(BUFFER_SIZE, inner),
            buffer: Vec::new(),
            consumed: 0,
        }
    }

    fn fill_buffer(&mut self) -> io::Result<usize> {
        let current_size = self.buffer.len() - self.consumed;
        check_buffer_size_limit(current_size)?;

        let mut read_buf = [0u8; 256];
        let n = self.inner.read(&mut read_buf)?;
        if n > 0 {
            let new_size = current_size + n;
            check_buffer_size_limit(new_size)?;
            self.buffer.extend_from_slice(&read_buf[..n]);
        }
        Ok(n)
    }
}

fn check_buffer_size_limit(current_size: usize) -> io::Result<()> {
    if current_size >= MAX_BUFFER_SIZE {
        return Err(io::Error::other(format!(
            "StreamingLineReader buffer exceeded maximum size of {MAX_BUFFER_SIZE} bytes. \
             This may indicate malformed input or an agent that is not sending newlines."
        )));
    }
    Ok(())
}

fn check_line_size_limit(line_len: usize) -> io::Result<()> {
    if line_len >= MAX_BUFFER_SIZE {
        return Err(io::Error::other(format!(
            "StreamingLineReader line exceeded maximum size of {MAX_BUFFER_SIZE} bytes. \
             This may indicate malformed input or an agent that is not sending newlines."
        )));
    }
    Ok(())
}

fn check_chunk_size_limit(line_len: usize, to_take: usize) -> io::Result<()> {
    let remaining = MAX_BUFFER_SIZE - line_len;
    if to_take > remaining {
        return Err(io::Error::other(format!(
            "StreamingLineReader line would exceed maximum size of {MAX_BUFFER_SIZE} bytes. \
             This may indicate malformed input or an agent that is not sending newlines."
        )));
    }
    Ok(())
}

fn parse_utf8_chunk(chunk: &[u8]) -> io::Result<&str> {
    std::str::from_utf8(chunk).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("agent output is not valid UTF-8: {e}"),
        )
    })
}

impl<R: Read> Read for StreamingLineReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let available = self.buffer.len() - self.consumed;
        if available > 0 {
            let to_copy = available.min(buf.len());
            buf[..to_copy].copy_from_slice(&self.buffer[self.consumed..self.consumed + to_copy]);
            self.consumed += to_copy;

            if self.consumed == self.buffer.len() {
                self.buffer.clear();
                self.consumed = 0;
            }
            return Ok(to_copy);
        }

        self.inner.read(buf)
    }
}

impl<R: Read> BufRead for StreamingLineReader<R> {
    fn fill_buf(&mut self) -> io::Result<&[u8]> {
        const MAX_ATTEMPTS: usize = 8;

        if self.consumed < self.buffer.len() {
            return Ok(&self.buffer[self.consumed..]);
        }

        self.buffer.clear();
        self.consumed = 0;

        let total_read = fill_buffer_with_retry(self, MAX_ATTEMPTS)?;
        if total_read == 0 {
            return Ok(&[]);
        }

        Ok(&self.buffer[self.consumed..])
    }

    fn consume(&mut self, amt: usize) {
        self.consumed = (self.consumed + amt).min(self.buffer.len());

        if self.consumed == self.buffer.len() {
            self.buffer.clear();
            self.consumed = 0;
        }
    }

    fn read_line(&mut self, buf: &mut String) -> io::Result<usize> {
        let start_len = buf.len();
        loop {
            match read_line_step(self, buf, start_len)? {
                ReadLineStep::Done => return Ok(buf.len() - start_len),
                ReadLineStep::Continue => {}
            }
        }
    }
}

enum ReadLineStep {
    Done,
    Continue,
}

fn read_line_step<R: Read>(
    reader: &mut StreamingLineReader<R>,
    buf: &mut String,
    start_len: usize,
) -> io::Result<ReadLineStep> {
    check_line_size_limit(buf.len() - start_len)?;
    let available = reader.fill_buf()?;
    if available.is_empty() {
        return Ok(ReadLineStep::Done);
    }
    let newline_pos = available.iter().position(|&b| b == b'\n');
    let to_take = newline_pos.map_or(available.len(), |i| i + 1);
    check_chunk_size_limit(buf.len() - start_len, to_take)?;
    buf.push_str(parse_utf8_chunk(&available[..to_take])?);
    reader.consume(to_take);
    Ok(newline_pos.map_or(ReadLineStep::Continue, |_| ReadLineStep::Done))
}

/// Outcome of a single fill attempt.
enum FillStepOutcome {
    /// Loop should stop; return this accumulated total.
    Stop(usize),
    /// Loop should continue with this updated total.
    Continue(usize),
}

fn classify_fill_step(n: usize, total_read: usize, has_newline: bool) -> FillStepOutcome {
    match n {
        0 if total_read == 0 => FillStepOutcome::Stop(0),
        0 => FillStepOutcome::Stop(total_read),
        _ if has_newline => FillStepOutcome::Stop(total_read + n),
        _ => FillStepOutcome::Continue(total_read + n),
    }
}

fn fill_buffer_with_retry(
    reader: &mut StreamingLineReader<impl Read>,
    max_attempts: usize,
) -> io::Result<usize> {
    let mut total_read = 0;
    for _ in 0..max_attempts {
        let n = reader.fill_buffer()?;
        match classify_fill_step(n, total_read, reader.buffer.contains(&b'\n')) {
            FillStepOutcome::Stop(v) => return Ok(v),
            FillStepOutcome::Continue(next) => total_read = next,
        }
    }
    Ok(total_read)
}

/// Result type for stdout channel operations.
type StdoutChannel = (
    mpsc::SyncSender<io::Result<Vec<u8>>>,
    mpsc::Receiver<io::Result<Vec<u8>>>,
);

// Upper bound on stdout data buffered between the pump thread and the parser.
// Each pump chunk is up to 4096 bytes.
pub const STDOUT_PUMP_CHANNEL_CAPACITY: usize = 256; // 256 * 4096B chunks ~= 1MiB worst-case

/// A reader that wraps a channel receiver with cancelation support.
///
/// This allows the reader to be stopped promptly when cancellation is requested,
/// even if the underlying receive is blocking.
pub struct CancelAwareReceiverBufRead {
    rx: mpsc::Receiver<io::Result<Vec<u8>>>,
    cancel: Arc<AtomicBool>,
    poll_interval: Duration,
    buffer: Vec<u8>,
    consumed: usize,
    eof: bool,
}

impl CancelAwareReceiverBufRead {
    /// Create a new cancel-aware reader.
    pub fn new(
        rx: mpsc::Receiver<io::Result<Vec<u8>>>,
        cancel: Arc<AtomicBool>,
        poll_interval: Duration,
    ) -> Self {
        Self {
            rx,
            cancel,
            poll_interval,
            buffer: Vec::new(),
            consumed: 0,
            eof: false,
        }
    }

    fn apply_cancel_if_needed(&mut self) {
        if self.cancel.load(Ordering::Acquire) {
            self.buffer.clear();
            self.consumed = 0;
            self.eof = true;
        }
    }

    fn recv_loop(&mut self) -> io::Result<()> {
        loop {
            if self.cancel.load(Ordering::Acquire) {
                self.eof = true;
                return Ok(());
            }
            if apply_recv_step(
                self.rx.recv_timeout(self.poll_interval),
                &mut self.buffer,
                &mut self.eof,
            )? {
                return Ok(());
            }
        }
    }

    fn refill_if_needed(&mut self) -> io::Result<()> {
        if should_cancel_or_eof(
            self.cancel.load(Ordering::Acquire),
            self.eof,
            self.consumed,
            &self.buffer,
        ) {
            self.apply_cancel_if_needed();
            return Ok(());
        }

        self.buffer.clear();
        self.consumed = 0;
        self.recv_loop()
    }
}

fn should_cancel_or_eof(cancelled: bool, eof: bool, consumed: usize, buffer: &[u8]) -> bool {
    cancelled || eof || consumed < buffer.len()
}

enum RecvStep {
    Done(Vec<u8>),
    Eof,
    Continue,
}

fn apply_recv_result(
    result: Result<io::Result<Vec<u8>>, mpsc::RecvTimeoutError>,
) -> io::Result<RecvStep> {
    match result {
        Ok(Ok(chunk)) if chunk.is_empty() => Ok(RecvStep::Eof),
        Ok(Ok(chunk)) => Ok(RecvStep::Done(chunk)),
        Ok(Err(e)) => Err(e),
        Err(mpsc::RecvTimeoutError::Timeout) => Ok(RecvStep::Continue),
        Err(mpsc::RecvTimeoutError::Disconnected) => Ok(RecvStep::Eof),
    }
}

/// Apply a single receive result to the buffer state.
///
/// Returns `Ok(true)` when the loop should stop (done or eof), `Ok(false)` to continue.
fn apply_recv_step(
    result: Result<io::Result<Vec<u8>>, mpsc::RecvTimeoutError>,
    buffer: &mut Vec<u8>,
    eof: &mut bool,
) -> io::Result<bool> {
    match apply_recv_result(result)? {
        RecvStep::Done(chunk) => {
            *buffer = chunk;
            Ok(true)
        }
        RecvStep::Eof => {
            *eof = true;
            Ok(true)
        }
        RecvStep::Continue => Ok(false),
    }
}

impl Read for CancelAwareReceiverBufRead {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.refill_if_needed()?;
        if self.eof {
            return Ok(0);
        }

        let available = self.buffer.len() - self.consumed;
        if available == 0 {
            return Ok(0);
        }
        let to_copy = available.min(buf.len());
        buf[..to_copy].copy_from_slice(&self.buffer[self.consumed..self.consumed + to_copy]);
        self.consumed += to_copy;
        Ok(to_copy)
    }
}

impl BufRead for CancelAwareReceiverBufRead {
    fn fill_buf(&mut self) -> io::Result<&[u8]> {
        self.refill_if_needed()?;
        if self.eof {
            return Ok(&[]);
        }
        Ok(&self.buffer[self.consumed..])
    }

    fn consume(&mut self, amt: usize) {
        self.consumed = (self.consumed + amt).min(self.buffer.len());
        if self.consumed == self.buffer.len() {
            self.buffer.clear();
            self.consumed = 0;
        }
    }
}

/// Spawn a thread to pump stdout data from a reader into a channel.
pub fn spawn_stdout_pump(
    stdout: Box<dyn io::Read + Send>,
    activity_timestamp: SharedActivityTimestamp,
    tx: mpsc::SyncSender<io::Result<Vec<u8>>>,
    cancel: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut tracked_stdout = ActivityTrackingReader::new(stdout, activity_timestamp);
        let mut buf = [0u8; 4096];

        loop {
            if cancel.load(Ordering::Acquire) {
                return;
            }
            match tracked_stdout.read(&mut buf) {
                Ok(0) => {
                    if tx.send(Ok(Vec::new())).is_err() {
                        return;
                    }
                    return;
                }
                Ok(n) => {
                    if tx.send(Ok(buf[..n].to_vec())).is_err() {
                        return;
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    if cancel.load(Ordering::Acquire) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(e) => {
                    let _ = tx.send(Err(e));
                    return;
                }
            }
        }
    })
}

fn pump_should_detach(cancelled: bool, parse_err: &io::Result<()>) -> bool {
    cancelled || parse_err.is_err()
}

fn detach_message_for_logger(detached: bool) -> Option<&'static str> {
    detached.then_some("Stdout pump thread did not exit; detaching thread")
}

fn wait_for_pump_deadline(pump_handle: &std::thread::JoinHandle<()>, deadline: std::time::Instant) {
    while !pump_handle.is_finished() && std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn finalize_pump(pump_handle: std::thread::JoinHandle<()>, logger: &crate::logger::Logger) {
    if pump_handle.is_finished() {
        let _ = pump_handle.join();
    } else {
        if let Some(msg) = detach_message_for_logger(true) {
            logger.warn(msg);
        }
        drop(pump_handle);
    }
}

/// Clean up the stdout pump thread.
fn join_or_detach_pump(pump_handle: std::thread::JoinHandle<()>, logger: &crate::logger::Logger) {
    let deadline = std::time::Instant::now() + Duration::from_secs(2);
    wait_for_pump_deadline(&pump_handle, deadline);
    finalize_pump(pump_handle, logger);
}

pub fn cleanup_stdout_pump(
    pump_handle: std::thread::JoinHandle<()>,
    cancel: &Arc<AtomicBool>,
    logger: &crate::logger::Logger,
    parse_result: &io::Result<()>,
) {
    if parse_result.is_err() {
        cancel.store(true, Ordering::Release);
    }

    let should_detach = pump_should_detach(cancel.load(Ordering::Acquire), parse_result);
    if should_detach {
        join_or_detach_pump(pump_handle, logger);
    } else {
        let _ = pump_handle.join();
    }
}

/// Create a bounded channel for stdout pumping.
pub fn create_stdout_channel() -> StdoutChannel {
    mpsc::sync_channel(STDOUT_PUMP_CHANNEL_CAPACITY)
}
