//! Fake transport for deterministic testing.
//!
//! This module provides an in-memory fake transport that implements the [`McpStream`]
//! trait for use in unit tests. It allows test harnesses to inject pre-formed
//! JSON-RPC messages and capture responses without any real I/O.
//!
//! # Usage
//!
//! ```ignore
//! use mcp_server::io::fake::FakeTransport;
//! use mcp_server::io::McpStream;
//!
//! let mut transport = FakeTransport::new();
//! transport.inject_request(request);
//! let response = transport.read_response().unwrap();
//! ```

use crate::io::transport::{McpStream, TransportError};
use crate::protocol::{JsonRpcRequest, JsonRpcResponse};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

/// A fake in-memory transport for deterministic testing.
///
/// Provides an in-memory bidirectional channel for JSON-RPC request/response
/// testing without real I/O. Requests are injected by the test harness,
/// and responses can be read back.
#[derive(Debug, Clone)]
pub struct FakeTransport {
    inner: Arc<Mutex<FakeTransportInner>>,
}

#[derive(Debug)]
struct FakeTransportInner {
    /// Queue of requests to be "received" by the server side.
    request_queue: VecDeque<JsonRpcRequest>,
    /// Queue of responses to be "sent" back to the client.
    response_queue: VecDeque<JsonRpcResponse>,
    /// Whether the transport has been closed.
    closed: bool,
}

impl FakeTransport {
    /// Create a new empty fake transport.
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(FakeTransportInner {
                request_queue: VecDeque::new(),
                response_queue: VecDeque::new(),
                closed: false,
            })),
        }
    }

    /// Create a fake transport with a pre-injected request.
    pub fn with_request(request: JsonRpcRequest) -> Self {
        let transport = Self::new();
        transport.inject_request(request);
        transport
    }

    /// Create a fake transport with a pre-injected request and expected response.
    pub fn with_request_response(request: JsonRpcRequest, response: JsonRpcResponse) -> Self {
        let transport = Self::new();
        transport.inject_request(request);
        transport.expect_response(response);
        transport
    }

    /// Inject a request into the transport (simulates receiving from client).
    pub fn inject_request(&self, request: JsonRpcRequest) {
        let mut inner = self.inner.lock().unwrap();
        inner.request_queue.push_back(request);
    }

    /// Queue an expected response (simulates what the server would send).
    pub fn expect_response(&self, response: JsonRpcResponse) {
        let mut inner = self.inner.lock().unwrap();
        inner.response_queue.push_back(response);
    }

    /// Check if there are pending responses.
    pub fn has_pending_responses(&self) -> bool {
        let inner = self.inner.lock().unwrap();
        !inner.response_queue.is_empty()
    }

    /// Get the number of pending requests.
    pub fn pending_request_count(&self) -> usize {
        let inner = self.inner.lock().unwrap();
        inner.request_queue.len()
    }

    /// Close the transport.
    pub fn close(&self) {
        let mut inner = self.inner.lock().unwrap();
        inner.closed = true;
    }
}

impl Default for FakeTransport {
    fn default() -> Self {
        Self::new()
    }
}

impl McpStream for FakeTransport {
    fn read_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError> {
        let mut inner = self.inner.lock().unwrap();
        if inner.closed {
            return Ok(None);
        }
        Ok(inner.request_queue.pop_front())
    }

    fn write_response(&mut self, response: &JsonRpcResponse) -> Result<(), TransportError> {
        let mut inner = self.inner.lock().unwrap();
        if inner.closed {
            return Err(TransportError::ConnectionClosed);
        }
        inner.response_queue.push_back(response.clone());
        Ok(())
    }
}

/// A fake transport pair for testing bidirectional communication.
///
/// Provides two connected transports where anything written to one
/// can be read from the other. Useful for testing full request/response cycles.
#[derive(Debug, Clone)]
pub struct FakeTransportPair {
    /// Transport A (client side).
    pub client: FakeTransport,
    /// Transport B (server side).
    pub server: FakeTransport,
}

impl FakeTransportPair {
    /// Create a new connected transport pair.
    ///
    /// Note: The current implementation creates two independent transports.
    /// Full bidirectional wiring would require a more sophisticated approach.
    pub fn new() -> Self {
        Self {
            client: FakeTransport::new(),
            server: FakeTransport::new(),
        }
    }

    /// Check if both transports are still open.
    pub fn is_connected(&self) -> bool {
        let client = self.client.inner.lock().unwrap();
        let server = self.server.inner.lock().unwrap();
        !client.closed && !server.closed
    }
}

impl Default for FakeTransportPair {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{JsonRpcRequest, JsonRpcResponse};
    use serde_json::json;

    #[test]
    fn test_fake_transport_inject_and_read() {
        let mut transport = FakeTransport::new();

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "tools/list".to_string(),
            params: None,
            id: Some(json!(1)),
        };

        transport.inject_request(request.clone());

        let read = transport.read_request().unwrap();
        assert!(read.is_some());
        assert_eq!(read.unwrap().method, "tools/list");
    }

    #[test]
    fn test_fake_transport_write_and_read_response() {
        let mut transport = FakeTransport::new();

        let response = JsonRpcResponse::success(json!({"tools": []}), json!(1));
        transport.write_response(&response).unwrap();

        let inner = transport.inner.lock().unwrap();
        assert_eq!(inner.response_queue.len(), 1);
        let stored = &inner.response_queue[0];
        assert!(stored.result.is_some());
    }

    #[test]
    fn test_fake_transport_close() {
        let mut transport = FakeTransport::new();
        transport.close();

        let result = transport.read_request().unwrap();
        assert!(result.is_none());

        let response = JsonRpcResponse::success(json!({}), json!(1));
        let result = transport.write_response(&response);
        assert!(result.is_err());
    }

    #[test]
    fn test_fake_transport_empty_on_creation() {
        let mut transport = FakeTransport::new();
        let read = transport.read_request().unwrap();
        assert!(read.is_none());
    }

    #[test]
    fn test_fake_transport_multiple_requests() {
        let mut transport = FakeTransport::new();

        let req1 = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "ping".to_string(),
            params: None,
            id: Some(json!(1)),
        };
        let req2 = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "tools/list".to_string(),
            params: None,
            id: Some(json!(2)),
        };

        transport.inject_request(req1);
        transport.inject_request(req2);

        let read1 = transport.read_request().unwrap().unwrap();
        let read2 = transport.read_request().unwrap().unwrap();

        assert_eq!(read1.method, "ping");
        assert_eq!(read2.method, "tools/list");
    }
}
