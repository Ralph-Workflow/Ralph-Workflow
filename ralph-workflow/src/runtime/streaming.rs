//! Streaming I/O utilities for runtime boundary.
//!
//! This module provides streaming readers for process output.

pub mod streaming_line_reader;

pub use streaming_line_reader::{StreamingLineReader, MAX_BUFFER_SIZE};

use std::io::{self, BufRead, Read};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::Duration;

use crate::pipeline::idle_timeout::{ActivityTrackingReader, SharedActivityTimestamp};

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

    fn refill_if_needed(&mut self) -> io::Result<()> {
        if should_cancel_or_eof(
            self.cancel.load(Ordering::Acquire),
            self.eof,
            self.consumed,
            &self.buffer,
        ) {
            if self.cancel.load(Ordering::Acquire) {
                self.buffer.clear();
                self.consumed = 0;
                self.eof = true;
            }
            return Ok(());
        }

        self.buffer.clear();
        self.consumed = 0;

        loop {
            if self.cancel.load(Ordering::Acquire) {
                self.eof = true;
                return Ok(());
            }
            match self.rx.recv_timeout(self.poll_interval) {
                Ok(Ok(chunk)) => {
                    if chunk.is_empty() {
                        self.eof = true;
                        return Ok(());
                    }
                    self.buffer = chunk;
                    return Ok(());
                }
                Ok(Err(e)) => return Err(e),
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    self.eof = true;
                    return Ok(());
                }
            }
        }
    }
}

fn should_cancel_or_eof(cancelled: bool, eof: bool, consumed: usize, buffer: &[u8]) -> bool {
    cancelled || eof || consumed < buffer.len()
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

/// Clean up the stdout pump thread.
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
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        while !pump_handle.is_finished() && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        if pump_handle.is_finished() {
            let _ = pump_handle.join();
        } else {
            if let Some(msg) = detach_message_for_logger(true) {
                logger.warn(msg);
            }
            drop(pump_handle);
        }
    } else {
        let _ = pump_handle.join();
    }
}

/// Create a bounded channel for stdout pumping.
pub fn create_stdout_channel() -> StdoutChannel {
    mpsc::sync_channel(STDOUT_PUMP_CHANNEL_CAPACITY)
}
