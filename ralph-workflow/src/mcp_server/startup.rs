//! MCP server startup for ralph-workflow.
//!
//! This module lives in the boundary layer and provides the canonical entry point
//! for starting an MCP server bound to an `AgentSession`.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::agents::tool_manifest::visible_mcp_tool_names_owned;
use crate::mcp_server::capability_mapping::drain_to_access_mode;
use crate::mcp_server::session_bridge::{SessionBridge, SessionBridgeError};
use crate::workspace::Workspace;
use mcp_server::dispatch::access::AccessMode;
use mcp_server::io::heartbeat::HeartbeatPolicy;
pub use mcp_server::io::{ControlCommand, ControlError};
use mcp_server::protocol::JsonRpcResponse;
use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Start an MCP server for the given agent session.
///
/// Creates a `SessionBridge` configured with the drain-appropriate `AccessMode`
/// and starts it. Returns the started bridge, ready to accept agent connections
/// via the endpoint returned by `bridge.endpoint_uri()`.
///
/// # Errors
///
/// Returns `SessionBridgeError` if the TCP loopback endpoint cannot be bound or the
/// server thread fails to start.
pub fn start_mcp_server_for_session(
    session: AgentSession,
    workspace: Arc<dyn Workspace>,
) -> Result<SessionBridge, SessionBridgeError> {
    let required_tools = visible_mcp_tool_names_owned(session.capabilities());
    let mut bridge = SessionBridge::new(session, workspace);
    let _policy = heartbeat_policy_from_env();
    bridge.start()?;
    let agent_endpoint = bridge.agent_endpoint_uri();
    preflight_mcp_server_tools(
        agent_endpoint.as_str(),
        required_tools
            .iter()
            .map(std::string::String::as_str)
            .collect::<Vec<_>>()
            .as_slice(),
        mcp_preflight_timeout_from_env(),
    )
    .map_err(SessionBridgeError::Transport)?;
    Ok(bridge)
}

fn mcp_preflight_timeout_from_env() -> Duration {
    const DEFAULT_TIMEOUT_MS: u64 = 30_000;
    let timeout_ms = env::var("RALPH_MCP_PREFLIGHT_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(DEFAULT_TIMEOUT_MS)
        .max(1);
    Duration::from_millis(timeout_ms)
}

fn preflight_mcp_server_tools(
    endpoint: &str,
    required_tools: &[&str],
    timeout: Duration,
) -> Result<(), String> {
    if endpoint.starts_with("http://") || endpoint.starts_with("https://") {
        return preflight_http_mcp_server_tools(endpoint, required_tools, timeout);
    }

    let address = parse_tcp_endpoint(endpoint)?;
    run_preflight_loop(endpoint, timeout, |remaining| {
        preflight_tcp_attempt(endpoint, address, required_tools, remaining)
    })
}

fn preflight_http_mcp_server_tools(
    endpoint: &str,
    required_tools: &[&str],
    timeout: Duration,
) -> Result<(), String> {
    let target = parse_http_endpoint(endpoint)?;
    run_preflight_loop(endpoint, timeout, |remaining| {
        preflight_http_attempt(endpoint, target.as_ref(), required_tools, remaining)
    })
}

fn run_preflight_loop<F>(endpoint: &str, timeout: Duration, attempt: F) -> Result<(), String>
where
    F: Fn(Duration) -> Result<(), PreflightError>,
{
    continue_preflight_loop(endpoint, timeout, Instant::now(), None, &attempt)
}

fn continue_preflight_loop<F>(
    endpoint: &str,
    timeout: Duration,
    start: Instant,
    last_error: Option<String>,
    attempt: &F,
) -> Result<(), String>
where
    F: Fn(Duration) -> Result<(), PreflightError>,
{
    if let Some(timeout_error) = preflight_timeout_error(endpoint, timeout, start, last_error) {
        Err(timeout_error)
    } else {
        handle_preflight_attempt_result(
            endpoint,
            timeout,
            start,
            attempt(remaining_budget(start, timeout)),
            attempt,
        )
    }
}

fn preflight_timeout_error(
    endpoint: &str,
    timeout: Duration,
    start: Instant,
    last_error: Option<String>,
) -> Option<String> {
    if start.elapsed() >= timeout {
        Some(last_error.unwrap_or_else(|| {
            format!(
                "MCP preflight timed out for endpoint {} after {:?}",
                endpoint, timeout
            )
        }))
    } else {
        None
    }
}

fn handle_preflight_attempt_result<F>(
    endpoint: &str,
    timeout: Duration,
    start: Instant,
    result: Result<(), PreflightError>,
    attempt: &F,
) -> Result<(), String>
where
    F: Fn(Duration) -> Result<(), PreflightError>,
{
    match result {
        Ok(()) => Ok(()),
        Err(PreflightError::Permanent(error)) => Err(error),
        Err(PreflightError::Retryable(error)) => {
            std::thread::sleep(retry_poll_delay(start, timeout));
            continue_preflight_loop(endpoint, timeout, start, Some(error), attempt)
        }
    }
}

fn preflight_tcp_attempt(
    endpoint: &str,
    address: SocketAddr,
    required_tools: &[&str],
    remaining: Duration,
) -> Result<(), PreflightError> {
    connect_to_endpoint(endpoint, address, remaining).and_then(|stream| {
        list_tools_for_endpoint(stream, io_timeout_budget(remaining))
            .and_then(|available_tools| ensure_required_tools(required_tools, available_tools))
    })
}

fn preflight_http_attempt(
    endpoint: &str,
    target: HttpEndpointTargetRef<'_>,
    required_tools: &[&str],
    remaining: Duration,
) -> Result<(), PreflightError> {
    connect_to_endpoint(endpoint, target.address, remaining).and_then(|stream| {
        list_tools_for_http_endpoint(stream, target, io_timeout_budget(remaining))
            .and_then(|available_tools| ensure_required_tools(required_tools, available_tools))
    })
}

fn connect_to_endpoint(
    endpoint: &str,
    address: SocketAddr,
    remaining: Duration,
) -> Result<TcpStream, PreflightError> {
    TcpStream::connect_timeout(&address, connect_timeout_budget(remaining))
        .map_err(|error| classify_connect_error(endpoint, error))
}

fn classify_connect_error(endpoint: &str, error: std::io::Error) -> PreflightError {
    let message = format!("failed to connect to MCP endpoint {}: {}", endpoint, error);
    if retryable_connect_error_kind(error.kind()) {
        PreflightError::Retryable(message)
    } else {
        PreflightError::Permanent(message)
    }
}

fn ensure_required_tools(
    required_tools: &[&str],
    available_tools: Vec<String>,
) -> Result<(), PreflightError> {
    missing_required_tools(required_tools, &available_tools).map_or(Ok(()), |missing| {
        Err(PreflightError::Permanent(format!(
            "missing required MCP tools: {:?}; available: {:?}",
            missing, available_tools
        )))
    })
}

fn missing_required_tools<'a>(
    required_tools: &'a [&'a str],
    available_tools: &[String],
) -> Option<Vec<&'a str>> {
    let missing: Vec<&str> = required_tools
        .iter()
        .copied()
        .filter(|tool| !available_tools.iter().any(|available| available == tool))
        .collect();
    if missing.is_empty() {
        None
    } else {
        Some(missing)
    }
}

fn connect_timeout_budget(remaining: Duration) -> Duration {
    std::cmp::min(Duration::from_millis(500), remaining)
}

fn io_timeout_budget(remaining: Duration) -> Duration {
    std::cmp::min(Duration::from_secs(2), remaining)
}

fn retry_poll_delay(start: Instant, timeout: Duration) -> Duration {
    std::cmp::min(Duration::from_millis(100), remaining_budget(start, timeout))
}

#[derive(Debug)]
enum PreflightError {
    Retryable(String),
    Permanent(String),
}

fn retryable_connect_error_kind(kind: std::io::ErrorKind) -> bool {
    matches!(
        kind,
        std::io::ErrorKind::ConnectionRefused
            | std::io::ErrorKind::ConnectionReset
            | std::io::ErrorKind::ConnectionAborted
            | std::io::ErrorKind::TimedOut
            | std::io::ErrorKind::WouldBlock
            | std::io::ErrorKind::Interrupted
            | std::io::ErrorKind::NotConnected
            | std::io::ErrorKind::UnexpectedEof
    )
}

fn remaining_budget(start: Instant, timeout: Duration) -> Duration {
    timeout
        .saturating_sub(start.elapsed())
        .max(Duration::from_millis(1))
}

fn parse_tcp_endpoint(endpoint: &str) -> Result<SocketAddr, String> {
    let stripped = endpoint
        .strip_prefix("tcp://")
        .ok_or_else(|| format!("MCP endpoint must use tcp://, got '{endpoint}'"))?;
    stripped
        .parse::<SocketAddr>()
        .map_err(|error| format!("invalid MCP endpoint '{}': {}", endpoint, error))
}

#[derive(Debug, Clone)]
struct HttpEndpointTarget {
    address: SocketAddr,
    host_header: String,
    path: String,
}

impl HttpEndpointTarget {
    fn as_ref(&self) -> HttpEndpointTargetRef<'_> {
        HttpEndpointTargetRef {
            address: self.address,
            host_header: self.host_header.as_str(),
            path: self.path.as_str(),
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct HttpEndpointTargetRef<'a> {
    address: SocketAddr,
    host_header: &'a str,
    path: &'a str,
}

fn parse_http_endpoint(endpoint: &str) -> Result<HttpEndpointTarget, String> {
    let (scheme, remainder) = split_endpoint_scheme(endpoint)?;
    ensure_supported_http_scheme(endpoint, scheme)?;
    let (authority, raw_path) = split_http_authority_and_path(remainder);
    ensure_http_authority(endpoint, authority)?;
    let address = resolve_http_endpoint_address(endpoint, authority)?;

    Ok(HttpEndpointTarget {
        address,
        host_header: authority.to_string(),
        path: raw_path,
    })
}

fn split_endpoint_scheme(endpoint: &str) -> Result<(&str, &str), String> {
    endpoint
        .split_once("://")
        .ok_or_else(|| format!("invalid HTTP MCP endpoint '{}': missing scheme", endpoint))
}

fn ensure_supported_http_scheme(endpoint: &str, scheme: &str) -> Result<(), String> {
    if scheme == "http" {
        Ok(())
    } else {
        Err(format!(
            "unsupported MCP HTTP scheme '{}' for endpoint '{}' (only http:// is supported)",
            scheme, endpoint
        ))
    }
}

fn split_http_authority_and_path(remainder: &str) -> (&str, String) {
    match remainder.split_once('/') {
        Some((host, path)) => (host, format!("/{path}")),
        None => (remainder, "/".to_string()),
    }
}

fn ensure_http_authority(endpoint: &str, authority: &str) -> Result<(), String> {
    if authority.is_empty() {
        Err(format!(
            "invalid HTTP MCP endpoint '{}': missing host:port authority",
            endpoint
        ))
    } else {
        Ok(())
    }
}

fn resolve_http_endpoint_address(endpoint: &str, authority: &str) -> Result<SocketAddr, String> {
    let mut candidates = authority
        .to_socket_addrs()
        .map_err(|error| format!("failed to resolve MCP endpoint '{}': {}", endpoint, error))?;
    candidates
        .find(|addr| addr.is_ipv4())
        .or_else(|| candidates.next())
        .ok_or_else(|| format!("failed to resolve any socket address for '{}'", endpoint))
}

fn list_tools_for_endpoint(
    mut stream: TcpStream,
    io_timeout: Duration,
) -> Result<Vec<String>, PreflightError> {
    configure_stream_timeouts(&stream, io_timeout)?;
    let reader_stream = clone_reader_stream(&stream)?;
    let mut reader = BufReader::new(reader_stream);
    complete_stdio_initialize(&mut stream, &mut reader)?;
    read_tools_list_response(&mut stream, &mut reader, "MCP")
}

fn list_tools_for_http_endpoint(
    mut stream: TcpStream,
    target: HttpEndpointTargetRef<'_>,
    io_timeout: Duration,
) -> Result<Vec<String>, PreflightError> {
    configure_stream_timeouts(&stream, io_timeout)?;
    ensure_http_initialize(&mut stream, target)?;
    let mut tools_stream = reconnect_http_tools_stream(target.address, io_timeout)?;
    read_http_tools_list_response(&mut tools_stream, target)
}

fn configure_stream_timeouts(
    stream: &TcpStream,
    io_timeout: Duration,
) -> Result<(), PreflightError> {
    stream.set_read_timeout(Some(io_timeout)).map_err(|error| {
        PreflightError::Permanent(format!("failed to configure read timeout: {}", error))
    })?;
    stream.set_write_timeout(Some(io_timeout)).map_err(|error| {
        PreflightError::Permanent(format!("failed to configure write timeout: {}", error))
    })
}

fn clone_reader_stream(stream: &TcpStream) -> Result<TcpStream, PreflightError> {
    stream.try_clone().map_err(|error| {
        PreflightError::Permanent(format!("failed to clone MCP socket for read: {}", error))
    })
}

fn complete_stdio_initialize(
    stream: &mut TcpStream,
    reader: &mut BufReader<TcpStream>,
) -> Result<(), PreflightError> {
    let init_response = send_stdio_request(stream, reader, initialize_request())?;
    ensure_no_preflight_error("MCP initialize", init_response.error)?;
    write_jsonrpc_request(stream, &initialized_notification()).map_err(PreflightError::Retryable)
}

fn read_tools_list_response(
    stream: &mut TcpStream,
    reader: &mut BufReader<TcpStream>,
    label: &str,
) -> Result<Vec<String>, PreflightError> {
    let list_response = send_stdio_request(stream, reader, tools_list_request())?;
    ensure_no_preflight_error(&format!("{label} tools/list"), list_response.error)?;
    extract_preflight_tool_names(list_response.result, label)
}

fn send_stdio_request(
    stream: &mut TcpStream,
    reader: &mut BufReader<TcpStream>,
    request: serde_json::Value,
) -> Result<JsonRpcResponse, PreflightError> {
    write_jsonrpc_request(stream, &request).map_err(PreflightError::Retryable)?;
    read_jsonrpc_response(reader).map_err(PreflightError::Retryable)
}

fn ensure_http_initialize(
    stream: &mut TcpStream,
    target: HttpEndpointTargetRef<'_>,
) -> Result<(), PreflightError> {
    let init_response = post_http_jsonrpc(stream, target, initialize_request())
        .map_err(PreflightError::Retryable)?;
    ensure_no_preflight_error("HTTP MCP initialize", init_response.error)
}

fn reconnect_http_tools_stream(
    address: SocketAddr,
    io_timeout: Duration,
) -> Result<TcpStream, PreflightError> {
    let stream = TcpStream::connect_timeout(&address, io_timeout).map_err(|error| {
        PreflightError::Retryable(format!("failed to reconnect for tools/list: {}", error))
    })?;
    configure_stream_timeouts(&stream, io_timeout)?;
    Ok(stream)
}

fn read_http_tools_list_response(
    stream: &mut TcpStream,
    target: HttpEndpointTargetRef<'_>,
) -> Result<Vec<String>, PreflightError> {
    let list_response = post_http_jsonrpc(stream, target, tools_list_request())
        .map_err(PreflightError::Retryable)?;
    ensure_no_preflight_error("HTTP MCP tools/list", list_response.error)?;
    extract_preflight_tool_names(list_response.result, "HTTP MCP")
}

fn ensure_no_preflight_error<T: std::fmt::Debug>(
    label: &str,
    error: Option<T>,
) -> Result<(), PreflightError> {
    error.map_or(Ok(()), |error_value| {
        Err(PreflightError::Permanent(format!(
            "{label} failed: {:?}",
            error_value
        )))
    })
}

fn extract_preflight_tool_names(
    result: Option<serde_json::Value>,
    label: &str,
) -> Result<Vec<String>, PreflightError> {
    let result_value = result.ok_or_else(|| {
        PreflightError::Permanent(format!("{label} tools/list response missing result"))
    })?;
    extract_tool_names(result_value).map_err(PreflightError::Permanent)
}

fn initialize_request() -> serde_json::Value {
    serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1,
    })
}

fn initialized_notification() -> serde_json::Value {
    serde_json::json!({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    })
}

fn tools_list_request() -> serde_json::Value {
    serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 2,
    })
}

fn post_http_jsonrpc(
    stream: &mut TcpStream,
    target: HttpEndpointTargetRef<'_>,
    payload: serde_json::Value,
) -> Result<JsonRpcResponse, String> {
    let body = serde_json::to_vec(&payload)
        .map_err(|error| format!("failed to serialize HTTP MCP request: {}", error))?;
    write!(
        stream,
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        target.path,
        target.host_header,
        body.len()
    )
    .map_err(|error| format!("failed to write HTTP MCP request headers: {}", error))?;
    stream
        .write_all(body.as_slice())
        .map_err(|error| format!("failed to write HTTP MCP request body: {}", error))?;
    stream
        .flush()
        .map_err(|error| format!("failed to flush HTTP MCP request: {}", error))?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| format!("failed to read HTTP MCP response: {}", error))?;

    let header_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| "invalid HTTP MCP response: missing header terminator".to_string())?;
    let header_bytes = &response[..header_end];
    let body_bytes = &response[(header_end + 4)..];
    let headers = String::from_utf8_lossy(header_bytes);
    let status_line = headers.lines().next().unwrap_or_default().to_string();
    if !status_line.contains(" 200 ") {
        return Err(format!(
            "HTTP MCP request failed with status '{}': {}",
            status_line,
            String::from_utf8_lossy(body_bytes)
        ));
    }

    serde_json::from_slice::<JsonRpcResponse>(body_bytes)
        .map_err(|error| format!("failed to parse HTTP MCP response JSON: {}", error))
}

fn write_jsonrpc_request(stream: &mut TcpStream, value: &serde_json::Value) -> Result<(), String> {
    let body = serde_json::to_vec(value)
        .map_err(|error| format!("failed to serialize MCP request: {}", error))?;
    write!(stream, "Content-Length: {}\r\n\r\n", body.len())
        .map_err(|error| format!("failed to write MCP request headers: {}", error))?;
    stream
        .write_all(body.as_slice())
        .map_err(|error| format!("failed to write MCP request body: {}", error))?;
    stream
        .flush()
        .map_err(|error| format!("failed to flush MCP request: {}", error))
}

fn read_jsonrpc_response<R: BufRead>(reader: &mut R) -> Result<JsonRpcResponse, String> {
    let content_length = read_content_length(reader)?;
    let mut body = vec![0_u8; content_length];
    reader
        .read_exact(body.as_mut_slice())
        .map_err(|error| format!("failed to read MCP response body: {}", error))?;
    serde_json::from_slice::<JsonRpcResponse>(body.as_slice())
        .map_err(|error| format!("failed to parse MCP response JSON: {}", error))
}

fn read_content_length<R: BufRead>(reader: &mut R) -> Result<usize, String> {
    read_content_length_recursive(reader, None)
}

fn read_content_length_recursive<R: BufRead>(
    reader: &mut R,
    current: Option<usize>,
) -> Result<usize, String> {
    let line = read_header_line(reader)?;
    resolve_content_length(reader, current, line)
}

fn read_header_line<R: BufRead>(reader: &mut R) -> Result<String, String> {
    let mut line = String::new();
    let read = reader
        .read_line(&mut line)
        .map_err(|error| format!("failed to read MCP response header: {}", error))?;
    if read == 0 {
        Err("MCP response closed while reading headers".to_string())
    } else {
        Ok(line)
    }
}

fn resolve_content_length<R: BufRead>(
    reader: &mut R,
    current: Option<usize>,
    line: String,
) -> Result<usize, String> {
    if line == "\r\n" {
        current.ok_or_else(|| "MCP response missing Content-Length header".to_string())
    } else {
        read_content_length_recursive(
            reader,
            current.or_else(|| parse_content_length_header(&line)),
        )
    }
}

fn parse_content_length_header(line: &str) -> Option<usize> {
    line.strip_prefix("Content-Length:")
        .and_then(|rest| rest.trim().parse::<usize>().ok())
}

fn extract_tool_names(result_value: serde_json::Value) -> Result<Vec<String>, String> {
    let tools = result_value
        .get("tools")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| "MCP tools/list result missing tools array".to_string())?;
    Ok(tools
        .iter()
        .filter_map(|tool| {
            tool.get("name")
                .and_then(serde_json::Value::as_str)
                .map(std::string::ToString::to_string)
        })
        .collect())
}

fn heartbeat_policy_from_env() -> HeartbeatPolicy {
    const DEFAULT_INTERVAL_MS: u64 = 2000;
    const DEFAULT_MISSES: u32 = 3;
    const DEFAULT_RECONNECT_MS: u64 = 10000;

    let interval = env::var("RALPH_MCP_HEARTBEAT_INTERVAL_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_INTERVAL_MS);
    let misses = env::var("RALPH_MCP_HEARTBEAT_MISSES")
        .ok()
        .and_then(|v| v.parse::<u32>().ok())
        .unwrap_or(DEFAULT_MISSES);
    let reconnect_ms = env::var("RALPH_MCP_HEARTBEAT_RECONNECT_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_RECONNECT_MS);

    HeartbeatPolicy::new(
        Duration::from_millis(interval),
        misses.max(1),
        Duration::from_millis(reconnect_ms),
    )
}

/// Pure mapping: determine the MCP `AccessMode` for a session drain.
///
/// Delegates to `mcp_server::capability_mapping::drain_to_access_mode`.
/// Exposed here so callers can inspect the mode without constructing a bridge.
pub fn access_mode_for_drain(drain: SessionDrain) -> AccessMode {
    drain_to_access_mode(drain)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::AgentSession;
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use mcp_server::dispatch::access::AccessMode;
    use std::sync::Arc;
    use std::time::Duration;

    #[test]
    fn planning_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Planning),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn analysis_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Analysis),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn review_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Review),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn fix_drain_is_read_write() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Fix),
            AccessMode::ReadWrite
        );
    }

    #[test]
    fn development_drain_is_read_write() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Development),
            AccessMode::ReadWrite
        );
    }

    #[test]
    fn commit_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Commit),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn preflight_mcp_server_tools_succeeds_for_registered_tool() {
        let session = AgentSession::for_drain(
            "startup-preflight-ok".to_string(),
            SessionDrain::Development,
            1,
        );
        let workspace: Arc<dyn crate::workspace::Workspace> = Arc::new(MemoryWorkspace::new_test());
        let mut bridge = crate::mcp_server::session_bridge::SessionBridge::new(session, workspace);
        assert!(
            bridge.start().is_ok(),
            "bridge must start for preflight test"
        );

        let endpoint = bridge.endpoint_uri();
        let result = preflight_mcp_server_tools(
            endpoint.as_str(),
            &["ralph_submit_artifact"],
            Duration::from_secs(2),
        );
        assert!(
            result.is_ok(),
            "preflight must succeed for registered tool: {result:?}"
        );
    }

    #[test]
    fn preflight_mcp_server_tools_succeeds_for_http_agent_endpoint() {
        let session = AgentSession::for_drain(
            "startup-preflight-http-ok".to_string(),
            SessionDrain::Commit,
            1,
        );
        let workspace: Arc<dyn crate::workspace::Workspace> = Arc::new(MemoryWorkspace::new_test());
        let mut bridge = crate::mcp_server::session_bridge::SessionBridge::new(session, workspace);
        assert!(
            bridge.start().is_ok(),
            "bridge must start for HTTP preflight test"
        );

        let endpoint = bridge.agent_endpoint_uri();
        let result = preflight_mcp_server_tools(
            endpoint.as_str(),
            &["ralph_submit_artifact"],
            Duration::from_secs(2),
        );
        assert!(
            result.is_ok(),
            "preflight must succeed for HTTP agent endpoint: {result:?}"
        );
    }

    #[test]
    fn preflight_mcp_server_tools_fails_for_missing_tool() {
        let session = AgentSession::for_drain(
            "startup-preflight-missing".to_string(),
            SessionDrain::Development,
            1,
        );
        let workspace: Arc<dyn crate::workspace::Workspace> = Arc::new(MemoryWorkspace::new_test());
        let mut bridge = crate::mcp_server::session_bridge::SessionBridge::new(session, workspace);
        assert!(
            bridge.start().is_ok(),
            "bridge must start for preflight test"
        );

        let endpoint = bridge.endpoint_uri();
        let result = preflight_mcp_server_tools(
            endpoint.as_str(),
            &["tool_that_does_not_exist"],
            Duration::from_secs(2),
        );
        assert!(
            result.is_err(),
            "preflight must fail when required tool is absent"
        );
    }

    #[test]
    fn start_mcp_server_for_session_exposes_http_agent_endpoint() {
        let workspace: Arc<dyn crate::workspace::Workspace> = Arc::new(MemoryWorkspace::new_test());
        let session = AgentSession::for_drain(
            "startup-http-endpoint-run".to_string(),
            SessionDrain::Commit,
            1,
        );
        let mut bridge =
            start_mcp_server_for_session(session, workspace).expect("startup should succeed");
        let endpoint = bridge.agent_endpoint_uri();
        assert!(
            endpoint.starts_with("http://127.0.0.1:") && endpoint.ends_with("/mcp"),
            "agent endpoint should be HTTP gateway, got: {endpoint}"
        );
        bridge.shutdown();
    }
}
