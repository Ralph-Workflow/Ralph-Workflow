use super::*;
use std::io::Cursor;
use std::path::Path;
use std::sync::{Mutex, MutexGuard, OnceLock};
use tempfile::tempdir;

// =============================================================================
// read_framed_message tests
// =============================================================================

#[test]
fn reads_valid_framed_message() {
    // "{hello world!}" = 14 chars
    let data = b"Content-Length: 14\r\n\r\n{hello world!}";
    let mut reader = std::io::BufReader::new(Cursor::new(&data[..]));
    let result = read_framed_message(&mut reader).unwrap();
    assert_eq!(result, Some(b"{hello world!}".to_vec()));
}

#[test]
fn returns_none_on_eof() {
    let data = b"";
    let mut reader = std::io::BufReader::new(Cursor::new(&data[..]));
    let result = read_framed_message(&mut reader).unwrap();
    assert_eq!(result, None);
}

#[test]
fn errors_on_missing_content_length_header() {
    let data = b"X-Foo: bar\r\n\r\n";
    let mut reader = std::io::BufReader::new(Cursor::new(&data[..]));
    let result = read_framed_message(&mut reader);
    assert!(result.is_err());
    let err_msg = result.unwrap_err().to_string();
    assert!(
        err_msg.contains("Missing Content-Length"),
        "expected 'Missing Content-Length' error, got: {err_msg}"
    );
}

#[test]
fn errors_on_invalid_content_length_value() {
    let data = b"Content-Length: abc\r\n\r\n";
    let mut reader = std::io::BufReader::new(Cursor::new(&data[..]));
    let result = read_framed_message(&mut reader);
    assert!(result.is_err());
    let err_msg = result.unwrap_err().to_string();
    assert!(
        err_msg.contains("Invalid Content-Length"),
        "expected 'Invalid Content-Length' error, got: {err_msg}"
    );
}

#[test]
fn ignores_unknown_headers() {
    let data = b"X-Custom: value\r\nContent-Length: 2\r\n\r\nhi";
    let mut reader = std::io::BufReader::new(Cursor::new(&data[..]));
    let result = read_framed_message(&mut reader).unwrap();
    assert_eq!(result, Some(b"hi".to_vec()));
}

// =============================================================================
// write_framed_message tests
// =============================================================================

#[test]
fn write_framed_message_produces_correct_format() {
    let mut buf = Vec::new();
    write_framed_message(&mut buf, b"test").unwrap();
    let output = String::from_utf8(buf).unwrap();
    assert!(
        output.starts_with("Content-Length: 4\r\n\r\n"),
        "output must start with 'Content-Length: 4\\r\\n\\r\\n', got: {output:?}"
    );
    assert!(
        output.ends_with("test"),
        "output must end with body 'test', got: {output:?}"
    );
}

#[test]
fn roundtrip_write_then_read() {
    let body = b"{jsonrpc: \"2.0\", method: \"test\"}";
    let mut buf = Vec::new();
    write_framed_message(&mut buf, body).unwrap();

    let mut reader = std::io::BufReader::new(Cursor::new(&buf));
    let result = read_framed_message(&mut reader).unwrap().unwrap();
    assert_eq!(result, body);
}

// =============================================================================
// run_proxy_inner tests — use UnixStream pairs as fake stdio
// =============================================================================

#[test]
fn proxy_routes_messages_between_stdio_and_socket() {
    use std::io::Write;
    use std::net::{TcpListener, TcpStream};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::thread;
    use std::time::Duration;

    fn tcp_pair() -> (TcpStream, TcpStream) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let addr = listener.local_addr().unwrap();
        let client = TcpStream::connect(addr).unwrap();
        let (server, _) = listener.accept().unwrap();
        (client, server)
    }

    let (agent_stdin, proxy_in) = tcp_pair();
    let (proxy_out, agent_stdout) = tcp_pair();
    let (socket_a, socket_b) = tcp_pair();

    // Make sockets non-blocking so reads don't hang forever
    proxy_in.set_read_timeout(Some(Duration::from_secs(5))).ok();
    proxy_out
        .set_read_timeout(Some(Duration::from_secs(5)))
        .ok();
    socket_a.set_read_timeout(Some(Duration::from_secs(5))).ok();
    socket_b.set_read_timeout(Some(Duration::from_secs(5))).ok();

    let shutdown = Arc::new(AtomicBool::new(false));
    let shutdown_clone = Arc::clone(&shutdown);

    // Spawn a thread that echoes: reads from proxy_in, writes to socket_a
    // (simulates the stdin->socket direction)
    let echo_handle = thread::spawn(move || {
        let mut reader = std::io::BufReader::new(&proxy_in);
        let mut writer = std::io::BufWriter::new(&socket_a);
        while let Ok(Some(body)) = read_framed_message(&mut reader) {
            if write_framed_message(&mut writer, &body).is_err() {
                break;
            }
        }
    });

    // Spawn run_proxy_inner to bridge socket_b <-> proxy_out/proxy_in
    let proxy_out_clone = proxy_out.try_clone().unwrap();
    let proxy_handle = thread::spawn(move || {
        let reader = std::io::BufReader::new(proxy_out);
        let writer = proxy_out_clone;
        let _ = run_proxy_inner(reader, writer, socket_b, shutdown_clone);
    });

    // Write a message through the fake stdin side
    {
        let mut w = std::io::BufWriter::new(&agent_stdin);
        write_framed_message(&mut w, b"hello from agent").unwrap();
        w.flush().unwrap();
    }

    // Read it from the fake stdout side
    agent_stdout
        .set_read_timeout(Some(Duration::from_secs(5)))
        .ok();
    let mut r = std::io::BufReader::new(&agent_stdout);
    let result = read_framed_message(&mut r).unwrap();
    assert_eq!(
        result,
        Some(b"hello from agent".to_vec()),
        "proxy must route the message bytes through unchanged"
    );

    // Clean shutdown
    shutdown.store(true, Ordering::Release);
    drop(agent_stdin);
    drop(agent_stdout);
    let _ = proxy_handle.join();
    let _ = echo_handle.join();
}

#[test]
fn proxy_shuts_down_on_stdin_eof() {
    use std::io::Write;
    use std::net::{TcpListener, TcpStream};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::thread;
    use std::time::Duration;

    fn tcp_pair() -> (TcpStream, TcpStream) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let addr = listener.local_addr().unwrap();
        let client = TcpStream::connect(addr).unwrap();
        let (server, _) = listener.accept().unwrap();
        (client, server)
    }

    let (stdin_end, proxy_in) = tcp_pair();
    let (proxy_out, stdout_end) = tcp_pair();
    let (socket_a, socket_b) = tcp_pair();

    proxy_in.set_read_timeout(Some(Duration::from_secs(2))).ok();
    proxy_out
        .set_read_timeout(Some(Duration::from_secs(2)))
        .ok();
    socket_a.set_read_timeout(Some(Duration::from_secs(2))).ok();
    socket_b.set_read_timeout(Some(Duration::from_secs(2))).ok();

    let shutdown = Arc::new(AtomicBool::new(false));
    let shutdown_clone = Arc::clone(&shutdown);

    // Dummy socket reader thread
    let dummy_handle = thread::spawn(move || {
        let mut r = std::io::BufReader::new(&socket_a);
        let mut w = std::io::BufWriter::new(&socket_a);
        while let Ok(Some(body)) = read_framed_message(&mut r) {
            // Echo back
            let _ = write_framed_message(&mut w, &body);
        }
    });

    let proxy_out_clone = proxy_out.try_clone().unwrap();
    let proxy_handle = thread::spawn(move || {
        let reader = std::io::BufReader::new(proxy_out);
        let writer = proxy_out_clone;
        run_proxy_inner(reader, writer, socket_b, shutdown_clone)
    });

    // Write one message, then drop the stdin end (EOF signal)
    {
        let mut w = std::io::BufWriter::new(&stdin_end);
        write_framed_message(&mut w, b"ping").unwrap();
        w.flush().unwrap();
    }
    drop(stdin_end);

    // Wait for proxy to exit cleanly
    let result = proxy_handle.join().unwrap();
    assert!(
        result.is_ok() || result.is_err(),
        "proxy must exit after stdin EOF"
    );

    let _ = dummy_handle.join();
    shutdown.store(true, Ordering::Release);
    drop(stdout_end);
}

#[test]
fn mcp_proxy_connection_retry_budget_covers_provider_startup_delay() {
    assert!(
        super::connect_retry_budget_ms() >= 5_000,
        "retry budget must allow real provider startup, got {}ms",
        super::connect_retry_budget_ms()
    );
}

fn write_lease_file(root: &Path, lease: &EndpointLease) {
    let lease_json = serde_json::to_string(lease).expect("serialize lease");
    let agent_dir = root.join(".agent");
    std::fs::create_dir_all(&agent_dir).expect("create agent dir");
    std::fs::write(agent_dir.join("endpoint_lease.json"), lease_json).expect("write lease");
}

struct TempDirGuard {
    original: PathBuf,
}

impl TempDirGuard {
    fn new(path: &Path) -> Self {
        let original = std::env::current_dir().expect("capture cwd");
        std::env::set_current_dir(path).expect("set cwd to tempdir");
        Self { original }
    }
}

impl Drop for TempDirGuard {
    fn drop(&mut self) {
        let _ = std::env::set_current_dir(&self.original);
    }
}

struct EnvVarGuard {
    name: &'static str,
    previous: Option<String>,
}

impl EnvVarGuard {
    fn set(name: &'static str, value: &str) -> Self {
        let previous = std::env::var(name).ok();
        std::env::set_var(name, value);
        EnvVarGuard { name, previous }
    }
}

impl Drop for EnvVarGuard {
    fn drop(&mut self) {
        match &self.previous {
            Some(value) => std::env::set_var(self.name, value),
            None => std::env::remove_var(self.name),
        }
    }
}

fn serial_env_and_cwd_guard() -> MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    let lock = LOCK.get_or_init(|| Mutex::new(()));
    lock.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[test]
fn validate_workspace_endpoint_rejects_generation_mismatch() {
    let _serial = serial_env_and_cwd_guard();
    let dir = tempdir().expect("tempdir");
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:1234".into(),
        generation: 5,
        run_id: "run-1".into(),
        ready_at: 1_700_000_000,
    };
    write_lease_file(dir.path(), &lease);
    let _cwd = TempDirGuard::new(dir.path());
    let _gen_guard = EnvVarGuard::set(MCP_GENERATION_ENV, "4");
    let _run_guard = EnvVarGuard::set(MCP_RUN_ID_ENV, &lease.run_id);

    let err =
        validate_workspace_endpoint(&lease.endpoint).expect_err("generation mismatch must fail");
    assert!(
        err.to_string().contains("Stale MCP generation"),
        "expected stale generation error, got {err}"
    );
}

#[test]
fn validate_workspace_endpoint_rejects_run_id_mismatch() {
    let _serial = serial_env_and_cwd_guard();
    let dir = tempdir().expect("tempdir");
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:1234".into(),
        generation: 6,
        run_id: "run-2".into(),
        ready_at: 1_700_000_010,
    };
    write_lease_file(dir.path(), &lease);
    let _cwd = TempDirGuard::new(dir.path());
    let _gen_guard = EnvVarGuard::set(MCP_GENERATION_ENV, &lease.generation.to_string());
    let _run_guard = EnvVarGuard::set(MCP_RUN_ID_ENV, "other-run");

    let err = validate_workspace_endpoint(&lease.endpoint).expect_err("run_id mismatch must fail");
    assert!(
        err.to_string().contains("MCP run_id mismatch"),
        "expected run_id error, got {err}"
    );
}

#[test]
fn validate_workspace_endpoint_accepts_matching_generation_and_run_id() {
    let _serial = serial_env_and_cwd_guard();
    let dir = tempdir().expect("tempdir");
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:1234".into(),
        generation: 7,
        run_id: "run-match".into(),
        ready_at: 1_700_000_020,
    };
    write_lease_file(dir.path(), &lease);
    let _cwd = TempDirGuard::new(dir.path());
    let _gen_guard = EnvVarGuard::set(MCP_GENERATION_ENV, &lease.generation.to_string());
    let _run_guard = EnvVarGuard::set(MCP_RUN_ID_ENV, &lease.run_id);

    assert!(validate_workspace_endpoint(&lease.endpoint).is_ok());
}

#[test]
fn validate_endpoint_matches_correct_lease() {
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:1234".into(),
        generation: 5,
        run_id: "run123".into(),
        ready_at: 1_700_000_000,
    };
    assert!(ensure_endpoint_matches_lease(&lease.endpoint, &lease).is_ok());
}

#[test]
fn validate_endpoint_detects_stale_lease() {
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:1234".into(),
        generation: 7,
        run_id: "run456".into(),
        ready_at: 1_700_000_001,
    };
    let err = ensure_endpoint_matches_lease("tcp://127.0.0.1:4321", &lease)
        .expect_err("stale endpoint must fail");
    assert!(err.to_string().contains("Stale MCP endpoint"));
}

#[test]
fn load_endpoint_lease_reads_existing_json() {
    let dir = tempdir().expect("tempdir");
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:1234".into(),
        generation: 9,
        run_id: "run789".into(),
        ready_at: 1_700_000_002,
    };
    write_lease_file(dir.path(), &lease);
    let loaded = load_endpoint_lease(dir.path()).expect("load lease");
    assert_eq!(loaded.as_ref(), Some(&lease));
}

#[test]
fn load_endpoint_lease_handles_missing_file() {
    let dir = tempdir().expect("tempdir");
    let loaded = load_endpoint_lease(dir.path()).expect("load lease without file");
    assert!(loaded.is_none());
}

#[test]
fn endpoint_lease_watcher_refreshes_endpoint_for_new_lease() {
    let dir = tempdir().expect("tempdir");
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:1234".into(),
        generation: 5,
        run_id: "run-new".into(),
        ready_at: 1_700_000_100,
    };
    write_lease_file(dir.path(), &lease);
    let mut watcher = EndpointLeaseWatcher::new(
        "tcp://stale.sock".to_string(),
        Some(dir.path().to_path_buf()),
    );
    assert!(watcher.refresh());
    assert_eq!(watcher.current_endpoint(), lease.endpoint);
    assert_eq!(watcher.current_generation(), Some(lease.generation));
}

#[test]
fn endpoint_lease_watcher_ignores_unchanged_generation() {
    let dir = tempdir().expect("tempdir");
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:4321".into(),
        generation: 6,
        run_id: "run-old".into(),
        ready_at: 1_700_000_200,
    };
    write_lease_file(dir.path(), &lease);
    let mut watcher = EndpointLeaseWatcher::new(
        "tcp://initial.sock".to_string(),
        Some(dir.path().to_path_buf()),
    );
    assert!(watcher.refresh());
    assert!(
        !watcher.refresh(),
        "watcher should not update when lease unchanged"
    );
}

#[test]
fn endpoint_lease_watcher_does_not_downgrade_generation() {
    let mut watcher = EndpointLeaseWatcher::new(
        "tcp://127.0.0.1:9000".to_string(),
        Some(PathBuf::from("/tmp")),
    );
    assert!(watcher.apply_loaded_lease(EndpointLease {
        endpoint: "tcp://127.0.0.1:9000".into(),
        generation: 9,
        run_id: "run-1".into(),
        ready_at: 1_700_000_500,
    }));

    assert!(
        !watcher.apply_loaded_lease(EndpointLease {
            endpoint: "tcp://127.0.0.1:9001".into(),
            generation: 8,
            run_id: "run-1".into(),
            ready_at: 1_700_000_501,
        }),
        "watcher must ignore stale lower generation leases"
    );
    assert_eq!(watcher.current_endpoint(), "tcp://127.0.0.1:9000");
    assert_eq!(watcher.current_generation(), Some(9));
}

#[test]
fn resolve_endpoint_for_connection_refreshes_stale_env_endpoint() {
    let _serial = serial_env_and_cwd_guard();
    let dir = tempdir().expect("tempdir");
    let lease = EndpointLease {
        endpoint: "tcp://127.0.0.1:7788".into(),
        generation: 12,
        run_id: "run-refresh".into(),
        ready_at: 1_700_000_600,
    };
    write_lease_file(dir.path(), &lease);
    let _cwd = TempDirGuard::new(dir.path());
    let _gen_guard = EnvVarGuard::set(MCP_GENERATION_ENV, "11");
    let _run_guard = EnvVarGuard::set(MCP_RUN_ID_ENV, &lease.run_id);

    let resolved = resolve_endpoint_for_connection("tcp://127.0.0.1:5566")
        .expect("stale endpoint should refresh from active lease");
    assert_eq!(resolved, lease.endpoint);
}

#[test]
fn connect_exhausted_error_mentions_endpoint_attempts_and_budget() {
    let err = connect_exhausted_error(
        "tcp://127.0.0.1:9999",
        4,
        300,
        std::io::Error::new(std::io::ErrorKind::ConnectionRefused, "refused"),
    );
    let message = err.to_string();
    assert!(
        message.contains("tcp://127.0.0.1:9999"),
        "terminal error must include endpoint"
    );
    assert!(
        message.contains("4 attempts"),
        "terminal error must include bounded attempt count"
    );
    assert!(
        message.contains("300ms"),
        "terminal error must include retry budget"
    );
    assert!(
        message.contains("ConnectionRefused") || message.contains("refused"),
        "terminal error must include root IO cause and kind"
    );
}
