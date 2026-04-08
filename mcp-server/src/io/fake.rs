//! Fake transport for deterministic testing.
//!
//! This module provides in-memory fake transports that implement the [`McpStream`]
//! trait for use in unit tests. Two variants are available:
//!
//! - [`FakeTransport`] — standalone single-side transport. The test injects
//!   requests and reads back responses from the same queue. Use this when driving
//!   the server via `McpServer::handle_request()` directly.
//!
//! - [`FakeTransportPair`] — bidirectional pair with shared queues. The client
//!   side (`pair.client`) injects requests and reads responses; the server side
//!   (`pair.server`) implements `McpStream` and is driven by the server loop.
//!   Use this when you need a true transport-level roundtrip.
//!
//! # Standalone Usage
//!
//! ```ignore
//! use mcp_server::io::fake::FakeTransport;
//! use mcp_server::io::McpStream;
//!
//! let mut transport = FakeTransport::new();
//! transport.inject_request(request);
//! let req = transport.read_request().unwrap().unwrap();
//! // process req via server...
//! transport.write_response(&response).unwrap();
//! ```
//!
//! # Bidirectional Pair Usage
//!
//! ```ignore
//! use mcp_server::io::fake::FakeTransportPair;
//! use mcp_server::io::transport::McpStream;
//!
//! let mut pair = FakeTransportPair::new();
//!
//! // Client side: inject a request
//! pair.client.inject_request(request);
//!
//! // Server side: read the request, handle it, write the response
//! let req = pair.server.read_request().unwrap().unwrap();
//! let (resp, _state) = server.handle_request(req, state);
//! pair.server.write_response(&resp.unwrap()).unwrap();
//!
//! // Client side: read the response
//! let client_resp = pair.client.read_response().unwrap();
//! ```

use crate::io::transport::{McpStream, TransportError};
use crate::protocol::{JsonRpcRequest, JsonRpcResponse};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

// ---------------------------------------------------------------------------
// Shared queue state for bidirectional transport pair
// ---------------------------------------------------------------------------

/// Shared bidirectional message queues for [`FakeTransportPair`].
///
/// Both the client and server halves of a pair hold an `Arc` clone of the
/// same `PairQueues`, so messages injected on one side are immediately
/// visible on the other side.
#[derive(Debug, Clone)]
struct PairQueues {
    /// Requests sent from the client side to the server side.
    client_to_server: Arc<Mutex<VecDeque<JsonRpcRequest>>>,
    /// Responses sent from the server side to the client side.
    server_to_client: Arc<Mutex<VecDeque<JsonRpcResponse>>>,
    /// Shared closed flag. Set to `true` by either side calling `close()`.
    closed: Arc<Mutex<bool>>,
}

impl PairQueues {
    fn new() -> Self {
        Self {
            client_to_server: Arc::new(Mutex::new(VecDeque::new())),
            server_to_client: Arc::new(Mutex::new(VecDeque::new())),
            closed: Arc::new(Mutex::new(false)),
        }
    }

    fn is_closed(&self) -> bool {
        *self.closed.lock().unwrap()
    }

    fn close(&self) {
        *self.closed.lock().unwrap() = true;
    }

    /// Read the next request from the client-to-server queue.
    fn pop_request(&self) -> Result<Option<JsonRpcRequest>, TransportError> {
        if self.is_closed() {
            return Ok(None);
        }
        Ok(self.client_to_server.lock().unwrap().pop_front())
    }

    /// Push a response onto the server-to-client queue.
    fn push_response(&self, response: &JsonRpcResponse) -> Result<(), TransportError> {
        if self.is_closed() {
            return Err(TransportError::ConnectionClosed);
        }
        self.server_to_client
            .lock()
            .unwrap()
            .push_back(response.clone());
        Ok(())
    }
}

/// Role of a [`FakeTransport`] inside a [`FakeTransportPair`].
#[derive(Debug, Clone, PartialEq, Eq)]
enum PairRole {
    /// Client half: injects requests, reads back responses.
    Client,
    /// Server half: reads requests via [`McpStream`], writes responses.
    Server,
}

// ---------------------------------------------------------------------------
// Standalone inner state
// ---------------------------------------------------------------------------

#[derive(Debug, Default)]
struct FakeTransportInner {
    request_queue: VecDeque<JsonRpcRequest>,
    response_queue: VecDeque<JsonRpcResponse>,
    closed: bool,
}

impl FakeTransportInner {
    fn pop_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError> {
        if self.closed {
            return Ok(None);
        }
        Ok(self.request_queue.pop_front())
    }

    fn push_response(&mut self, response: &JsonRpcResponse) -> Result<(), TransportError> {
        if self.closed {
            return Err(TransportError::ConnectionClosed);
        }
        self.response_queue.push_back(response.clone());
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// FakeTransport
// ---------------------------------------------------------------------------

/// A fake in-memory transport for deterministic testing.
///
/// When created via [`FakeTransport::new()`] the transport is standalone:
/// the test and the server share the same queue set. When created as part of
/// a [`FakeTransportPair`], the transport's role (client or server) determines
/// which shared queue it reads from and writes to.
///
/// # Standalone Mode
///
/// Both `inject_request()` and `McpStream::read_request()` operate on the same
/// internal queue, allowing the test to inject a request and the server to pop
/// it. `McpStream::write_response()` pushes to the response queue; the test can
/// inspect responses via `read_response()` or by locking `inner` directly.
///
/// # Pair Mode
///
/// The client side uses a shared `client_to_server` queue for requests and a
/// shared `server_to_client` queue for responses. The server side's
/// `McpStream` reads from `client_to_server` and writes to `server_to_client`.
/// This ensures proper bidirectional isolation between the two halves.
#[derive(Debug, Clone)]
pub struct FakeTransport {
    /// Internal state for standalone mode.
    inner: Arc<Mutex<FakeTransportInner>>,
    /// Shared queues when this transport is one half of a `FakeTransportPair`.
    pair: Option<(PairQueues, PairRole)>,
}

impl FakeTransport {
    /// Create a new standalone fake transport with empty queues.
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(FakeTransportInner::default())),
            pair: None,
        }
    }

    /// Create a standalone fake transport with a pre-injected request.
    pub fn with_request(request: JsonRpcRequest) -> Self {
        let transport = Self::new();
        transport.inject_request(request);
        transport
    }

    /// Create a standalone fake transport with a pre-injected request and
    /// a queued expected response.
    pub fn with_request_response(request: JsonRpcRequest, response: JsonRpcResponse) -> Self {
        let transport = Self::new();
        transport.inject_request(request);
        transport.expect_response(response);
        transport
    }

    /// Create the client half of a transport pair.
    ///
    /// The client half exposes [`inject_request()`] and [`read_response()`]
    /// for driving the server from test code.
    fn client_side(queues: PairQueues) -> Self {
        Self {
            inner: Arc::new(Mutex::new(FakeTransportInner::default())),
            pair: Some((queues, PairRole::Client)),
        }
    }

    /// Create the server half of a transport pair.
    ///
    /// The server half implements [`McpStream`]: it reads requests from the
    /// shared `client_to_server` queue and writes responses to the shared
    /// `server_to_client` queue.
    fn server_side(queues: PairQueues) -> Self {
        Self {
            inner: Arc::new(Mutex::new(FakeTransportInner::default())),
            pair: Some((queues, PairRole::Server)),
        }
    }

    /// Inject a request into the transport (simulates a request arriving from
    /// a remote client).
    ///
    /// - Standalone mode: pushes to the internal `request_queue`.
    /// - Client-side pair mode: pushes to the shared `client_to_server` queue
    ///   so the server half can read it via [`McpStream::read_request()`].
    pub fn inject_request(&self, request: JsonRpcRequest) {
        match &self.pair {
            Some((queues, PairRole::Client)) => {
                queues.client_to_server.lock().unwrap().push_back(request);
            }
            _ => {
                self.inner.lock().unwrap().request_queue.push_back(request);
            }
        }
    }

    /// Queue an expected response in the internal response buffer.
    ///
    /// This is a convenience method for standalone tests that pre-load
    /// the response queue before processing. It always uses the internal
    /// `response_queue` regardless of pair mode.
    pub fn expect_response(&self, response: JsonRpcResponse) {
        self.inner
            .lock()
            .unwrap()
            .response_queue
            .push_back(response);
    }

    /// Read a response sent by the server.
    ///
    /// - Standalone mode: pops from the internal `response_queue`.
    /// - Client-side pair mode: pops from the shared `server_to_client` queue.
    ///
    /// Returns `None` if no response is available.
    pub fn read_response(&self) -> Option<JsonRpcResponse> {
        match &self.pair {
            Some((queues, PairRole::Client)) => queues.server_to_client.lock().unwrap().pop_front(),
            _ => self.inner.lock().unwrap().response_queue.pop_front(),
        }
    }

    /// Check if there are pending responses available to read.
    ///
    /// - Standalone mode: checks the internal `response_queue`.
    /// - Client-side pair mode: checks the shared `server_to_client` queue.
    pub fn has_pending_responses(&self) -> bool {
        match &self.pair {
            Some((queues, PairRole::Client)) => !queues.server_to_client.lock().unwrap().is_empty(),
            _ => !self.inner.lock().unwrap().response_queue.is_empty(),
        }
    }

    /// Get the number of pending requests (requests injected but not yet read).
    pub fn pending_request_count(&self) -> usize {
        match &self.pair {
            Some((queues, PairRole::Client)) => queues.client_to_server.lock().unwrap().len(),
            _ => self.inner.lock().unwrap().request_queue.len(),
        }
    }

    /// Close the transport.
    ///
    /// After closing, `read_request()` returns `Ok(None)` and `write_response()`
    /// returns `Err(TransportError::ConnectionClosed)`.
    ///
    /// In pair mode, closing either half sets the shared closed flag so both
    /// sides observe the closure.
    pub fn close(&self) {
        match &self.pair {
            Some((queues, _)) => queues.close(),
            None => {
                self.inner.lock().unwrap().closed = true;
            }
        }
    }
}

impl Default for FakeTransport {
    fn default() -> Self {
        Self::new()
    }
}

impl McpStream for FakeTransport {
    fn read_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError> {
        match &self.pair {
            Some((queues, PairRole::Server)) => queues.pop_request(),
            _ => self.inner.lock().unwrap().pop_request(),
        }
    }

    fn write_response(&mut self, response: &JsonRpcResponse) -> Result<(), TransportError> {
        match &self.pair {
            Some((queues, PairRole::Server)) => queues.push_response(response),
            _ => self.inner.lock().unwrap().push_response(response),
        }
    }
}

// ---------------------------------------------------------------------------
// FakeTransportPair
// ---------------------------------------------------------------------------

/// A bidirectional fake transport pair for testing full request-response cycles.
///
/// The two halves share a pair of queues:
///
/// - `client.inject_request(req)` — sends a request to the server side.
/// - `server.read_request()` — receives that request (via [`McpStream`]).
/// - `server.write_response(&resp)` — sends the response to the client side.
/// - `client.read_response()` — receives that response.
///
/// This allows tests to exercise the full transport layer without a real socket,
/// proving the protocol stack works end-to-end without external I/O.
///
/// # Example
///
/// ```ignore
/// use mcp_server::io::fake::FakeTransportPair;
/// use mcp_server::io::transport::McpStream;
/// use mcp_server::io::ServerState;
///
/// let mut pair = FakeTransportPair::new();
///
/// pair.client.inject_request(initialize_request(1));
///
/// let req = pair.server.read_request().unwrap().unwrap();
/// let (resp, _) = server.handle_request(req, ServerState::Uninitialized);
/// pair.server.write_response(&resp.unwrap()).unwrap();
///
/// let client_resp = pair.client.read_response().unwrap();
/// assert!(client_resp.result.is_some());
/// ```
#[derive(Debug, Clone)]
pub struct FakeTransportPair {
    /// Client half — injects requests and reads responses.
    pub client: FakeTransport,
    /// Server half — implements [`McpStream`] for the MCP server.
    pub server: FakeTransport,
}

impl FakeTransportPair {
    /// Create a new bidirectional transport pair with empty shared queues.
    pub fn new() -> Self {
        let queues = PairQueues::new();
        Self {
            client: FakeTransport::client_side(queues.clone()),
            server: FakeTransport::server_side(queues),
        }
    }

    /// Check if both sides of the pair are still open (not closed).
    pub fn is_connected(&self) -> bool {
        match &self.client.pair {
            Some((queues, _)) => !queues.is_closed(),
            None => false,
        }
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

    // -------------------------------------------------------------------------
    // FakeTransportPair tests
    // -------------------------------------------------------------------------

    #[test]
    fn pair_client_request_reaches_server() {
        let mut pair = FakeTransportPair::new();

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "initialize".to_string(),
            params: Some(json!({"protocolVersion": "2024-11-05"})),
            id: Some(json!(1)),
        };

        pair.client.inject_request(request);

        let received = pair
            .server
            .read_request()
            .expect("read must not error")
            .expect("request must be available");

        assert_eq!(received.method, "initialize");
    }

    #[test]
    fn pair_server_response_reaches_client() {
        let mut pair = FakeTransportPair::new();

        let response = JsonRpcResponse::success(
            json!({"protocolVersion": "2024-11-05", "serverInfo": {"name": "test"}}),
            json!(1),
        );

        pair.server
            .write_response(&response)
            .expect("write must not error");

        let received = pair
            .client
            .read_response()
            .expect("response must be available");
        assert!(received.result.is_some());
    }

    #[test]
    fn pair_bidirectional_roundtrip() {
        let mut pair = FakeTransportPair::new();

        // Client sends a request
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "ping".to_string(),
            params: None,
            id: Some(json!(42)),
        };
        pair.client.inject_request(request);

        // Server reads it and writes a response
        let req = pair
            .server
            .read_request()
            .expect("read must not error")
            .expect("request must be available");
        let response = JsonRpcResponse::success(serde_json::Value::Null, req.id.unwrap());
        pair.server
            .write_response(&response)
            .expect("write must not error");

        // Client reads the response
        let client_resp = pair
            .client
            .read_response()
            .expect("response must be available");
        assert!(client_resp.error.is_none());
        assert_eq!(client_resp.id, json!(42));
    }

    #[test]
    fn pair_close_makes_server_read_return_none() {
        let mut pair = FakeTransportPair::new();
        pair.client.close();

        let result = pair.server.read_request().expect("read must not error");
        assert!(result.is_none(), "closed pair server must return None");
    }

    #[test]
    fn pair_close_makes_server_write_return_error() {
        let mut pair = FakeTransportPair::new();
        pair.client.close();

        let response = JsonRpcResponse::success(json!({}), json!(1));
        let result = pair.server.write_response(&response);
        assert!(result.is_err(), "write on closed pair must return error");
    }

    #[test]
    fn pair_is_connected_reflects_close_state() {
        let pair = FakeTransportPair::new();
        assert!(pair.is_connected(), "new pair must be connected");

        pair.client.close();
        assert!(
            !pair.is_connected(),
            "pair must be disconnected after close"
        );
    }

    #[test]
    fn pair_independent_from_standalone_transport() {
        // Standalone transport queue must not interfere with pair queues
        let mut standalone = FakeTransport::new();
        let mut pair = FakeTransportPair::new();

        let standalone_req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "standalone".to_string(),
            params: None,
            id: Some(json!(100)),
        };
        let pair_req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "paired".to_string(),
            params: None,
            id: Some(json!(200)),
        };

        standalone.inject_request(standalone_req);
        pair.client.inject_request(pair_req);

        let from_standalone = standalone.read_request().unwrap().unwrap();
        let from_pair = pair.server.read_request().unwrap().unwrap();

        assert_eq!(from_standalone.method, "standalone");
        assert_eq!(from_pair.method, "paired");

        // Pair server has no more requests
        let none = pair.server.read_request().unwrap();
        assert!(none.is_none());
    }
}
