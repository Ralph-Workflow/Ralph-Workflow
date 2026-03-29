//! MCP transport layer for RFC-009 Phase 3.
//!
//! This module provides the stdio and Unix socket transports for MCP JSON-RPC
//! communication. Both use Content-Length framing per the MCP specification:
//! `Content-Length: N\r\n\r\n{JSON body}`.

use crate::mcp_server::types::{JsonRpcRequest, JsonRpcResponse};
use anyhow::{Context, Result};
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

#[cfg(unix)]
use std::os::unix::net::{UnixListener, UnixStream};

/// Result of reading one HTTP-style header line from stdin.
enum HeaderLineResult {
    /// EOF before any content.
    Eof,
    /// Blank line marking end of headers.
    EndOfHeaders,
    /// A `Content-Length` header was parsed.
    ContentLength(usize),
    /// A non-Content-Length header line.
    Other,
}

/// Stdio transport for MCP communication.
///
/// This transport reads JSON-RPC requests from stdin and writes responses to stdout.
/// Each message is framed using the MCP Content-Length protocol.
pub struct StdioTransport<R, W> {
    reader: R,
    writer: W,
    running: Arc<AtomicBool>,
}

/// Unix socket transport for MCP communication.
///
/// The listener is non-blocking so `accept()` returns immediately. Accepted
/// streams are blocking with a read timeout so the server can check the
/// shutdown flag periodically.
pub struct UnixSocketTransport {
    socket_path: PathBuf,
    listener: UnixListener,
    running: Arc<AtomicBool>,
    shutdown_flag: Arc<AtomicBool>,
}

/// Remove an existing socket file and create its parent directory.
#[cfg(unix)]
fn prepare_socket_path(socket_path: &Path) -> Result<()> {
    if socket_path.exists() {
        std::fs::remove_file(socket_path)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::AlreadyExists, e))?;
    }
    if let Some(parent) = socket_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::NotFound, e))?;
    }
    Ok(())
}

/// Bind a non-blocking Unix listener and set socket permissions.
#[cfg(unix)]
fn bind_nonblocking_listener(socket_path: &Path) -> Result<UnixListener> {
    use std::os::unix::fs::PermissionsExt;
    let listener = UnixListener::bind(socket_path).map_err(std::io::Error::other)?;
    listener
        .set_nonblocking(true)
        .map_err(std::io::Error::other)?;
    if let Ok(mut perms) = std::fs::metadata(socket_path).map(|m| m.permissions()) {
        perms.set_mode(0o700);
        std::fs::set_permissions(socket_path, perms).ok();
    }
    Ok(listener)
}

impl UnixSocketTransport {
    /// Create a new Unix socket transport with a shared shutdown flag.
    #[cfg(unix)]
    pub fn new_with_shutdown(socket_path: &Path, shutdown_flag: Arc<AtomicBool>) -> Result<Self> {
        prepare_socket_path(socket_path)?;
        let listener = bind_nonblocking_listener(socket_path)?;
        Ok(Self {
            socket_path: socket_path.to_path_buf(),
            listener,
            running: Arc::new(AtomicBool::new(true)),
            shutdown_flag,
        })
    }

    /// Create a new Unix socket transport (convenience without shutdown flag).
    #[cfg(unix)]
    pub fn new(socket_path: &Path) -> Result<Self> {
        Self::new_with_shutdown(socket_path, Arc::new(AtomicBool::new(false)))
    }

    #[cfg(not(unix))]
    pub fn new(_socket_path: &Path) -> Result<Self> {
        anyhow::bail!("Unix sockets are not supported on this platform")
    }

    #[cfg(not(unix))]
    pub fn new_with_shutdown(_socket_path: &Path, _shutdown_flag: Arc<AtomicBool>) -> Result<Self> {
        anyhow::bail!("Unix sockets are not supported on this platform")
    }

    /// Accept a new connection from the socket.
    ///
    /// Accepted streams are blocking with a 500ms read timeout so the server
    /// can check the shutdown flag between reads.
    pub fn accept(&self) -> Result<Option<McpStream>> {
        match self.listener.accept() {
            Ok((stream, _addr)) => {
                // Stream stays blocking (default). A read timeout lets the
                // server loop check the shutdown flag periodically.
                stream
                    .set_read_timeout(Some(std::time::Duration::from_millis(500)))
                    .ok();
                Ok(Some(McpStream::new_with_shutdown(
                    stream,
                    Arc::clone(&self.shutdown_flag),
                )))
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(anyhow::anyhow!("Accept failed: {}", e)),
        }
    }

    /// Check if the transport is still running.
    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    /// Signal the transport to stop.
    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
    }

    /// Get the socket path.
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }
}

/// Result of reading more data into the McpStream buffer.
enum ReadStep {
    Continue,
    Done(Option<JsonRpcRequest>),
}

/// A stream adapter that handles MCP protocol framing on a Unix socket.
pub struct McpStream {
    stream: UnixStream,
    read_buf: Vec<u8>,
    write_buf: Vec<u8>,
    shutdown_flag: Arc<AtomicBool>,
}

impl McpStream {
    /// Create a new MCP stream from a Unix socket.
    pub fn new(stream: UnixStream) -> Self {
        Self {
            stream,
            read_buf: Vec::new(),
            write_buf: Vec::new(),
            shutdown_flag: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Create a new MCP stream with a shutdown flag for graceful termination.
    pub fn new_with_shutdown(stream: UnixStream, shutdown_flag: Arc<AtomicBool>) -> Self {
        Self {
            stream,
            read_buf: Vec::new(),
            write_buf: Vec::new(),
            shutdown_flag,
        }
    }

    /// Find the header/body separator in a byte slice.
    ///
    /// Handles both `\r\n\r\n` (MCP spec) and `\n\n` (lenient) separators.
    /// Returns the byte offset of the first body byte.
    fn find_header_body_separator(data: &[u8]) -> Option<usize> {
        // Check for \r\n\r\n first (MCP spec)
        if let Some(pos) = data.windows(4).position(|w| w == b"\r\n\r\n") {
            return Some(pos + 4);
        }
        // Fallback: \n\n
        if let Some(pos) = data.windows(2).position(|w| w == b"\n\n") {
            return Some(pos + 2);
        }
        None
    }

    /// Parse the message end offset from a buffer slice starting at a Content-Length header.
    fn parse_message_end_at(buf: &[u8], pos: usize) -> Option<usize> {
        let slice = &buf[pos..];
        let body_start = pos + Self::find_header_body_separator(slice)?;
        let header_end = buf[pos..body_start].iter().position(|&b| b == b'\n')?;
        let len_str =
            std::str::from_utf8(&buf[pos + b"Content-Length:".len()..pos + header_end]).ok()?;
        let content_len: usize = len_str.trim().parse().ok()?;
        let msg_end = body_start + content_len;
        if msg_end <= buf.len() {
            Some(msg_end)
        } else {
            None
        }
    }

    /// Check if there's a complete message in the buffer.
    fn find_message_end(&self) -> Option<usize> {
        let buf = &self.read_buf;
        let pos = buf
            .windows(b"Content-Length:".len())
            .position(|w| w == b"Content-Length:")?;
        Self::parse_message_end_at(buf, pos)
    }

    /// Handle a non-fatal IO error (WouldBlock/TimedOut), checking for shutdown.
    fn handle_transient_error(shutdown_flag: &AtomicBool) -> ReadStep {
        if shutdown_flag.load(Ordering::Acquire) {
            ReadStep::Done(None)
        } else {
            ReadStep::Continue
        }
    }

    /// Handle EOF: return Done if buffer is empty, error if partial message was received.
    fn handle_eof(read_buf: &[u8]) -> Result<ReadStep> {
        if read_buf.is_empty() {
            Ok(ReadStep::Done(None))
        } else {
            Err(anyhow::anyhow!("Incomplete message at EOF"))
        }
    }

    /// Check if an IO error is a transient WouldBlock or TimedOut.
    fn is_transient_error(e: &std::io::Error) -> bool {
        e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut
    }

    /// Handle a raw read result, updating the buffer and returning what to do next.
    fn interpret_read_result(
        read_buf: &mut Vec<u8>,
        result: std::io::Result<usize>,
        shutdown_flag: &AtomicBool,
        buf: &[u8],
    ) -> Result<ReadStep> {
        match result {
            Ok(0) => Self::handle_eof(read_buf),
            Ok(n) => {
                read_buf.extend_from_slice(&buf[..n]);
                Ok(ReadStep::Continue)
            }
            Err(e) if Self::is_transient_error(&e) => {
                Ok(Self::handle_transient_error(shutdown_flag))
            }
            Err(e) => Err(anyhow::anyhow!("Read error: {}", e)),
        }
    }

    /// Process one iteration of the read loop, returning what to do next.
    fn read_one_step(
        read_buf: &mut Vec<u8>,
        stream: &mut UnixStream,
        shutdown_flag: &AtomicBool,
    ) -> Result<ReadStep> {
        let mut buf = [0u8; 4096];
        let result = stream.read(&mut buf);
        Self::interpret_read_result(read_buf, result, shutdown_flag, &buf)
    }

    /// Extract and parse a message from the buffer once a complete message is buffered.
    fn extract_message_from_buf(read_buf: &mut Vec<u8>, end: usize) -> Result<JsonRpcRequest> {
        let msg = read_buf[..end].to_vec();
        read_buf.drain(..end);
        let body_start = Self::find_header_body_separator(&msg).unwrap_or(0);
        serde_json::from_slice(&msg[body_start..]).context("Failed to parse JSON-RPC request")
    }

    /// Advance the read loop one step; returns `Some(Ok(v))` if reading should stop.
    fn advance_read_loop(
        read_buf: &mut Vec<u8>,
        stream: &mut UnixStream,
        shutdown_flag: &AtomicBool,
    ) -> Result<Option<Option<JsonRpcRequest>>> {
        match Self::read_one_step(read_buf, stream, shutdown_flag)? {
            ReadStep::Done(v) => Ok(Some(v)),
            ReadStep::Continue => Ok(None),
        }
    }

    fn pop_complete_message(&mut self) -> Result<JsonRpcRequest> {
        let end = self
            .find_message_end()
            .context("expected complete message")?;
        Self::extract_message_from_buf(&mut self.read_buf, end)
    }

    /// Read and parse the next JSON-RPC request.
    ///
    /// Returns `Ok(None)` on true EOF (connection closed) or shutdown.
    /// On read timeout, checks the shutdown flag and retries.
    pub fn read_request(&mut self) -> Result<Option<JsonRpcRequest>> {
        loop {
            if self.find_message_end().is_some() {
                break;
            }
            if let Some(result) =
                Self::advance_read_loop(&mut self.read_buf, &mut self.stream, &self.shutdown_flag)?
            {
                return Ok(result);
            }
        }
        Ok(Some(self.pop_complete_message()?))
    }

    /// Write a JSON-RPC response.
    pub fn write_response(&mut self, response: &JsonRpcResponse) -> Result<()> {
        let json = serde_json::to_vec(response).context("Failed to serialize response")?;

        // Format: "Content-Length: N\r\n\r\n{JSON}"
        self.write_buf.clear();
        self.write_buf.extend_from_slice(b"Content-Length: ");
        self.write_buf
            .extend_from_slice(json.len().to_string().as_bytes());
        self.write_buf.extend_from_slice(b"\r\n\r\n");
        self.write_buf.extend_from_slice(&json);

        self.stream
            .write_all(&self.write_buf)
            .context("Failed to write response")?;
        self.stream.flush().context("Failed to flush")?;
        Ok(())
    }
}

impl<R: BufRead, W: Write> StdioTransport<R, W> {
    /// Create a new stdio transport with the given reader and writer.
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            reader,
            writer,
            running: Arc::new(AtomicBool::new(true)),
        }
    }
}

impl StdioTransport<std::io::StdinLock<'static>, std::io::StdoutLock<'static>> {
    /// Create a new stdio transport with the default stdin/stdout.
    pub fn with_default_stdio() -> Self {
        Self::new(std::io::stdin().lock(), std::io::stdout().lock())
    }
}

impl<R: BufRead, W: Write> StdioTransport<R, W> {
    /// Check if the transport is still running.
    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    /// Signal the transport to stop.
    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst)
    }

    /// Parse the Content-Length value from a header line, if present.
    fn parse_content_length_from_line(line: &str) -> Result<Option<usize>> {
        let trimmed = line.trim();
        if !trimmed.starts_with("Content-Length:") {
            return Ok(None);
        }
        let len_str = trimmed.trim_start_matches("Content-Length:").trim();
        Ok(Some(
            len_str
                .parse::<usize>()
                .context("Invalid Content-Length value")?,
        ))
    }

    /// Read and parse Content-Length headers from stdin, returning the body length.
    ///
    /// Returns `Ok(None)` on EOF before any data, `Ok(Some(len))` on success.
    fn read_content_length(&mut self) -> Result<Option<usize>> {
        let mut cl: Option<usize> = None;
        loop {
            cl = match self.read_next_header_line()? {
                HeaderLineResult::Eof => return Ok(None),
                HeaderLineResult::EndOfHeaders => break,
                HeaderLineResult::ContentLength(len) => Some(len),
                HeaderLineResult::Other => cl,
            };
        }
        cl.context("Missing Content-Length header").map(Some)
    }

    fn parse_line_as_header(line: &str) -> Result<HeaderLineResult> {
        Ok(match Self::parse_content_length_from_line(line)? {
            Some(len) => HeaderLineResult::ContentLength(len),
            None => HeaderLineResult::Other,
        })
    }

    fn read_next_header_line(&mut self) -> Result<HeaderLineResult> {
        let mut line = String::new();
        let n = self
            .reader
            .read_line(&mut line)
            .context("Failed to read from stdin")?;
        if n == 0 {
            return Ok(HeaderLineResult::Eof);
        }
        if line.trim().is_empty() {
            return Ok(HeaderLineResult::EndOfHeaders);
        }
        Self::parse_line_as_header(&line)
    }

    /// Read the next JSON-RPC request from stdin.
    ///
    /// Returns `None` if EOF is reached (agent exited).
    pub fn read_request(&mut self) -> Result<Option<JsonRpcRequest>> {
        let content_length = match self.read_content_length()? {
            None => return Ok(None),
            Some(n) => n,
        };
        let mut json_buffer = vec![0u8; content_length];
        self.reader
            .read_exact(&mut json_buffer)
            .context("Failed to read JSON content")?;
        let request: JsonRpcRequest =
            serde_json::from_slice(&json_buffer).context("Failed to parse JSON-RPC request")?;
        Ok(Some(request))
    }

    /// Write a JSON-RPC response to stdout.
    pub fn write_response(&mut self, response: &JsonRpcResponse) -> Result<()> {
        let json = serde_json::to_vec(response).context("Failed to serialize response")?;

        // MCP spec: Content-Length header followed by \r\n\r\n then body
        write!(self.writer, "Content-Length: {}\r\n\r\n", json.len())
            .context("Failed to write Content-Length header")?;

        self.writer
            .write_all(&json)
            .context("Failed to write JSON content")?;

        self.writer.flush().context("Failed to flush output")?;

        Ok(())
    }

    /// Write a JSON-RPC notification (no id) to stdout.
    pub fn write_notification(
        &mut self,
        method: &str,
        params: Option<serde_json::Value>,
    ) -> Result<()> {
        let _ = method;
        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            result: params,
            error: None,
            id: serde_json::Value::Null,
        };

        let json = serde_json::to_vec(&response).context("Failed to serialize notification")?;

        // MCP spec: Content-Length header followed by \r\n\r\n then body
        write!(self.writer, "Content-Length: {}\r\n\r\n", json.len())
            .context("Failed to write Content-Length header")?;

        self.writer
            .write_all(&json)
            .context("Failed to write JSON content")?;

        self.writer.flush().context("Failed to flush output")?;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_content_length_parsing() {
        let header = "Content-Length: 123";
        let len_str = header.trim_start_matches("Content-Length:").trim();
        let len: usize = len_str.parse().unwrap();
        assert_eq!(len, 123);
    }

    #[test]
    fn test_find_header_body_separator_crlf() {
        let data = b"Content-Length: 5\r\n\r\nhello";
        let pos = McpStream::find_header_body_separator(data);
        assert_eq!(pos, Some(21));
        assert_eq!(&data[21..], b"hello");
    }

    #[test]
    fn test_find_header_body_separator_lf() {
        let data = b"Content-Length: 5\n\nhello";
        let pos = McpStream::find_header_body_separator(data);
        assert_eq!(pos, Some(19));
        assert_eq!(&data[19..], b"hello");
    }

    #[test]
    fn test_find_header_body_separator_incomplete() {
        let data = b"Content-Length: 5\r\n";
        assert_eq!(McpStream::find_header_body_separator(data), None);
    }

    #[test]
    fn test_stdio_write_response_uses_crlf_framing() {
        let mut buf = Vec::new();
        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            result: Some(serde_json::json!({"ok": true})),
            error: None,
            id: serde_json::Value::Number(1.into()),
        };
        let reader = std::io::BufReader::new(std::io::empty());
        let mut transport = StdioTransport::new(reader, &mut buf);
        transport.write_response(&response).unwrap();
        let output = String::from_utf8(buf).unwrap();
        assert!(output.starts_with("Content-Length: "));
        assert!(output.contains("\r\n\r\n"));
        // Verify body starts right after \r\n\r\n
        let sep = output.find("\r\n\r\n").unwrap();
        let body = &output[sep + 4..];
        let parsed: serde_json::Value = serde_json::from_str(body).unwrap();
        assert_eq!(parsed["result"]["ok"], true);
    }
}
