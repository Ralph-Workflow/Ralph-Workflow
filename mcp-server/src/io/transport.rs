//! MCP transport layer for Content-Length framed JSON-RPC messaging.
//!
//! This module is the I/O boundary for MCP communication. All transport code
//! lives here, making it the only module where actual I/O effects occur.
//!
//! # Transport Types
//!
//! | Type | Description |
//! |------|-------------|
//! | [`StdioTransport`] | Reads from stdin, writes to stdout (for Claude Code) |
//! | [`UnixSocketTransport`] | Unix domain socket server for local agents |
//! | [`McpStream`] | Per-connection stream after accept |
//!
//! # Framing Protocol
//!
//! MCP uses Content-Length framing (similar to HTTP). The format is:
//! ```text
//! Content-Length: <byte-count>
//!
//! <JSON body>
//! ```
//!
//! The JSON body is a JSON-RPC 2.0 request or response object.

use crate::protocol::{JsonRpcRequest, JsonRpcResponse};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::{SocketAddr, UnixListener, UnixStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use thiserror::Error;

/// Errors that can occur during MCP transport operations.
#[derive(Error, Debug)]
pub enum TransportError {
    /// Underlying I/O error (read/write failure).
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    /// JSON serialization/deserialization failed.
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    /// Content-Length header was missing or malformed.
    #[error("Invalid Content-Length header: {0}")]
    InvalidContentLength(String),
    /// The connection was closed by the peer.
    #[error("Connection closed")]
    ConnectionClosed,
    /// Timed out while waiting for a connection.
    #[error("Timeout waiting for connection")]
    Timeout,
    /// Socket was shut down gracefully.
    #[error("Socket shutdown requested")]
    Shutdown,
}

/// Trait for MCP transport streams.
///
/// Implementors handle reading JSON-RPC requests and writing responses
/// with Content-Length framing.
pub trait McpStream: Send + Sync {
    /// Read a JSON-RPC request from the transport.
    /// Returns `Ok(None)` on EOF.
    fn read_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError>;

    /// Write a JSON-RPC response to the transport.
    fn write_response(&mut self, response: &JsonRpcResponse) -> Result<(), TransportError>;
}

// ---------------------------------------------------------------------------
// Stdio Transport
// ---------------------------------------------------------------------------

/// MCP transport using stdin/stdout.
///
/// Used when Ralph is spawned as a child process by Claude Code.
/// Claude Code only supports stdio transport, not Unix sockets.
pub struct StdioTransport {
    reader: BufReader<std::io::Stdin>,
    writer: std::io::Stdout,
}

impl StdioTransport {
    /// Create a new stdio transport using the default stdin/stdout.
    pub fn with_default_stdio() -> Self {
        Self {
            reader: BufReader::new(std::io::stdin()),
            writer: std::io::stdout(),
        }
    }
}

impl McpStream for StdioTransport {
    fn read_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError> {
        read_framed_jsonrpc(&mut self.reader)
    }

    fn write_response(&mut self, response: &JsonRpcResponse) -> Result<(), TransportError> {
        write_framed_jsonrpc(&mut self.writer, response)
    }
}

// ---------------------------------------------------------------------------
// Unix Socket Transport
// ---------------------------------------------------------------------------

/// Unix socket MCP transport for local agent connections.
///
/// Creates a non-blocking Unix domain socket server. Each accepted connection
/// gets its own `McpStream` for reading/writing JSON-RPC messages.
pub struct UnixSocketTransport {
    listener: UnixListener,
    socket_path: PathBuf,
    shutdown_flag: Arc<AtomicBool>,
}

impl UnixSocketTransport {
    /// Create a new Unix socket transport at the given path.
    ///
    /// # Arguments
    ///
    /// * `socket_path` - Path for the Unix socket
    /// * `shutdown_flag` - Atomic flag to signal shutdown
    pub fn new(
        socket_path: PathBuf,
        shutdown_flag: Arc<AtomicBool>,
    ) -> Result<Self, TransportError> {
        // Remove existing socket file if present
        if socket_path.exists() {
            std::fs::remove_file(&socket_path)?;
        }

        let listener = UnixListener::bind(&socket_path)?;
        listener.set_nonblocking(true)?;

        Ok(Self {
            listener,
            socket_path,
            shutdown_flag,
        })
    }

    /// Get the socket path.
    pub fn socket_path(&self) -> &PathBuf {
        &self.socket_path
    }

    /// Accept a new client connection.
    ///
    /// Returns `Ok(None)` if no connection is ready (non-blocking).
    /// Returns `Err(TransportError::Shutdown)` if shutdown is signaled.
    pub fn accept(&mut self) -> Result<Option<McpStreamImpl>, TransportError> {
        if self.shutdown_flag.load(Ordering::Relaxed) {
            return Err(TransportError::Shutdown);
        }

        match self.listener.accept() {
            Ok((stream, _)) => {
                stream.set_read_timeout(Some(Duration::from_millis(500)))?;
                Ok(Some(McpStreamImpl::new(stream)))
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(e.into()),
        }
    }
}

/// Concrete McpStream implementation for Unix sockets.
pub struct McpStreamImpl {
    stream: UnixStream,
}

unsafe impl Send for McpStreamImpl {}
unsafe impl Sync for McpStreamImpl {}

impl McpStreamImpl {
    /// Create a new MCP stream from a Unix stream.
    pub fn new(stream: UnixStream) -> Self {
        Self { stream }
    }

    /// Get the peer address of the connected socket.
    pub fn peer_address(&self) -> Result<SocketAddr, std::io::Error> {
        self.stream.peer_addr()
    }
}

impl McpStream for McpStreamImpl {
    fn read_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError> {
        // Retry on WouldBlock (EAGAIN on macOS) - this means no data available yet,
        // not an error. The connection is still alive, just need to wait.
        loop {
            match read_framed_jsonrpc_from_stream(&mut self.stream) {
                Err(TransportError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(std::time::Duration::from_micros(100));
                    continue;
                }
                other => break other,
            }
        }
    }

    fn write_response(&mut self, response: &JsonRpcResponse) -> Result<(), TransportError> {
        write_framed_jsonrpc_to_stream(&mut self.stream, response)
    }
}

// ---------------------------------------------------------------------------
// Content-Length Framing Helpers
// ---------------------------------------------------------------------------

/// Read a JSON-RPC request from a buffered reader.
///
/// Handles the Content-Length framing protocol:
/// 1. Read headers until we have a blank line
/// 2. Parse Content-Length header
/// 3. Read exactly that many bytes
/// 4. Parse JSON-RPC request
pub fn read_framed_jsonrpc<R: BufRead>(
    reader: &mut R,
) -> Result<Option<JsonRpcRequest>, TransportError> {
    let content_length = match read_content_length_header(reader)? {
        Some(len) => len,
        None => return Ok(None),
    };
    let body = read_body(reader, content_length)?;
    let request = serde_json::from_slice(&body)?;
    Ok(Some(request))
}

/// Read body bytes of exactly the specified length.
///
/// Uses `read_exact` semantics to ensure all `content_length` bytes are read.
/// If fewer bytes are available, returns `ConnectionClosed`.
fn read_body<R: Read>(reader: &mut R, content_length: usize) -> Result<Vec<u8>, TransportError> {
    let mut body = vec![0u8; content_length];
    reader.read_exact(&mut body).map_err(|e| {
        if e.kind() == std::io::ErrorKind::UnexpectedEof {
            TransportError::ConnectionClosed
        } else {
            e.into()
        }
    })?;
    Ok(body)
}

/// Read and parse Content-Length header from a buffered reader.
fn read_content_length_header<R: BufRead>(reader: &mut R) -> Result<Option<usize>, TransportError> {
    let lines = read_lines_until_blank(reader)?;
    parse_content_length_from_lines(&lines)
}

/// Read lines until blank line is encountered.
fn read_lines_until_blank<R: BufRead>(reader: &mut R) -> Result<Vec<String>, TransportError> {
    let mut lines = Vec::new();
    for line in reader.lines() {
        let line = line.map_err(|_| TransportError::ConnectionClosed)?;
        if line.trim().is_empty() {
            break;
        }
        lines.push(line);
    }
    Ok(lines)
}

/// Parse Content-Length from lines, returning error if header is present but invalid.
fn parse_content_length_from_lines(lines: &[String]) -> Result<Option<usize>, TransportError> {
    let header = lines
        .iter()
        .find(|l| l.trim().starts_with("Content-Length:"));
    match header {
        Some(h) => parse_content_length_line(h.trim())
            .map(Some)
            .ok_or_else(|| {
                TransportError::InvalidContentLength("Invalid Content-Length value".to_string())
            }),
        None => Ok(None),
    }
}

/// Parse Content-Length value from a header line, if present.
fn parse_content_length_line(line: &str) -> Option<usize> {
    let trimmed = line.trim();
    let len_str = trimmed.strip_prefix("Content-Length:")?.trim();
    len_str.parse().ok()
}

/// Write a JSON-RPC response to a writer with Content-Length framing.
fn write_framed_jsonrpc<W: Write>(
    writer: &mut W,
    response: &JsonRpcResponse,
) -> Result<(), TransportError> {
    let body = serde_json::to_vec(response)?;
    let len = body.len();

    write!(writer, "Content-Length: {}\r\n\r\n", len)?;
    writer.write_all(&body)?;
    writer.flush()?;

    Ok(())
}

/// Read a framed JSON-RPC message from a Unix stream.
///
/// UnixStream doesn't implement BufRead, so we read byte-by-byte until we have
/// the complete headers, then read the body.
fn read_framed_jsonrpc_from_stream(
    stream: &mut UnixStream,
) -> Result<Option<JsonRpcRequest>, TransportError> {
    let header_bytes = read_headers_until_blank_line(stream)?;
    if header_bytes.is_empty() {
        return Ok(None);
    }
    let content_length = parse_content_length_from_bytes(&header_bytes)?;
    let body = read_body_from_stream(stream, content_length)?;
    let request = serde_json::from_slice(&body)?;
    Ok(Some(request))
}

/// Read one byte from a non-blocking stream.
///
/// Returns Ok(Some(byte)) on success, Ok(None) on EOF, or error on failure.
/// This helper is used by read_headers_until_blank_line to read byte-by-byte.
fn read_one_byte(stream: &mut UnixStream) -> Result<Option<u8>, TransportError> {
    let mut buf = [0u8; 1];
    match stream.read(&mut buf) {
        Ok(0) => Ok(None), // EOF
        Ok(1) => Ok(Some(buf[0])),
        Ok(_) => unreachable!(),
        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Err(TransportError::Io(e)),
        Err(e) => Err(e.into()),
    }
}

/// Read headers until blank line by reading byte-by-byte.
///
/// This avoids BufReader which can have unexpected behavior with non-blocking
/// Unix sockets when the stream is empty.
fn read_headers_until_blank_line(stream: &mut UnixStream) -> Result<Vec<u8>, TransportError> {
    let mut header = Vec::new();
    loop {
        let byte = read_one_byte(stream)?.ok_or(TransportError::ConnectionClosed)?;
        header.push(byte);
        if header.ends_with(b"\r\n\r\n") {
            return Ok(header);
        }
    }
}

/// Parse Content-Length from header bytes (pure function).
fn parse_content_length_from_bytes(header_bytes: &[u8]) -> Result<usize, TransportError> {
    let headers = String::from_utf8_lossy(header_bytes);
    for line in headers.lines() {
        if let Some(len) = parse_content_length_line(line.trim()) {
            return Ok(len);
        }
    }
    Err(TransportError::InvalidContentLength(
        "Content-Length header not found".to_string(),
    ))
}

/// Read body from stream with exact byte count.
///
/// Uses `read_exact` semantics to ensure all `content_length` bytes are read.
/// If fewer bytes are available, returns `ConnectionClosed`.
fn read_body_from_stream(
    stream: &mut UnixStream,
    content_length: usize,
) -> Result<Vec<u8>, TransportError> {
    let mut body = vec![0u8; content_length];
    stream.read_exact(&mut body).map_err(|e| {
        if e.kind() == std::io::ErrorKind::UnexpectedEof {
            TransportError::ConnectionClosed
        } else {
            e.into()
        }
    })?;
    Ok(body)
}

/// Write a framed JSON-RPC response to a Unix stream.
fn write_framed_jsonrpc_to_stream(
    stream: &mut UnixStream,
    response: &JsonRpcResponse,
) -> Result<(), TransportError> {
    let body = serde_json::to_vec(response)?;
    let len = body.len();

    write!(stream, "Content-Length: {}\r\n\r\n", len)?;
    stream.write_all(&body)?;
    stream.flush()?;

    Ok(())
}

// ---------------------------------------------------------------------------
// Socket Path Helpers
// ---------------------------------------------------------------------------

/// Prepare a socket path in the temp directory.
///
/// Creates the directory structure if needed.
pub fn prepare_socket_path(session_id: &str, nonce: u64) -> PathBuf {
    let temp_dir = std::env::temp_dir();
    let socket_dir = temp_dir.join("ralph-mcp");
    std::fs::create_dir_all(&socket_dir).ok();
    socket_dir.join(format!("{}-{}.sock", session_id, nonce))
}

/// Bind a non-blocking Unix listener and return it with the path.
pub fn bind_nonblocking_listener(path: &PathBuf) -> Result<UnixListener, TransportError> {
    if path.exists() {
        std::fs::remove_file(path)?;
    }

    let listener = UnixListener::bind(path)?;
    listener.set_nonblocking(true)?;
    Ok(listener)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn test_content_length_header_parsing() {
        let input = "Content-Length: 123\r\n\r\n";
        let mut cursor = Cursor::new(input);
        let result = read_content_length_header(&mut cursor).unwrap();
        assert_eq!(result, Some(123));
    }

    #[test]
    fn test_multiple_headers() {
        let input = "Content-Type: application/json\r\nContent-Length: 456\r\n\r\n";
        let mut cursor = Cursor::new(input);
        let result = read_content_length_header(&mut cursor).unwrap();
        assert_eq!(result, Some(456));
    }

    #[test]
    fn test_invalid_content_length() {
        let input = "Content-Length: not-a-number\r\n\r\n";
        let mut cursor = Cursor::new(input);
        let result = read_content_length_header(&mut cursor);
        assert!(matches!(
            result,
            Err(TransportError::InvalidContentLength(_))
        ));
    }

    #[test]
    fn test_prepare_socket_path() {
        let path = prepare_socket_path("test-session", 12345);
        assert!(path.to_string_lossy().contains("ralph-mcp"));
        assert!(path.to_string_lossy().contains("test-session"));
        assert!(path.to_string_lossy().ends_with(".sock"));
    }
}
