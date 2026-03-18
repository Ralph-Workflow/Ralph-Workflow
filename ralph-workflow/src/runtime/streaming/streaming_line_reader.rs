use std::io::{self, BufRead, BufReader, Read};

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
            let line_len = buf.len() - start_len;
            if line_len >= MAX_BUFFER_SIZE {
                return Err(io::Error::other(format!(
                    "StreamingLineReader line exceeded maximum size of {MAX_BUFFER_SIZE} bytes. \
                     This may indicate malformed input or an agent that is not sending newlines."
                )));
            }

            let available = self.fill_buf()?;
            if available.is_empty() {
                return Ok(buf.len() - start_len);
            }

            let newline_pos = available.iter().position(|&b| b == b'\n');
            let to_take = newline_pos.map_or(available.len(), |i| i + 1);

            let remaining = MAX_BUFFER_SIZE - (buf.len() - start_len);
            if to_take > remaining {
                return Err(io::Error::other(format!(
                    "StreamingLineReader line would exceed maximum size of {MAX_BUFFER_SIZE} bytes. \
                     This may indicate malformed input or an agent that is not sending newlines."
                )));
            }

            let chunk = &available[..to_take];
            let chunk_str = std::str::from_utf8(chunk).map_err(|e| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("agent output is not valid UTF-8: {e}"),
                )
            })?;
            buf.push_str(chunk_str);
            self.consume(to_take);

            if newline_pos.is_some() {
                return Ok(buf.len() - start_len);
            }
        }
    }
}

fn fill_buffer_with_retry(
    reader: &mut StreamingLineReader<impl Read>,
    max_attempts: usize,
) -> io::Result<usize> {
    let mut total_read = 0;
    for _ in 0..max_attempts {
        match reader.fill_buffer()? {
            0 if total_read == 0 => return Ok(0),
            0 => break,
            n => {
                total_read += n;
                if reader.buffer.contains(&b'\n') {
                    break;
                }
            }
        }
    }
    Ok(total_read)
}
