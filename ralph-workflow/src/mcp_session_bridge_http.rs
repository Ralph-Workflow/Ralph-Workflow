use super::*;
use serde_json::json;
use std::io::{Read, Write};
use std::time::Duration;

pub(super) fn run_http_gateway(
    listener: TcpListener,
    shutdown: Arc<AtomicBool>,
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
    audit_adapter: Arc<RalphAuditSinkAdapter>,
) {
    let server = super::build_in_process_server(session, workspace, audit_adapter);
    let mut state = ServerState::Uninitialized;

    let _ = std::iter::repeat(())
        .take_while(|_| !shutdown.load(Ordering::SeqCst))
        .try_for_each(|_| run_http_gateway_iteration(&listener, &server, &mut state));
}

fn run_http_gateway_iteration(
    listener: &TcpListener,
    server: &McpServer,
    state: &mut ServerState,
) -> Result<(), ()> {
    let iteration = next_http_gateway_iteration(listener);
    if iteration.stop {
        Err(())
    } else {
        apply_http_gateway_iteration(server, state, iteration);
        Ok(())
    }
}

struct HttpGatewayIteration {
    stream: Option<std::net::TcpStream>,
    delay: Option<Duration>,
    stop: bool,
}

fn next_http_gateway_iteration(listener: &TcpListener) -> HttpGatewayIteration {
    match listener.accept() {
        Ok((stream, _addr)) => HttpGatewayIteration {
            stream: Some(stream),
            delay: None,
            stop: false,
        },
        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => HttpGatewayIteration {
            stream: None,
            delay: Some(Duration::from_millis(20)),
            stop: false,
        },
        Err(_) => HttpGatewayIteration {
            stream: None,
            delay: None,
            stop: true,
        },
    }
}

fn apply_http_gateway_iteration(
    server: &McpServer,
    state: &mut ServerState,
    iteration: HttpGatewayIteration,
) {
    iteration.delay.into_iter().for_each(thread::sleep);
    iteration.stream.into_iter().for_each(|mut stream| {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
        handle_http_connection(server, state, &mut stream);
    });
}

fn handle_http_connection(
    server: &McpServer,
    state: &mut ServerState,
    stream: &mut std::net::TcpStream,
) {
    let Some(request) = read_http_request(stream) else {
        return;
    };
    let response = response_for_http_request(server, *state, request);
    *state = response.next_state;
    let _ = write_http_response(stream, response.status, "application/json", &response.body);
}

struct HttpRequestEnvelope {
    request_line: String,
    body: Vec<u8>,
}

struct HttpResponseEnvelope {
    status: u16,
    body: Vec<u8>,
    next_state: ServerState,
}

fn read_http_request(stream: &mut std::net::TcpStream) -> Option<HttpRequestEnvelope> {
    read_http_buffer(stream).map(|request| finalize_http_request(stream, request))
}

struct BufferedHttpRequest {
    buffer: Vec<u8>,
    header_end: usize,
}

fn read_http_buffer(stream: &mut std::net::TcpStream) -> Option<BufferedHttpRequest> {
    continue_http_buffer_read(stream, Vec::new())
}

fn continue_http_buffer_read(
    stream: &mut std::net::TcpStream,
    buffer: Vec<u8>,
) -> Option<BufferedHttpRequest> {
    let next_buffer: Vec<u8> = buffer.into_iter().chain(read_http_chunk(stream)?).collect();
    find_header_end(&next_buffer)
        .map(|header_end| BufferedHttpRequest {
            buffer: next_buffer.clone(),
            header_end,
        })
        .or_else(|| continue_http_buffer_read(stream, next_buffer))
}

fn read_http_chunk(stream: &mut std::net::TcpStream) -> Option<Vec<u8>> {
    let mut chunk = [0u8; 1024];
    let read = stream.read(&mut chunk).ok()?;
    if read == 0 {
        None
    } else {
        Some(chunk[..read].to_vec())
    }
}

fn finalize_http_request(
    stream: &mut std::net::TcpStream,
    request: BufferedHttpRequest,
) -> HttpRequestEnvelope {
    let headers_raw = String::from_utf8_lossy(&request.buffer[..request.header_end]);
    let mut lines = headers_raw.lines();
    let request_line = lines.next().unwrap_or_default().to_string();
    let content_length = lines
        .filter_map(|line| line.split_once(':'))
        .find_map(|(name, value)| {
            if name.trim().eq_ignore_ascii_case("content-length") {
                value.trim().parse::<usize>().ok()
            } else {
                None
            }
        })
        .unwrap_or(0);

    HttpRequestEnvelope {
        request_line,
        body: read_http_body(stream, request.buffer, request.header_end, content_length),
    }
}

fn read_http_body(
    stream: &mut std::net::TcpStream,
    buffer: Vec<u8>,
    header_end: usize,
    content_length: usize,
) -> Vec<u8> {
    continue_http_body_read(stream, buffer[(header_end + 4)..].to_vec(), content_length)
}

fn continue_http_body_read(
    stream: &mut std::net::TcpStream,
    body: Vec<u8>,
    content_length: usize,
) -> Vec<u8> {
    if body.len() >= content_length {
        body
    } else {
        read_http_chunk(stream)
            .map(|chunk| {
                continue_http_body_read(
                    stream,
                    body.clone().into_iter().chain(chunk).collect(),
                    content_length,
                )
            })
            .unwrap_or(body)
    }
}

fn response_for_http_request(
    server: &McpServer,
    state: ServerState,
    request: HttpRequestEnvelope,
) -> HttpResponseEnvelope {
    if !request.request_line.starts_with("POST /mcp ") {
        return HttpResponseEnvelope {
            status: 404,
            body: b"{}".to_vec(),
            next_state: state,
        };
    }

    match serde_json::from_slice::<JsonRpcRequest>(&request.body) {
        Ok(parsed_request) => response_for_jsonrpc_request(server, state, parsed_request),
        Err(_) => HttpResponseEnvelope {
            status: 400,
            body: serde_json::to_vec(&json!({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": serde_json::Value::Null
            }))
            .unwrap_or_else(|_| b"{}".to_vec()),
            next_state: state,
        },
    }
}

fn response_for_jsonrpc_request(
    server: &McpServer,
    state: ServerState,
    request: JsonRpcRequest,
) -> HttpResponseEnvelope {
    let (response, next_state) = server.handle_request(request, state);
    response.map_or_else(
        || HttpResponseEnvelope {
            status: 204,
            body: Vec::new(),
            next_state,
        },
        |payload| HttpResponseEnvelope {
            status: 200,
            body: serde_json::to_vec(&payload).unwrap_or_else(|_| b"{}".to_vec()),
            next_state,
        },
    )
}

fn write_http_response(
    stream: &mut std::net::TcpStream,
    status: u16,
    content_type: &str,
    body: &[u8],
) -> std::io::Result<()> {
    let status_text = http_status_text(status);
    let headers = format!(
        "HTTP/1.1 {status} {status_text}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(headers.as_bytes())?;
    stream.write_all(body)?;
    stream.flush()
}

fn http_status_text(status: u16) -> &'static str {
    match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        404 => "Not Found",
        _ => "OK",
    }
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|window| window == b"\r\n\r\n")
}
