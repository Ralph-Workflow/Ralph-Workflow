use anyhow::{Context, Result};
use mcp_server::io::ServerState;
use mcp_server::protocol::JsonRpcRequest;
use ralph_workflow::agents::session::{AgentSession, SessionDrain};
use ralph_workflow::mcp_server::session_bridge::SessionBridge;
use ralph_workflow::workspace::memory_workspace::MemoryWorkspace;
use ralph_workflow::workspace::Workspace;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Arc;
use tempfile::TempDir;

const MCP_PROTOCOL_VERSION: &str = "2024-11-05";

struct ProxyHarness {
    _cwd: TempDir,
    child: Child,
    stdin: ChildStdin,
    stdout: std::io::BufReader<ChildStdout>,
}

impl ProxyHarness {
    fn spawn(endpoint: &str) -> Result<Self> {
        let cwd = tempfile::tempdir().context("create temp cwd for proxy")?;
        let mut child = Command::new(find_ralph_binary()?)
            .arg("--mcp-proxy")
            .current_dir(cwd.path())
            .env("NO_COLOR", "1")
            .env("RALPH_MCP_ENDPOINT", endpoint)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::piped())
            .spawn()
            .context("spawn ralph --mcp-proxy")?;
        let stdin = child.stdin.take().context("capture proxy stdin")?;
        let stdout = child.stdout.take().context("capture proxy stdout")?;

        Ok(Self {
            _cwd: cwd,
            child,
            stdin,
            stdout: std::io::BufReader::new(stdout),
        })
    }

    fn request(&mut self, request: serde_json::Value) -> Result<serde_json::Value> {
        let body = serde_json::to_vec(&request).context("serialize request")?;
        write!(self.stdin, "Content-Length: {}\r\n\r\n", body.len())
            .context("write content-length header")?;
        self.stdin.write_all(&body).context("write request body")?;
        self.stdin.flush().context("flush request body")?;
        read_framed_json(&mut self.stdout)
    }

    fn finish(self) -> Result<std::process::Output> {
        drop(self.stdin);
        self.child
            .wait_with_output()
            .context("wait for proxy process")
    }
}

fn find_ralph_binary() -> Result<PathBuf> {
    std::env::var("CARGO_BIN_EXE_ralph")
        .ok()
        .map(PathBuf::from)
        .filter(|path| path.exists())
        .or_else(|| {
            std::env::var("CARGO_MANIFEST_DIR")
                .ok()
                .map(PathBuf::from)
                .and_then(|tests_dir| tests_dir.parent().map(Path::to_path_buf))
                .map(|repo_root| repo_root.join("target/debug/ralph"))
                .filter(|path| path.exists())
        })
        .or_else(|| {
            std::env::var("CARGO_MANIFEST_DIR")
                .ok()
                .map(PathBuf::from)
                .and_then(|tests_dir| tests_dir.parent().map(Path::to_path_buf))
                .map(|repo_root| repo_root.join("target/release/ralph"))
                .filter(|path| path.exists())
        })
        .context("ralph binary not found for process system test")
}

fn read_framed_json(reader: &mut impl BufRead) -> Result<serde_json::Value> {
    let content_length = read_content_length(reader)?;
    let mut body = vec![0_u8; content_length];
    reader
        .read_exact(&mut body)
        .context("read framed response body")?;
    serde_json::from_slice(&body).context("parse framed response json")
}

fn read_content_length(reader: &mut impl BufRead) -> Result<usize> {
    let mut content_length = None;
    loop {
        let mut line = String::new();
        let read = reader
            .read_line(&mut line)
            .context("read response header")?;
        if read == 0 {
            return Err(anyhow::anyhow!(
                "proxy exited before sending response headers"
            ));
        }
        if line == "\r\n" {
            break;
        }
        if let Some(value) = line.strip_prefix("Content-Length:") {
            content_length = Some(
                value
                    .trim()
                    .parse::<usize>()
                    .context("parse response Content-Length")?,
            );
        }
    }
    content_length.context("response missing Content-Length header")
}

fn request_json(method: &str, params: serde_json::Value, id: u64) -> serde_json::Value {
    serde_json::json!({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": id
    })
}

fn initialize(proxy: &mut ProxyHarness) -> Result<serde_json::Value> {
    proxy.request(request_json(
        "initialize",
        serde_json::json!({
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "process-system-test", "version": "0.1"}
        }),
        1,
    ))
}

fn start_bridge(run_id: &str, drain: SessionDrain, workspace: Arc<dyn Workspace>) -> SessionBridge {
    let session = AgentSession::for_drain(run_id.to_string(), drain, 1);
    let mut bridge = SessionBridge::new(session, workspace);
    bridge.start().expect("SessionBridge::start() must succeed");
    bridge
}

fn valid_commit_message_artifact() -> serde_json::Value {
    serde_json::json!({
        "type": "commit",
        "subject": "feat: probe commit submission path"
    })
}

#[test]
fn mcp_proxy_lists_tools_through_live_tcp_runtime() {
    let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());
    let mut bridge = start_bridge(
        "task-13-live-tool-list",
        SessionDrain::Development,
        Arc::clone(&workspace),
    );
    let endpoint = bridge.endpoint_uri();

    let mut proxy = ProxyHarness::spawn(endpoint.as_str()).expect("spawn live proxy");
    let init = initialize(&mut proxy).expect("initialize through live proxy");
    assert!(
        init.get("error").is_none(),
        "initialize must succeed through live proxy: {init}"
    );

    let response = proxy
        .request(request_json("tools/list", serde_json::json!({}), 2))
        .expect("list tools through live proxy");
    assert!(
        response.get("error").is_none(),
        "tools/list must succeed through live proxy: {response}"
    );

    let tool_names: Vec<&str> = response["result"]["tools"]
        .as_array()
        .expect("tools array")
        .iter()
        .filter_map(|tool| tool["name"].as_str())
        .collect();

    assert!(
        tool_names.contains(&"read_file"),
        "live proxy tools/list must expose read_file: {tool_names:?}"
    );
    assert!(
        tool_names.contains(&"ralph_submit_artifact"),
        "live proxy tools/list must expose ralph_submit_artifact: {tool_names:?}"
    );

    let output = proxy.finish().expect("wait for live proxy exit");
    assert!(
        output.status.success(),
        "live proxy process must exit cleanly, stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    bridge.shutdown();
}

#[test]
fn mcp_proxy_submits_commit_artifact_through_live_runtime() {
    let memory_workspace = Arc::new(MemoryWorkspace::new_test());
    let workspace: Arc<dyn Workspace> = memory_workspace.clone();
    let mut bridge = start_bridge(
        "task-13-live-commit-submit",
        SessionDrain::Commit,
        workspace,
    );
    let endpoint = bridge.endpoint_uri();

    let mut proxy = ProxyHarness::spawn(endpoint.as_str()).expect("spawn live proxy");
    let init = initialize(&mut proxy).expect("initialize commit proxy");
    assert!(
        init.get("error").is_none(),
        "initialize must succeed for commit drain: {init}"
    );

    let response = proxy
        .request(request_json(
            "tools/call",
            serde_json::json!({
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "commit_message",
                    "content": serde_json::to_string(&valid_commit_message_artifact())
                        .expect("serialize commit artifact")
                }
            }),
            2,
        ))
        .expect("submit artifact through live proxy");

    assert!(
        response.get("error").is_none(),
        "commit artifact submission must not return JSON-RPC error: {response}"
    );

    let text_payload = response["result"]["content"]
        .as_array()
        .expect("tool result content array")
        .iter()
        .find(|entry| entry["type"].as_str() == Some("text"))
        .and_then(|entry| entry["text"].as_str())
        .expect("text payload from submit artifact response");

    assert!(
        text_payload.contains("\"accepted\": true"),
        "commit artifact submission must be accepted: {text_payload}"
    );

    let artifact = memory_workspace
        .read_artifact_json("commit_message")
        .expect("read commit artifact from workspace")
        .expect("commit artifact must exist after live submission");
    assert_eq!(artifact.artifact_type, "commit_message");

    let output = proxy.finish().expect("wait for commit proxy exit");
    assert!(
        output.status.success(),
        "commit proxy process must exit cleanly, stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    bridge.shutdown();
}

#[test]
fn mcp_proxy_fails_fast_with_root_cause_when_endpoint_unreachable() {
    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).expect("bind probe port");
    let endpoint = format!("tcp://{}", listener.local_addr().expect("listener addr"));
    drop(listener);

    let output = ProxyHarness::spawn(endpoint.as_str())
        .expect("spawn unreachable proxy")
        .finish()
        .expect("wait for unreachable proxy exit");

    assert!(
        !output.status.success(),
        "proxy must fail when endpoint is unreachable"
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("Failed to connect to MCP endpoint at")
            && stderr.contains(endpoint.as_str()),
        "stderr must include explicit endpoint failure context: {stderr}"
    );
    assert!(
        stderr.contains("Connection refused") || stderr.contains("kind=ConnectionRefused"),
        "stderr must include root cause from failed connect: {stderr}"
    );
    assert!(
        stderr.contains("after 61 attempts") && stderr.contains("6000ms retry budget"),
        "stderr must show fail-fast retry bounds: {stderr}"
    );
}

#[test]
fn session_bridge_exposes_live_tcp_state_for_runtime_probe_harness() {
    let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());
    let mut bridge = start_bridge(
        "task-13-live-endpoint",
        SessionDrain::Development,
        workspace,
    );

    assert!(bridge.is_started(), "bridge must report started");
    let endpoint = bridge.endpoint_uri();
    assert!(
        endpoint.starts_with("tcp://127.0.0.1:"),
        "runtime probe harness requires TCP loopback endpoint, got {endpoint}"
    );
    let lease = bridge
        .endpoint_lease()
        .expect("live runtime must publish endpoint lease");
    assert_eq!(lease.endpoint, endpoint);

    let request: JsonRpcRequest = serde_json::from_value(serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": MCP_PROTOCOL_VERSION},
        "id": 1
    }))
    .expect("build initialize request");
    let (response, next_state) =
        bridge.handle_request_in_process(request, ServerState::Uninitialized);
    assert!(
        response.is_some(),
        "in-process seam remains available for parity checks"
    );
    assert_ne!(next_state, ServerState::Uninitialized);

    bridge.shutdown();
}
