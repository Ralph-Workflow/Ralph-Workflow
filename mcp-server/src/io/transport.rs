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
//! | [`TcpLoopbackTransport`] | TCP loopback server for local agents |
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
use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
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
    /// Header bytes exceeded the configured transport header limit.
    #[error("Header too large: {actual} bytes (max {max})")]
    HeaderTooLarge {
        /// Observed header size in bytes.
        actual: usize,
        /// Maximum allowed header size in bytes.
        max: usize,
    },
    /// Declared `Content-Length` exceeded the configured transport body limit.
    #[error("Body too large: {actual} bytes (max {max})")]
    BodyTooLarge {
        /// Declared content length in bytes.
        actual: usize,
        /// Maximum allowed body size in bytes.
        max: usize,
    },
}

const MAX_HEADER_BYTES: usize = 16 * 1024;
const MAX_BODY_BYTES: usize = 8 * 1024 * 1024;

/// Metadata for the currently bound MCP endpoint.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EndpointLease {
    /// Endpoint URI (e.g., tcp://127.0.0.1:12345)
    pub endpoint: String,
    /// Run identifier associated with this lease.
    pub run_id: String,
    /// Monotonically increasing generation counter.
    pub generation: u32,
    /// UTC ready timestamp as seconds since UNIX_EPOCH.
    pub ready_at: u64,
}

impl EndpointLease {
    /// Create a new lease with the given parameters.
    pub fn new(endpoint: String, run_id: String, generation: u32, ready_at: SystemTime) -> Self {
        let ready_at = ready_at
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        Self {
            endpoint,
            run_id,
            generation,
            ready_at,
        }
    }

    /// Return the ready timestamp as `SystemTime`.
    pub fn ready_at_system_time(&self) -> SystemTime {
        UNIX_EPOCH + Duration::from_secs(self.ready_at)
    }
}

impl fmt::Display for EndpointLease {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} (run_id={}, generation={}, ready_at={})",
            self.endpoint, self.run_id, self.generation, self.ready_at
        )
    }
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
/// Claude Code only supports stdio transport, not direct TCP listener connections.
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
// TCP Loopback Transport
// ---------------------------------------------------------------------------

/// TCP MCP transport for local agent connections.
///
/// Creates a non-blocking localhost TCP server. Each accepted connection
/// gets its own `McpStream` for reading/writing JSON-RPC messages.
pub struct TcpLoopbackTransport {
    listener: TcpListener,
    local_addr: SocketAddr,
    shutdown_flag: Arc<AtomicBool>,
}

impl TcpLoopbackTransport {
    fn normalize_accept_result(
        result: Result<(TcpStream, SocketAddr), std::io::Error>,
    ) -> Result<Option<TcpStream>, TransportError> {
        result
            .map(|(stream, _)| Some(stream))
            .or_else(|error| match error.kind() {
                std::io::ErrorKind::WouldBlock => Ok(None),
                _ => Err(error.into()),
            })
    }

    fn finalize_accepted_stream(stream: TcpStream) -> Result<McpStreamImpl, TransportError> {
        stream.set_read_timeout(Some(Duration::from_millis(500)))?;
        Ok(McpStreamImpl::new(stream))
    }

    /// Create a new localhost TCP transport on an ephemeral port.
    pub fn new(shutdown_flag: Arc<AtomicBool>) -> Result<Self, TransportError> {
        let listener = TcpListener::bind(("127.0.0.1", 0))?;
        listener.set_nonblocking(true)?;
        let local_addr = listener.local_addr()?;

        Ok(Self {
            listener,
            local_addr,
            shutdown_flag,
        })
    }

    /// Get the bound local address.
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }

    /// Accept a new client connection.
    ///
    /// Returns `Ok(None)` if no connection is ready (non-blocking).
    /// Returns `Err(TransportError::Shutdown)` if shutdown is signaled.
    pub fn accept(&mut self) -> Result<Option<McpStreamImpl>, TransportError> {
        if self.shutdown_flag.load(Ordering::Relaxed) {
            return Err(TransportError::Shutdown);
        }

        let accepted = Self::normalize_accept_result(self.listener.accept())?;
        accepted.map(Self::finalize_accepted_stream).transpose()
    }
}

/// Concrete McpStream implementation for local TCP sockets.
pub struct McpStreamImpl {
    stream: TcpStream,
}

unsafe impl Send for McpStreamImpl {}
unsafe impl Sync for McpStreamImpl {}

impl McpStreamImpl {
    /// Create a new MCP stream from a TCP stream.
    pub fn new(stream: TcpStream) -> Self {
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
    if content_length > MAX_BODY_BYTES {
        return Err(TransportError::BodyTooLarge {
            actual: content_length,
            max: MAX_BODY_BYTES,
        });
    }

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
    let header_bytes = read_headers_until_blank_line_from_reader(reader)?;
    if header_bytes.is_empty() {
        return Ok(None);
    }

    parse_content_length_from_bytes(&header_bytes).map(Some)
}

/// Parse Content-Length value from a header line, if present.
fn parse_content_length_line(line: &str) -> Option<usize> {
    let trimmed = line.trim();
    let len_str = trimmed.strip_prefix("Content-Length:")?.trim();
    len_str.parse().ok()
}

fn enforce_content_length_limit(
    content_length: Option<usize>,
) -> Result<Option<usize>, TransportError> {
    match content_length {
        Some(length) if length > MAX_BODY_BYTES => Err(TransportError::BodyTooLarge {
            actual: length,
            max: MAX_BODY_BYTES,
        }),
        _ => Ok(content_length),
    }
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

/// Read a framed JSON-RPC message from a TCP stream.
///
/// TcpStream doesn't implement BufRead, so we read byte-by-byte until we have
/// the complete headers, then read the body.
fn read_framed_jsonrpc_from_stream(
    stream: &mut TcpStream,
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
fn read_one_byte(stream: &mut TcpStream) -> Result<Option<u8>, TransportError> {
    let mut buf = [0u8; 1];
    match stream.read(&mut buf) {
        Ok(0) => Ok(None), // EOF
        Ok(1) => Ok(Some(buf[0])),
        Ok(_) => unreachable!(),
        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Err(TransportError::Io(e)),
        Err(e) => Err(e.into()),
    }
}

fn read_headers_until_blank_line_from_reader<R: BufRead>(
    reader: &mut R,
) -> Result<Vec<u8>, TransportError> {
    let mut header = Vec::new();
    let mut line = Vec::new();

    loop {
        line.clear();
        let read_limit = MAX_HEADER_BYTES
            .saturating_sub(header.len())
            .saturating_add(1);
        let mut limited = reader.take(read_limit as u64);
        let bytes = limited.read_until(b'\n', &mut line)?;
        if let Some(done) = advance_header_read(&mut header, &line, bytes)? {
            return done;
        }
    }
}

fn advance_header_read(
    header: &mut Vec<u8>,
    line: &[u8],
    bytes_read: usize,
) -> Result<Option<Result<Vec<u8>, TransportError>>, TransportError> {
    match (bytes_read, header.is_empty()) {
        (0, true) => Ok(Some(Ok(header.clone()))),
        (0, false) => Ok(Some(Err(TransportError::ConnectionClosed))),
        _ => {
            append_header_line(header, line)?;
            Ok(header.ends_with(b"\r\n\r\n").then(|| Ok(header.clone())))
        }
    }
}

fn append_header_line(header: &mut Vec<u8>, line: &[u8]) -> Result<(), TransportError> {
    header.extend_from_slice(line);
    if header.len() > MAX_HEADER_BYTES {
        return Err(TransportError::HeaderTooLarge {
            actual: header.len(),
            max: MAX_HEADER_BYTES,
        });
    }
    Ok(())
}

/// Read headers until blank line by reading byte-by-byte.
///
/// This avoids BufReader which can have unexpected behavior with non-blocking
/// local TCP streams when the stream is empty.
fn read_headers_until_blank_line(stream: &mut TcpStream) -> Result<Vec<u8>, TransportError> {
    let mut header = Vec::new();
    loop {
        let byte = read_one_byte(stream)?.ok_or(TransportError::ConnectionClosed)?;
        header.push(byte);
        if header.len() > MAX_HEADER_BYTES {
            return Err(TransportError::HeaderTooLarge {
                actual: header.len(),
                max: MAX_HEADER_BYTES,
            });
        }
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
            return enforce_content_length_limit(Some(len)).and_then(|value| {
                value.ok_or_else(|| {
                    TransportError::InvalidContentLength(
                        "Content-Length header not found".to_string(),
                    )
                })
            });
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
    stream: &mut TcpStream,
    content_length: usize,
) -> Result<Vec<u8>, TransportError> {
    if content_length > MAX_BODY_BYTES {
        return Err(TransportError::BodyTooLarge {
            actual: content_length,
            max: MAX_BODY_BYTES,
        });
    }

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

/// Write a framed JSON-RPC response to a TCP stream.
fn write_framed_jsonrpc_to_stream(
    stream: &mut TcpStream,
    response: &JsonRpcResponse,
) -> Result<(), TransportError> {
    let body = serde_json::to_vec(response)?;
    let len = body.len();

    write!(stream, "Content-Length: {}\r\n\r\n", len)?;
    stream.write_all(&body)?;
    stream.flush()?;

    Ok(())
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
    fn tcp_transport_binds_localhost_ephemeral_port() {
        let transport = TcpLoopbackTransport::new(Arc::new(AtomicBool::new(false)))
            .expect("transport should bind localhost tcp");
        assert_eq!(transport.local_addr().ip().to_string(), "127.0.0.1");
        assert!(transport.local_addr().port() > 0);
    }

    #[test]
    fn test_read_framed_jsonrpc_rejects_oversized_content_length() {
        let input = format!(
            "Content-Length: {}\r\n\r\n",
            MAX_BODY_BYTES.saturating_add(1)
        );
        let mut cursor = Cursor::new(input.into_bytes());

        let result = read_framed_jsonrpc(&mut cursor);
        assert!(matches!(
            result,
            Err(TransportError::BodyTooLarge {
                actual: _,
                max: MAX_BODY_BYTES
            })
        ));
    }

    #[test]
    fn test_read_content_length_header_rejects_oversized_header() {
        let long_header_value = "x".repeat(MAX_HEADER_BYTES.saturating_add(1));
        let frame = format!("X-Long: {long_header_value}\r\nContent-Length: 2\r\n\r\n{{}}");
        let mut cursor = Cursor::new(frame.into_bytes());

        let result = read_content_length_header(&mut cursor);
        assert!(matches!(
            result,
            Err(TransportError::HeaderTooLarge {
                actual: _,
                max: MAX_HEADER_BYTES
            })
        ));
    }

    #[test]
    fn test_read_content_length_header_stops_reading_at_header_limit() {
        let long_header_value = "x".repeat(MAX_HEADER_BYTES.saturating_add(8 * 1024));
        let frame = format!("X-Long: {long_header_value}\r\nContent-Length: 2\r\n\r\n{{}}");
        let mut cursor = Cursor::new(frame.into_bytes());

        let result = read_content_length_header(&mut cursor);
        assert!(matches!(
            result,
            Err(TransportError::HeaderTooLarge {
                actual: _,
                max: MAX_HEADER_BYTES
            })
        ));

        assert!(
            cursor.position() <= (MAX_HEADER_BYTES.saturating_add(1)) as u64,
            "reader consumed too many bytes before limit check: {}",
            cursor.position()
        );
    }
}
