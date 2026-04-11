//! Stdio-to-localhost-TCP MCP proxy for Claude Code integration.
//!
//! Claude Code can only connect to MCP servers via stdio transport (spawning a
//! child process). Ralph's MCP server runs on a localhost TCP endpoint. This module
//! provides a thin proxy that bridges the two: it reads Content-Length framed
//! JSON-RPC messages from stdin, forwards them to the TCP endpoint, and relays
//! responses back to stdout.
//!
//! This is a boundary module — mutation and imperative loops are allowed here.

use crate::mcp_server::session_bridge::{MCP_GENERATION_ENV, MCP_RUN_ID_ENV};
use anyhow::{Context, Result};
use mcp_server::io::transport::EndpointLease;
use serde_json;
use std::env;
use std::fs;
use std::io::Write;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
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

fn endpoint_lease_file(root: &Path) -> PathBuf {
    root.join(".agent").join("endpoint_lease.json")
}

fn load_endpoint_lease(root: &Path) -> Result<Option<EndpointLease>> {
    let path = endpoint_lease_file(root);
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(&path)
        .with_context(|| format!("Failed to read endpoint lease file at {}", path.display()))?;
    let lease = serde_json::from_str(&content)
        .with_context(|| format!("Failed to parse endpoint lease JSON at {}", path.display()))?;
    Ok(Some(lease))
}

#[cfg(test)]
#[cfg(test)]
fn ensure_endpoint_matches_lease(endpoint: &str, lease: &EndpointLease) -> Result<()> {
    if endpoint == lease.endpoint {
        Ok(())
    } else {
        Err(anyhow::anyhow!(
            "Stale MCP endpoint '{endpoint}' detected (generation={}, run_id={}). Active endpoint is '{}' ready_at={}",
            lease.generation,
            lease.run_id,
            lease.endpoint,
            lease.ready_at
        ))
    }
}

#[cfg(test)]
#[cfg(test)]
fn ensure_generation_matches_lease(lease: &EndpointLease) -> Result<()> {
    let generation = load_generation_from_env()?;
    if generation != lease.generation {
        Err(anyhow::anyhow!(
            "Stale MCP generation {generation} detected for {MCP_GENERATION_ENV}; expected generation {} for endpoint {}",
            lease.generation,
            lease.endpoint
        ))
    } else {
        Ok(())
    }
}

fn load_generation_from_env() -> Result<u32> {
    let raw = env::var(MCP_GENERATION_ENV)
        .with_context(|| format!("Environment variable {MCP_GENERATION_ENV} not set"))?;
    raw.parse::<u32>()
        .with_context(|| format!("Failed to parse {MCP_GENERATION_ENV}='{raw}'"))
}

fn ensure_run_id_matches_lease(lease: &EndpointLease) -> Result<()> {
    let run_id = env::var(MCP_RUN_ID_ENV)
        .with_context(|| format!("Environment variable {MCP_RUN_ID_ENV} not set"))?;
    if run_id != lease.run_id {
        Err(anyhow::anyhow!(
            "MCP run_id mismatch for {MCP_RUN_ID_ENV}: expected {} but found {}",
            lease.run_id,
            run_id
        ))
    } else {
        Ok(())
    }
}

#[cfg(test)]
fn validate_workspace_endpoint(endpoint: &str) -> Result<()> {
    if let Ok(root) = std::env::current_dir() {
        if let Some(lease) = load_endpoint_lease(&root)? {
            ensure_endpoint_matches_lease(endpoint, &lease)?;
            ensure_generation_matches_lease(&lease)?;
            ensure_run_id_matches_lease(&lease)?;
        }
    }
    Ok(())
}

fn load_current_lease() -> Result<Option<EndpointLease>> {
    let Ok(root) = std::env::current_dir() else {
        return Ok(None);
    };
    load_endpoint_lease(&root)
}

fn ensure_generation_not_regressed(env_generation: u32, lease: &EndpointLease) -> Result<()> {
    if env_generation > lease.generation {
        Err(anyhow::anyhow!(
            "MCP generation regression detected for {MCP_GENERATION_ENV}: env={env_generation}, lease={} at {}",
            lease.generation,
            lease.endpoint
        ))
    } else {
        Ok(())
    }
}

fn lease_matches_injected_endpoint(
    endpoint: &str,
    env_generation: u32,
    lease: &EndpointLease,
) -> bool {
    endpoint == lease.endpoint && env_generation == lease.generation
}

fn resolve_endpoint_for_connection(endpoint: &str) -> Result<String> {
    let Some(lease) = load_current_lease()? else {
        return Ok(endpoint.to_string());
    };
    ensure_run_id_matches_lease(&lease)?;
    let env_generation = load_generation_from_env()?;
    ensure_generation_not_regressed(env_generation, &lease)?;
    if lease_matches_injected_endpoint(endpoint, env_generation, &lease) {
        return Ok(endpoint.to_string());
    }
    eprintln!(
        "mcp-proxy: refreshing stale endpoint to active lease endpoint={} generation={} (injected endpoint={}, injected generation={})",
        lease.endpoint,
        lease.generation,
        endpoint,
        env_generation
    );
    Ok(lease.endpoint)
}

struct EndpointLeaseWatcher {
    workspace_root: Option<PathBuf>,
    current_endpoint: String,
    lease: Option<EndpointLease>,
}

impl EndpointLeaseWatcher {
    fn new(initial_endpoint: String, workspace_root: Option<PathBuf>) -> Self {
        Self {
            workspace_root,
            current_endpoint: initial_endpoint,
            lease: None,
        }
    }

    fn current_endpoint(&self) -> &str {
        &self.current_endpoint
    }

    fn current_generation(&self) -> Option<u32> {
        self.lease.as_ref().map(|lease| lease.generation)
    }

    fn refresh(&mut self) -> bool {
        let Some(root) = self.workspace_root.clone() else {
            return false;
        };
        self.try_refresh_from_root(&root)
    }

    fn try_refresh_from_root(&mut self, root: &Path) -> bool {
        match load_endpoint_lease(root) {
            Ok(Some(lease)) => self.apply_loaded_lease(lease),
            Ok(None) => false,
            Err(e) => {
                eprintln!("mcp-proxy: failed to read endpoint lease: {e}");
                false
            }
        }
    }

    fn apply_loaded_lease(&mut self, lease: EndpointLease) -> bool {
        if !self.should_update(&lease) {
            return false;
        }
        self.current_endpoint = lease.endpoint.clone();
        self.lease = Some(lease);
        true
    }

    fn should_update(&self, lease: &EndpointLease) -> bool {
        should_update_lease(self.lease.as_ref(), lease)
    }
}

fn should_update_lease(previous: Option<&EndpointLease>, lease: &EndpointLease) -> bool {
    match previous {
        Some(previous) if lease.generation < previous.generation => false,
        Some(previous) if lease.generation > previous.generation => true,
        Some(previous) => lease.endpoint != previous.endpoint && lease.ready_at > previous.ready_at,
        None => true,
    }
}

/// Spawn the stdin→socket relay thread.
pub(crate) fn spawn_stdin_thread<R>(
    reader: R,
    socket_writer: TcpStream,
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
pub(crate) fn spawn_socket_thread<W>(
    socket_reader: TcpStream,
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
    socket: TcpStream,
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

/// Resolve the TCP address from RALPH_MCP_ENDPOINT env var.
fn resolve_socket_path() -> Result<String> {
    let endpoint = std::env::var("RALPH_MCP_ENDPOINT")
        .context("RALPH_MCP_ENDPOINT environment variable not set")?;
    Ok(endpoint
        .strip_prefix("tcp://")
        .unwrap_or(&endpoint)
        .to_string())
}

/// Attempt a single connection to the TCP endpoint.
fn attempt_connection(socket_path: &str) -> std::io::Result<TcpStream> {
    TcpStream::connect(socket_path)
}

/// Outcome of the connection retry loop.
enum ConnectOutcome {
    Connected(TcpStream),
    Exhausted {
        endpoint: String,
        last_err: std::io::Error,
        attempts: usize,
    },
}

/// Maximum number of connection attempts before giving up.
///
/// Providers do substantial startup work before they even begin custom MCP
/// connection attempts (settings/plugins/skills/MCP config resolution). A
/// half-second retry budget is far too short for real unattended runs.
const MAX_CONNECT_ATTEMPTS: usize = 61;

/// Sleep duration between connection attempts in milliseconds.
const CONNECT_RETRY_SLEEP_MS: u64 = 100;

fn connect_retry_budget_ms() -> u64 {
    (MAX_CONNECT_ATTEMPTS.saturating_sub(1) as u64) * CONNECT_RETRY_SLEEP_MS
}

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

fn log_refreshed_endpoint(watcher: &EndpointLeaseWatcher) {
    if let Some(generation) = watcher.current_generation() {
        eprintln!(
            "mcp-proxy: endpoint lease refreshed to generation {} at {}",
            generation,
            watcher.current_endpoint()
        );
    } else {
        eprintln!(
            "mcp-proxy: endpoint lease refreshed at {}",
            watcher.current_endpoint()
        );
    }
}

fn maybe_log_refreshed_endpoint(watcher: &EndpointLeaseWatcher, refreshed: bool) {
    if refreshed {
        log_refreshed_endpoint(watcher);
    }
}

fn attempt_current_endpoint(watcher: &EndpointLeaseWatcher) -> std::io::Result<TcpStream> {
    attempt_connection(watcher.current_endpoint())
}

fn exhausted_outcome(endpoint: String, err: std::io::Error) -> ConnectOutcome {
    eprintln!(
        "mcp-proxy: failed to connect to {} after {} attempts: {}",
        endpoint, MAX_CONNECT_ATTEMPTS, err
    );
    ConnectOutcome::Exhausted {
        endpoint,
        last_err: err,
        attempts: MAX_CONNECT_ATTEMPTS,
    }
}

fn connect_exhausted_error(
    endpoint: &str,
    attempts: usize,
    retry_budget_ms: u64,
    last_err: std::io::Error,
) -> anyhow::Error {
    let error_kind = last_err.kind();
    anyhow::anyhow!(
        "Failed to connect to MCP endpoint at {} after {} attempts over {}ms retry budget (kind={:?}): {}",
        endpoint,
        attempts,
        retry_budget_ms,
        error_kind,
        last_err
    )
}

fn continue_after_error(
    attempt: usize,
    endpoint: String,
    err: std::io::Error,
) -> Option<ConnectOutcome> {
    if attempt == MAX_CONNECT_ATTEMPTS {
        return Some(exhausted_outcome(endpoint, err));
    }
    sleep_retry();
    None
}

fn process_connection_attempt(
    watcher: &mut EndpointLeaseWatcher,
    attempt: usize,
) -> Option<ConnectOutcome> {
    let refreshed = watcher.refresh();
    maybe_log_refreshed_endpoint(watcher, refreshed);
    let endpoint = watcher.current_endpoint().to_string();
    match attempt_current_endpoint(watcher) {
        Ok(stream) => Some(ConnectOutcome::Connected(stream)),
        Err(err) => continue_after_error(attempt, endpoint, err),
    }
}

/// Execute the connection retry loop with up to MAX_CONNECT_ATTEMPTS attempts,
/// sleeping CONNECT_RETRY_SLEEP_MS ms between each attempt.
///
/// Uses Result::or_else chaining for a functional retry pattern, avoiding
/// explicit loop constructs that would trigger forbid_imperative_loops.
fn run_connection_loop(socket_path: &str) -> ConnectOutcome {
    let workspace_root = std::env::current_dir().ok();
    let mut watcher = EndpointLeaseWatcher::new(socket_path.to_string(), workspace_root);

    for attempt in 1..=MAX_CONNECT_ATTEMPTS {
        if let Some(outcome) = process_connection_attempt(&mut watcher, attempt) {
            return outcome;
        }
    }
    unreachable!("run_connection_loop exhausted without returning");
}

/// Run a stdio-to-localhost-TCP MCP proxy.
///
/// This is spawned by Claude Code as an MCP server child process.
/// It reads JSON-RPC messages from stdin and forwards them to the
/// localhost TCP endpoint at `RALPH_MCP_ENDPOINT`, then forwards responses back to stdout.
///
/// Uses Content-Length framing (same as MCP protocol).
pub fn run_mcp_proxy() -> Result<()> {
    let injected_socket_path = resolve_socket_path()?;
    let socket_path = resolve_endpoint_for_connection(&injected_socket_path)?;
    let shutdown = Arc::new(AtomicBool::new(false));
    // Use stdin()/stdout() directly (Stdin/Stdout are Send + 'static).
    // Do NOT use stdin().lock()/stdout().lock() — those return StdinLock/StdoutLock
    // which hold lifetime-bound references and are NOT Send + 'static.
    let reader = std::io::BufReader::new(std::io::stdin());
    let writer = std::io::stdout();

    match run_connection_loop(&socket_path) {
        ConnectOutcome::Connected(stream) => run_proxy_inner(reader, writer, stream, shutdown),
        ConnectOutcome::Exhausted {
            endpoint,
            last_err,
            attempts,
        } => {
            eprintln!(
                "mcp-proxy: failed to connect to {} after {} attempts: {}",
                endpoint, attempts, last_err
            );
            Err(connect_exhausted_error(
                endpoint.as_str(),
                attempts,
                connect_retry_budget_ms(),
                last_err,
            ))
        }
    }
}

#[cfg(test)]
mod stdio_proxy_tests;
