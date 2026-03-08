use super::*;
use crate::config::Config;
use crate::executor::{MockProcessExecutor, ProcessExecutor};
use crate::logger::{Colors, Logger};
use crate::pipeline::Timer;
use crate::workspace::MemoryWorkspace;
use crate::workspace::Workspace;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

#[derive(Debug)]
struct ControlledReader {
    stop: Arc<AtomicBool>,
}

impl io::Read for ControlledReader {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.stop.load(Ordering::Acquire) {
            return Ok(0);
        }
        if buf.is_empty() {
            return Ok(0);
        }
        buf[0] = b'x';
        Ok(1)
    }
}

#[derive(Debug)]
struct FastReader {
    remaining: usize,
}

impl FastReader {
    fn new(total_bytes: usize) -> Self {
        Self {
            remaining: total_bytes,
        }
    }
}

impl io::Read for FastReader {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.remaining == 0 {
            return Ok(0);
        }

        let n = buf.len().min(self.remaining);
        buf[..n].fill(b'a');
        self.remaining -= n;
        Ok(n)
    }
}

#[test]
fn stdout_pump_applies_backpressure_when_receiver_is_not_consuming() {
    // Bounded-memory invariant: the stdout pump must not enqueue unbounded chunks when the
    // downstream parser stalls. A bounded channel forces backpressure (blocking on send).
    let timestamp = crate::pipeline::idle_timeout::new_activity_timestamp();
    let cancel = Arc::new(AtomicBool::new(false));

    // Large enough to exceed any bounded queue (4096 bytes per chunk).
    let reader: Box<dyn io::Read + Send> = Box::new(FastReader::new(10 * 1024 * 1024));
    let (tx, rx) = mpsc::sync_channel(STDOUT_PUMP_CHANNEL_CAPACITY);

    let handle = spawn_stdout_pump(reader, timestamp, tx, Arc::clone(&cancel));

    std::thread::sleep(Duration::from_millis(20));

    // With an unbounded channel, the pump can usually enqueue everything and exit.
    // With a bounded channel, it should block and remain alive.
    assert!(
        !handle.is_finished(),
        "stdout pump finished despite receiver not consuming"
    );

    cancel.store(true, Ordering::Release);
    drop(rx);
    let _ = handle.join();
}

#[test]
fn stdout_pump_exits_when_receiver_dropped() {
    let stop = Arc::new(AtomicBool::new(false));
    let reader: Box<dyn io::Read + Send> = Box::new(ControlledReader {
        stop: Arc::clone(&stop),
    });

    let timestamp = crate::pipeline::idle_timeout::new_activity_timestamp();
    let (tx, rx) = mpsc::sync_channel(STDOUT_PUMP_CHANNEL_CAPACITY);
    let cancel = Arc::new(AtomicBool::new(false));

    let handle = spawn_stdout_pump(reader, timestamp, tx, cancel);
    drop(rx);

    let test_result = {
        let handle_ref = &handle;
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || {
            let deadline = std::time::Instant::now() + Duration::from_millis(200);
            while std::time::Instant::now() < deadline {
                if handle_ref.is_finished() {
                    return;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            panic!("stdout pump thread did not exit after receiver drop");
        }))
    };

    // Always stop and join so the test doesn't leak threads.
    stop.store(true, Ordering::Release);
    let _ = handle.join();

    if let Err(payload) = test_result {
        std::panic::resume_unwind(payload);
    }
}

#[test]
fn cleanup_stdout_pump_sets_cancel_on_parse_error() {
    let colors = Colors::with_enabled(false);
    let logger = Logger::new(colors);
    let config = Config::default();
    let workspace = MemoryWorkspace::new_test();

    let executor_arc: Arc<dyn ProcessExecutor> = Arc::new(MockProcessExecutor::new());
    let executor: &dyn ProcessExecutor = executor_arc.as_ref();

    let mut timer = Timer::new();
    let runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor,
        executor_arc: Arc::clone(&executor_arc),
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
    };

    let cancel = Arc::new(AtomicBool::new(false));
    let pump_handle = std::thread::spawn(|| {});
    let parse_result: io::Result<()> = Err(io::Error::other("parse error"));

    cleanup_stdout_pump(pump_handle, &cancel, &runtime, &parse_result);

    assert!(
        cancel.load(Ordering::Acquire),
        "cancel should be set on parse error to stop the pump thread promptly"
    );
}

#[test]
fn test_extract_error_message_from_json_line_opencode_usage_limit() {
    let line = r#"{"type":"error","timestamp":1768191346712,"sessionID":"ses_123","error":{"message":"usage limit reached"}}"#;
    let result = extract_error_message_from_json_line(line);
    assert_eq!(result, Some("usage limit reached".to_string()));
}

#[test]
fn test_extract_error_message_from_json_line_opencode_data_message() {
    let line = r#"{"type":"error","error":{"data":{"message":"Invalid API key"}}}"#;
    let result = extract_error_message_from_json_line(line);
    assert_eq!(result, Some("Invalid API key".to_string()));
}

#[test]
fn test_extract_error_message_from_json_line_opencode_error_name() {
    let line = r#"{"type":"error","error":{"name":"APIError"}}"#;
    let result = extract_error_message_from_json_line(line);
    assert_eq!(result, Some("APIError".to_string()));
}

#[test]
fn test_extract_error_message_from_json_line_claude_format() {
    let line = r#"{"type":"error","message":"Rate limit exceeded"}"#;
    let result = extract_error_message_from_json_line(line);
    assert_eq!(result, Some("Rate limit exceeded".to_string()));
}

#[test]
fn test_extract_error_message_from_json_line_not_error_event() {
    let line = r#"{"type":"init","session_id":"abc123"}"#;
    let result = extract_error_message_from_json_line(line);
    assert_eq!(result, None);
}

#[test]
fn test_extract_error_message_from_json_line_invalid_json() {
    let line = "This is not JSON";
    let result = extract_error_message_from_json_line(line);
    assert_eq!(result, None);
}

#[test]
fn test_extract_error_message_from_json_line_requires_explicit_error_type() {
    // Regression test: JSON without a top-level type marker should not be treated
    // as an error event, even if it contains an `error` object.
    let line = r#"{"error":{"message":"not actually an error event"}}"#;
    let result = extract_error_message_from_json_line(line);
    assert_eq!(result, None);
}

#[test]
fn test_extract_error_message_from_logfile_opencode_usage_limit() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    // Create a test workspace with a logfile containing OpenCode JSON output
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/opencode.log");

    // Simulate OpenCode JSON output with multiple events including error
    let log_content = r#"{"type":"init","timestamp":1768191346000,"sessionID":"ses_123","model":"claude-3.5-sonnet"}
{"type":"message","timestamp":1768191346100,"content":"Processing request..."}
{"type":"message","timestamp":1768191346200,"content":"Analyzing code..."}
{"type":"error","timestamp":1768191346712,"sessionID":"ses_123","error":{"message":"usage limit reached"}}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    // Extract error message from logfile
    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, Some("usage limit reached".to_string()));
}

#[test]
fn test_extract_error_message_from_logfile_no_error() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/opencode.log");

    // Logfile with no error events
    let log_content = r#"{"type":"init","timestamp":1768191346000,"sessionID":"ses_123"}
{"type":"message","timestamp":1768191346100,"content":"All good"}
{"type":"completion","timestamp":1768191346200,"status":"success"}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, None);
}

#[test]
fn test_extract_error_message_from_logfile_file_not_found() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));

    // Try to extract from non-existent file
    let result = extract_error_message_from_logfile(".agent/tmp/nonexistent.log", &workspace);

    assert_eq!(result, None);
}

#[test]
fn test_extract_error_message_from_logfile_empty_file() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/empty.log");

    workspace.write(logfile_path, "").unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, None);
}

#[test]
fn test_extract_error_message_from_logfile_error_on_first_line() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/opencode.log");

    // Error on first line
    let log_content = r#"{"type":"error","error":{"message":"Invalid API key"}}
{"type":"init","timestamp":1768191346000,"sessionID":"ses_123"}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, Some("Invalid API key".to_string()));
}

#[test]
fn test_extract_error_message_from_logfile_multiple_errors() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/opencode.log");

    // Multiple error events - should return the LAST one (most recent)
    let log_content = r#"{"type":"error","error":{"message":"First error"}}
{"type":"message","timestamp":1768191346100,"content":"Retrying..."}
{"type":"error","error":{"message":"Second error"}}
{"type":"message","timestamp":1768191346200,"content":"Retrying again..."}
{"type":"error","error":{"message":"Final error"}}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    // Should return the last error (searched in reverse, take last 50 lines)
    assert_eq!(result, Some("Final error".to_string()));
}

#[test]
fn test_extract_error_message_from_logfile_claude_format() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/claude.log");

    // Claude error format
    let log_content = r#"{"type":"init","session_id":"abc123"}
{"type":"message","content":"Working..."}
{"type":"error","message":"Rate limit exceeded"}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, Some("Rate limit exceeded".to_string()));
}

#[test]
fn test_extract_error_message_from_logfile_opencode_data_format() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/opencode.log");

    // OpenCode nested data format
    let log_content = r#"{"type":"init","sessionID":"ses_123"}
{"type":"error","error":{"data":{"message":"Nested error message"}}}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, Some("Nested error message".to_string()));
}

#[test]
fn test_extract_error_from_json_line_opencode_error_code() {
    // Test extraction of OpenCode error codes (usage_limit_exceeded, etc.)
    // Error codes are more stable than message text for detection
    let line = r#"{"type":"error","timestamp":1768191346712,"sessionID":"ses_123","error":{"code":"usage_limit_exceeded","message":"Usage limit reached"}}"#;
    let result = extract_error_identifier_from_json_line(line);
    assert_eq!(result, Some("usage_limit_exceeded".to_string()));
}

#[test]
fn test_extract_error_from_json_line_opencode_quota_exceeded_code() {
    // Test extraction of quota_exceeded error code
    let line =
        r#"{"type":"error","error":{"code":"quota_exceeded","message":"Quota limit reached"}}"#;
    let result = extract_error_identifier_from_json_line(line);
    assert_eq!(result, Some("quota_exceeded".to_string()));
}

#[test]
fn test_extract_error_from_json_line_opencode_usage_limit_reached_code() {
    // Test extraction of usage_limit_reached error code
    let line = r#"{"type":"error","error":{"code":"usage_limit_reached"}}"#;
    let result = extract_error_identifier_from_json_line(line);
    assert_eq!(result, Some("usage_limit_reached".to_string()));
}

#[test]
fn test_extract_error_from_json_line_opencode_provider_error() {
    // Test extraction of provider-specific errors (e.g., "anthropic: usage limit reached")
    // OpenCode multi-provider gateway forwards errors with provider prefix
    let line = r#"{"type":"error","timestamp":1768191346712,"sessionID":"ses_123","error":{"provider":"anthropic","message":"usage limit reached"}}"#;
    let result = extract_error_identifier_from_json_line(line);
    assert_eq!(result, Some("anthropic: usage limit reached".to_string()));
}

#[test]
fn test_extract_error_from_json_line_opencode_provider_error_openai() {
    // Test provider-specific error for OpenAI
    let line = r#"{"type":"error","error":{"provider":"openai","message":"usage limit exceeded"}}"#;
    let result = extract_error_identifier_from_json_line(line);
    assert_eq!(result, Some("openai: usage limit exceeded".to_string()));
}

#[test]
fn test_extract_error_from_json_line_opencode_provider_error_google() {
    // Test provider-specific error for Google
    let line = r#"{"type":"error","error":{"provider":"google","message":"quota exceeded"}}"#;
    let result = extract_error_identifier_from_json_line(line);
    assert_eq!(result, Some("google: quota exceeded".to_string()));
}

#[test]
fn test_extract_error_from_json_line_opencode_zen_error() {
    // Test OpenCode Zen usage limit error
    let line = r#"{"type":"error","timestamp":1768191346712,"error":{"message":"OpenCode Zen usage limit has been reached"}}"#;
    let result = extract_error_message_from_json_line(line);
    assert_eq!(
        result,
        Some("OpenCode Zen usage limit has been reached".to_string())
    );
}

#[test]
fn test_extract_error_from_json_line_error_code_priority() {
    // Test that error codes are extracted even when message is present
    // Error codes should have priority as they're more stable
    let line = r#"{"type":"error","error":{"code":"usage_limit_exceeded","message":"The usage limit has been reached [retryin]"}}"#;
    let result = extract_error_identifier_from_json_line(line);
    assert_eq!(result, Some("usage_limit_exceeded".to_string()));
}

#[test]
fn test_extract_error_message_from_logfile_multiple_formats() {
    // Test that we correctly extract errors from logs with multiple event types
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/test.log");

    let log_content = r#"{"type":"text","text":"Processing request..."}
{"type":"tool_use","tool":"read","state":{"status":"completed"}}
{"type":"error","error":{"code":"usage_limit_exceeded","message":"Usage limit reached"}}
{"type":"text","text":"Operation failed"}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, Some("Usage limit reached".to_string()));
}

#[test]
fn test_extract_error_identifier_from_logfile_prefers_code() {
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/test.log");

    let log_content = r#"{"type":"error","error":{"code":"usage_limit_exceeded","message":"Usage limit reached"}}"#;
    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_identifier_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, Some("usage_limit_exceeded".to_string()));
}

#[test]
fn test_extract_error_message_from_logfile_provider_specific() {
    // Test extraction of provider-specific error from logfile
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/opencode.log");

    let log_content = r#"{"type":"init","sessionID":"ses_123"}
{"type":"message","content":"Working..."}
{"type":"error","error":{"provider":"anthropic","message":"usage limit reached"}}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(result, Some("anthropic: usage limit reached".to_string()));
}

#[test]
fn test_extract_error_message_from_logfile_opencode_zen() {
    // Test extraction of OpenCode Zen usage limit error from logfile
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;

    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let logfile_path = std::path::Path::new(".agent/tmp/opencode.log");

    let log_content = r#"{"type":"init","sessionID":"ses_123"}
{"type":"message","content":"Processing..."}
{"type":"error","error":{"message":"OpenCode Zen usage limit has been reached"}}
"#;

    workspace.write(logfile_path, log_content).unwrap();

    let result = extract_error_message_from_logfile(logfile_path.to_str().unwrap(), &workspace);

    assert_eq!(
        result,
        Some("OpenCode Zen usage limit has been reached".to_string())
    );
}
