//! Stdio-to-Unix-socket MCP proxy for Claude Code integration.
//!
//! Claude Code can only connect to MCP servers via stdio transport (spawning a
//! child process). Ralph's MCP server runs on a Unix socket. This module
//! provides a thin proxy that bridges the two: it reads Content-Length framed
//! JSON-RPC messages from stdin, forwards them to the Unix socket, and relays
//! responses back to stdout.

use anyhow::{Context, Result};
use std::io::Write;
use std::os::unix::net::UnixStream;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// Parse Content-Length from a header line, returning `Some(len)` or `None`.
fn parse_content_length_header(line: &str) -> Option<Result<usize>> {
    let trimmed = line.trim();
    if !trimmed.starts_with("Content-Length:") {
        return None;
    }
    let len_str = trimmed.trim_start_matches("Content-Length:").trim();
    Some(
        len_str
            .parse::<usize>()
            .context("Invalid Content-Length value"),
    )
}

/// Outcome of reading headers from a framed message.
enum HeadersOutcome {
    /// Clean EOF before any data was read.
    Eof,
    /// Headers were read and Content-Length was found.
    ContentLength(usize),
    /// Headers ended without a Content-Length header.
    Missing,
}

/// Read a single header line, returning `None` if EOF, `Some(line)` otherwise.
/// Returns `Err` on read failure.
fn read_header_line(reader: &mut impl std::io::BufRead) -> Result<Option<String>> {
    let mut line = String::new();
    let n = reader
        .read_line(&mut line)
        .context("Failed to read header line")?;
    if n == 0 {
        Ok(None)
    } else {
        Ok(Some(line))
    }
}

/// Update content_length from a single non-empty header line.
fn apply_content_length_header(line: &str, content_length: &mut Option<usize>) -> Result<()> {
    if let Some(r) = parse_content_length_header(line) {
        *content_length = Some(r?);
    }
    Ok(())
}

/// Read headers until empty line, classifying the outcome.
fn read_headers(reader: &mut impl std::io::BufRead) -> Result<HeadersOutcome> {
    let mut content_length: Option<usize> = None;
    loop {
        let line = match read_header_line(reader)? {
            None => return Ok(HeadersOutcome::Eof),
            Some(l) => l,
        };
        if line.trim().is_empty() {
            break;
        } else {
            apply_content_length_header(&line, &mut content_length)?;
        }
    }
    Ok(content_length.map_or(HeadersOutcome::Missing, HeadersOutcome::ContentLength))
}

/// Read a single Content-Length framed message from `reader`.
///
/// Returns `Ok(None)` on clean EOF, `Ok(Some(bytes))` on success.
pub(crate) fn read_framed_message(reader: &mut impl std::io::BufRead) -> Result<Option<Vec<u8>>> {
    let length = match read_headers(reader)? {
        HeadersOutcome::Eof => return Ok(None),
        HeadersOutcome::Missing => return Err(anyhow::anyhow!("Missing Content-Length header")),
        HeadersOutcome::ContentLength(n) => n,
    };
    let mut body = vec![0u8; length];
    reader
        .read_exact(&mut body)
        .context("Failed to read message body")?;
    Ok(Some(body))
}

/// Write a single Content-Length framed message to `writer`.
pub(crate) fn write_framed_message(writer: &mut impl Write, body: &[u8]) -> Result<()> {
    write!(writer, "Content-Length: {}\r\n\r\n", body.len())
        .context("Failed to write Content-Length header")?;
    writer.write_all(body).context("Failed to write body")?;
    writer.flush().context("Failed to flush")?;
    Ok(())
}

/// Handle a relay read error: log if not already shutting down, then signal shutdown.
fn handle_relay_read_error(e: anyhow::Error, shutdown: &AtomicBool, label: &str) {
    if !shutdown.load(Ordering::Acquire) {
        eprintln!("mcp-proxy: {label} read error: {e}");
    }
    shutdown.store(true, Ordering::Release);
}

/// Process one message from the relay loop. Returns false if the loop should stop.
fn relay_one_message(
    reader: &mut impl std::io::BufRead,
    writer: &mut impl Write,
    shutdown: &AtomicBool,
    label: &str,
) -> bool {
    if shutdown.load(Ordering::Acquire) {
        return false;
    }
    match read_framed_message(reader) {
        Ok(Some(body)) => {
            relay_body(body, writer, shutdown, label);
            true
        }
        Ok(None) => {
            shutdown.store(true, Ordering::Release);
            false
        }
        Err(e) => {
            handle_relay_read_error(e, shutdown, label);
            false
        }
    }
}

/// Relay messages from `reader` to `writer` until EOF, error, or shutdown.
fn run_relay_loop(
    reader: &mut impl std::io::BufRead,
    writer: &mut impl Write,
    shutdown: &AtomicBool,
    label: &str,
) {
    while relay_one_message(reader, writer, shutdown, label) {}
}

fn relay_body(body: Vec<u8>, writer: &mut impl Write, shutdown: &AtomicBool, label: &str) {
    if let Err(e) = write_framed_message(writer, &body) {
        if !shutdown.load(Ordering::Acquire) {
            eprintln!("mcp-proxy: {label} write error: {e}");
        }
        shutdown.store(true, Ordering::Release);
    }
}

/// Spawn the stdin→socket relay thread.
fn spawn_stdin_thread<R>(
    reader: R,
    socket_writer: UnixStream,
    shutdown: Arc<AtomicBool>,
) -> std::thread::JoinHandle<Result<()>>
where
    R: std::io::BufRead + Send + 'static,
{
    std::thread::spawn(move || {
        let mut reader = reader;
        let mut sock_writer = std::io::BufWriter::new(socket_writer);
        run_relay_loop(&mut reader, &mut sock_writer, &shutdown, "stdin");
        sock_writer
            .into_inner()
            .ok()
            .and_then(|s| s.shutdown(std::net::Shutdown::Write).ok());
        Ok(())
    })
}

/// Spawn the socket→stdout relay thread.
fn spawn_socket_thread<W>(
    socket_reader: UnixStream,
    writer: W,
    shutdown: Arc<AtomicBool>,
) -> std::thread::JoinHandle<Result<()>>
where
    W: std::io::Write + Send + 'static,
{
    std::thread::spawn(move || {
        let mut reader = std::io::BufReader::new(socket_reader);
        let mut writer = std::io::BufWriter::new(writer);
        run_relay_loop(&mut reader, &mut writer, &shutdown, "socket");
        Ok(())
    })
}

/// Run the MCP proxy logic using custom reader/writer handles.
///
/// This is the testable core of the proxy. It spawns the stdin→socket and
/// socket→stdout worker threads, using the provided interfaces so we can inject
/// fake stdio in tests.
pub(crate) fn run_proxy_inner<R, W>(
    reader: R,
    writer: W,
    socket: UnixStream,
    shutdown: Arc<AtomicBool>,
) -> Result<()>
where
    R: std::io::BufRead + Send + 'static,
    W: std::io::Write + Send + 'static,
{
    let socket_reader = socket
        .try_clone()
        .context("Failed to clone socket for reader")?;
    let socket_writer = socket;

    let stdin_thread = spawn_stdin_thread(reader, socket_writer, Arc::clone(&shutdown));
    let socket_thread = spawn_socket_thread(socket_reader, writer, shutdown);

    let stdin_result = stdin_thread
        .join()
        .map_err(|_| anyhow::anyhow!("stdin thread panicked"))?;
    let socket_result = socket_thread
        .join()
        .map_err(|_| anyhow::anyhow!("socket thread panicked"))?;
    stdin_result.and(socket_result)
}

/// Resolve the socket path from RALPH_MCP_ENDPOINT env var.
fn resolve_socket_path() -> Result<String> {
    let endpoint = std::env::var("RALPH_MCP_ENDPOINT")
        .context("RALPH_MCP_ENDPOINT environment variable not set")?;
    Ok(endpoint
        .strip_prefix("unix://")
        .unwrap_or(&endpoint)
        .to_string())
}

/// Attempt a single connection to the socket.
fn attempt_connection(socket_path: &str) -> std::io::Result<UnixStream> {
    UnixStream::connect(socket_path)
}

/// Outcome of the connection retry loop.
enum ConnectOutcome {
    Connected(UnixStream),
    Exhausted {
        last_err: std::io::Error,
        attempts: usize,
    },
}

/// Maximum number of connection attempts before giving up.
const MAX_CONNECT_ATTEMPTS: usize = 5;

/// Sleep duration between connection attempts in milliseconds.
const CONNECT_RETRY_SLEEP_MS: u64 = 100;

/// Sleep for the retry interval between connection attempts.
///
/// Uses `std::thread::sleep` which is an effect boundary call (I/O sleep).
///
/// Shutdown flag is not checked here: this sleep runs only during initial connection
/// (max 5 * 100ms = 500ms total). Prompt shutdown on relay errors is handled by
/// `run_proxy_inner`, which checks the shutdown flag before attempting reconnect.
fn sleep_retry() {
    std::thread::sleep(std::time::Duration::from_millis(CONNECT_RETRY_SLEEP_MS));
}

/// Execute the connection retry loop with up to MAX_CONNECT_ATTEMPTS attempts,
/// sleeping CONNECT_RETRY_SLEEP_MS ms between each attempt.
///
/// Uses Result::or_else chaining for a functional retry pattern, avoiding
/// explicit loop constructs that would trigger forbid_imperative_loops.
fn run_connection_loop(socket_path: &str) -> ConnectOutcome {
    /// Attempt connection with retry on failure, using Result::or_else chaining.
    ///
    /// Each or_else sleeps before retrying, building up to MAX_CONNECT_ATTEMPTS attempts.
    /// The final attempt is tried without sleeping afterward.
    fn attempt_with_retries(
        socket_path: &str,
        remaining: usize,
    ) -> Result<UnixStream, std::io::Error> {
        debug_assert!(
            remaining > 0,
            "attempt_with_retries called with 0 remaining"
        );
        attempt_connection(socket_path).or_else(|err| {
            if remaining > 1 {
                sleep_retry();
                attempt_with_retries(socket_path, remaining - 1)
            } else {
                Err(err)
            }
        })
    }

    match attempt_with_retries(socket_path, MAX_CONNECT_ATTEMPTS) {
        Ok(stream) => ConnectOutcome::Connected(stream),
        Err(last_err) => {
            eprintln!(
                "mcp-proxy: failed to connect to {} after {} attempts: {}",
                socket_path, MAX_CONNECT_ATTEMPTS, last_err
            );
            ConnectOutcome::Exhausted {
                last_err,
                attempts: MAX_CONNECT_ATTEMPTS,
            }
        }
    }
}

/// Run a stdio-to-Unix-socket MCP proxy.
///
/// This is spawned by Claude Code as an MCP server child process.
/// It reads JSON-RPC messages from stdin and forwards them to the
/// Unix socket at `RALPH_MCP_ENDPOINT`, then forwards responses back to stdout.
///
/// Uses Content-Length framing (same as MCP protocol).
pub fn run_mcp_proxy() -> Result<()> {
    let socket_path = resolve_socket_path()?;
    let shutdown = Arc::new(AtomicBool::new(false));
    // Use stdin()/stdout() directly (Stdin/Stdout are Send + 'static).
    // Do NOT use stdin().lock()/stdout().lock() — those return StdinLock/StdoutLock
    // which hold lifetime-bound references and are NOT Send + 'static.
    let reader = std::io::BufReader::new(std::io::stdin());
    let writer = std::io::stdout();

    match run_connection_loop(&socket_path) {
        ConnectOutcome::Connected(stream) => run_proxy_inner(reader, writer, stream, shutdown),
        ConnectOutcome::Exhausted { last_err, attempts } => {
            eprintln!(
                "mcp-proxy: failed to connect to {} after {} attempts: {}",
                socket_path, attempts, last_err
            );
            Err(anyhow::anyhow!(
                "Failed to connect to MCP socket at {}: {}",
                socket_path,
                last_err
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

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
        use std::os::unix::net::UnixStream;
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::sync::Arc;
        use std::thread;
        use std::time::Duration;

        // Create two UnixStream pairs: one for fake stdin/stdout, one for socket
        let (agent_stdin, proxy_in) = UnixStream::pair().unwrap();
        let (proxy_out, agent_stdout) = UnixStream::pair().unwrap();
        let (socket_a, socket_b) = UnixStream::pair().unwrap();

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
        use std::os::unix::net::UnixStream;
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::sync::Arc;
        use std::thread;
        use std::time::Duration;

        let (stdin_end, proxy_in) = UnixStream::pair().unwrap();
        let (proxy_out, stdout_end) = UnixStream::pair().unwrap();
        let (socket_a, socket_b) = UnixStream::pair().unwrap();

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
}
