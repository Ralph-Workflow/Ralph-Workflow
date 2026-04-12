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
    let start = Instant::now();
    let mut last_error: Option<String> = None;

    while start.elapsed() < timeout {
        let connect_timeout =
            std::cmp::min(Duration::from_millis(500), remaining_budget(start, timeout));
        match TcpStream::connect_timeout(&address, connect_timeout) {
            Ok(stream) => match list_tools_for_endpoint(
                stream,
                std::cmp::min(Duration::from_secs(2), remaining_budget(start, timeout)),
            ) {
                Ok(available_tools) => {
                    let missing: Vec<&str> = required_tools
                        .iter()
                        .copied()
                        .filter(|tool| !available_tools.iter().any(|available| available == tool))
                        .collect();
                    if missing.is_empty() {
                        return Ok(());
                    }
                    return Err(format!(
                        "missing required MCP tools: {:?}; available: {:?}",
                        missing, available_tools
                    ));
                }
                Err(PreflightError::Retryable(error)) => {
                    last_error = Some(error);
                }
                Err(PreflightError::Permanent(error)) => {
                    return Err(error);
                }
            },
            Err(error) => {
                let message = format!("failed to connect to MCP endpoint {}: {}", endpoint, error);
                if retryable_connect_error_kind(error.kind()) {
                    last_error = Some(message);
                } else {
                    return Err(message);
                }
            }
        }

        std::thread::sleep(std::cmp::min(
            Duration::from_millis(100),
            remaining_budget(start, timeout),
        ));
    }

    Err(last_error.unwrap_or_else(|| {
        format!(
            "MCP preflight timed out for endpoint {} after {:?}",
            endpoint, timeout
        )
    }))
}

fn preflight_http_mcp_server_tools(
    endpoint: &str,
    required_tools: &[&str],
    timeout: Duration,
) -> Result<(), String> {
    let target = parse_http_endpoint(endpoint)?;
    let start = Instant::now();
    let mut last_error: Option<String> = None;

    while start.elapsed() < timeout {
        let connect_timeout =
            std::cmp::min(Duration::from_millis(500), remaining_budget(start, timeout));
        match TcpStream::connect_timeout(&target.address, connect_timeout) {
            Ok(stream) => match list_tools_for_http_endpoint(
                stream,
                target.as_ref(),
                std::cmp::min(Duration::from_secs(2), remaining_budget(start, timeout)),
            ) {
                Ok(available_tools) => {
                    let missing: Vec<&str> = required_tools
                        .iter()
                        .copied()
                        .filter(|tool| !available_tools.iter().any(|available| available == tool))
                        .collect();
                    if missing.is_empty() {
                        return Ok(());
                    }
                    return Err(format!(
                        "missing required MCP tools: {:?}; available: {:?}",
                        missing, available_tools
                    ));
                }
                Err(PreflightError::Retryable(error)) => {
                    last_error = Some(error);
                }
                Err(PreflightError::Permanent(error)) => {
                    return Err(error);
                }
            },
            Err(error) => {
                let message = format!("failed to connect to MCP endpoint {}: {}", endpoint, error);
                if retryable_connect_error_kind(error.kind()) {
                    last_error = Some(message);
                } else {
                    return Err(message);
                }
            }
        }

        std::thread::sleep(std::cmp::min(
            Duration::from_millis(100),
            remaining_budget(start, timeout),
        ));
    }

    Err(last_error.unwrap_or_else(|| {
        format!(
            "MCP preflight timed out for endpoint {} after {:?}",
            endpoint, timeout
        )
    }))
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
    let (scheme, remainder) = endpoint
        .split_once("://")
        .ok_or_else(|| format!("invalid HTTP MCP endpoint '{}': missing scheme", endpoint))?;

    if scheme != "http" {
        return Err(format!(
            "unsupported MCP HTTP scheme '{}' for endpoint '{}' (only http:// is supported)",
            scheme, endpoint
        ));
    }

    let (authority, raw_path) = match remainder.split_once('/') {
        Some((host, path)) => (host, format!("/{path}")),
        None => (remainder, "/".to_string()),
    };

    if authority.is_empty() {
        return Err(format!(
            "invalid HTTP MCP endpoint '{}': missing host:port authority",
            endpoint
        ));
    }

    let mut candidates = authority
        .to_socket_addrs()
        .map_err(|error| format!("failed to resolve MCP endpoint '{}': {}", endpoint, error))?;
    let address = candidates
        .find(|addr| addr.is_ipv4())
        .or_else(|| candidates.next())
        .ok_or_else(|| format!("failed to resolve any socket address for '{}'", endpoint))?;

    Ok(HttpEndpointTarget {
        address,
        host_header: authority.to_string(),
        path: raw_path,
    })
}

fn list_tools_for_endpoint(
    mut stream: TcpStream,
    io_timeout: Duration,
) -> Result<Vec<String>, PreflightError> {
    stream.set_read_timeout(Some(io_timeout)).map_err(|error| {
        PreflightError::Permanent(format!("failed to configure read timeout: {}", error))
    })?;
    stream
        .set_write_timeout(Some(io_timeout))
        .map_err(|error| {
            PreflightError::Permanent(format!("failed to configure write timeout: {}", error))
        })?;

    let reader_stream = stream.try_clone().map_err(|error| {
        PreflightError::Permanent(format!("failed to clone MCP socket for read: {}", error))
    })?;
    let mut reader = BufReader::new(reader_stream);

    let initialize = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1,
    });
    write_jsonrpc_request(&mut stream, &initialize).map_err(PreflightError::Retryable)?;
    let init_response = read_jsonrpc_response(&mut reader).map_err(PreflightError::Retryable)?;
    if init_response.error.is_some() {
        return Err(PreflightError::Permanent(format!(
            "MCP initialize failed: {:?}",
            init_response.error
        )));
    }

    let initialized_notification = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    });
    write_jsonrpc_request(&mut stream, &initialized_notification)
        .map_err(PreflightError::Retryable)?;

    let tools_list = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 2,
    });
    write_jsonrpc_request(&mut stream, &tools_list).map_err(PreflightError::Retryable)?;
    let list_response = read_jsonrpc_response(&mut reader).map_err(PreflightError::Retryable)?;
    if list_response.error.is_some() {
        return Err(PreflightError::Permanent(format!(
            "MCP tools/list failed: {:?}",
            list_response.error
        )));
    }

    let result_value = list_response.result.ok_or_else(|| {
        PreflightError::Permanent("MCP tools/list response missing result".to_string())
    })?;
    extract_tool_names(result_value).map_err(PreflightError::Permanent)
}

fn list_tools_for_http_endpoint(
    mut stream: TcpStream,
    target: HttpEndpointTargetRef<'_>,
    io_timeout: Duration,
) -> Result<Vec<String>, PreflightError> {
    stream.set_read_timeout(Some(io_timeout)).map_err(|error| {
        PreflightError::Permanent(format!("failed to configure read timeout: {}", error))
    })?;
    stream
        .set_write_timeout(Some(io_timeout))
        .map_err(|error| {
            PreflightError::Permanent(format!("failed to configure write timeout: {}", error))
        })?;

    let initialize = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1,
    });
    let init_response =
        post_http_jsonrpc(&mut stream, target, initialize).map_err(PreflightError::Retryable)?;
    if init_response.error.is_some() {
        return Err(PreflightError::Permanent(format!(
            "HTTP MCP initialize failed: {:?}",
            init_response.error
        )));
    }

    let mut tools_stream =
        TcpStream::connect_timeout(&target.address, io_timeout).map_err(|error| {
            PreflightError::Retryable(format!("failed to reconnect for tools/list: {}", error))
        })?;
    tools_stream
        .set_read_timeout(Some(io_timeout))
        .map_err(|error| {
            PreflightError::Permanent(format!(
                "failed to configure tools/list read timeout: {}",
                error
            ))
        })?;
    tools_stream
        .set_write_timeout(Some(io_timeout))
        .map_err(|error| {
            PreflightError::Permanent(format!(
                "failed to configure tools/list write timeout: {}",
                error
            ))
        })?;

    let tools_list = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 2,
    });
    let list_response = post_http_jsonrpc(&mut tools_stream, target, tools_list)
        .map_err(PreflightError::Retryable)?;
    if list_response.error.is_some() {
        return Err(PreflightError::Permanent(format!(
            "HTTP MCP tools/list failed: {:?}",
            list_response.error
        )));
    }

    let result_value = list_response.result.ok_or_else(|| {
        PreflightError::Permanent("HTTP MCP tools/list response missing result".to_string())
    })?;
    extract_tool_names(result_value).map_err(PreflightError::Permanent)
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
    let mut line = String::new();
    let mut content_length: Option<usize> = None;
    loop {
        line.clear();
        let read = reader
            .read_line(&mut line)
            .map_err(|error| format!("failed to read MCP response header: {}", error))?;
        if read == 0 {
            return Err("MCP response closed while reading headers".to_string());
        }

        if line == "\r\n" {
            break;
        }

        if let Some(rest) = line.strip_prefix("Content-Length:") {
            content_length = rest.trim().parse::<usize>().ok();
        }
    }

    content_length.ok_or_else(|| "MCP response missing Content-Length header".to_string())
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
