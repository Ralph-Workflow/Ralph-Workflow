// Boundary module: streaming output parsing.
// File named io/streaming.rs — recognized as boundary module.
// Contains streaming output parsing that requires mutable parser state.

use crate::agents::JsonParserType;
use crate::common::split_command;
use crate::logger::argv_requests_json;
use crate::pipeline::prompt::types::{PipelineRuntime, PromptCommand};
use crate::rendering::json_pretty::format_generic_json_for_display;
use crate::runtime::streaming::{CancelAwareReceiverBufRead, StreamingLineReader};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU32};
use std::sync::Arc;
use std::time::Duration;

use crate::pipeline::idle_timeout::SharedActivityTimestamp;

/// Shared counter passed to parsers to signal active tool executions to the idle-timeout monitor.
/// Non-zero = at least one tool is actively executing. Incremented on tool start, saturating-
/// decremented on tool complete, reset to 0 on step/turn end.
pub type ToolActivityTracker = Arc<AtomicU32>;

type StdoutChunkResult = Result<Vec<u8>, std::io::Error>;
type StdoutTx = std::sync::mpsc::SyncSender<StdoutChunkResult>;
type StdoutRx = std::sync::mpsc::Receiver<StdoutChunkResult>;

pub fn create_stdout_channel() -> (StdoutTx, StdoutRx) {
    crate::runtime::streaming::create_stdout_channel()
}

pub fn spawn_stdout_pump(
    stdout: Box<dyn std::io::Read + Send>,
    activity_timestamp: SharedActivityTimestamp,
    tx: std::sync::mpsc::SyncSender<Result<Vec<u8>, std::io::Error>>,
    cancel: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    crate::runtime::streaming::spawn_stdout_pump(stdout, activity_timestamp, tx, cancel)
}

pub fn cleanup_stdout_pump(
    pump_handle: std::thread::JoinHandle<()>,
    cancel: &Arc<AtomicBool>,
    logger: &crate::logger::Logger,
    parse_result: &Result<(), std::io::Error>,
) {
    crate::runtime::streaming::cleanup_stdout_pump(pump_handle, cancel, logger, parse_result)
}

fn run_raw_lines_stream(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    reader: StreamingLineReader<CancelAwareReceiverBufRead>,
) -> Result<(), std::io::Error> {
    let logfile_path = Path::new(cmd.logfile);
    let stdout_io = std::io::stdout();
    let mut out = stdout_io.lock();
    for line in std::io::BufRead::lines(reader) {
        let line = line?;
        std::io::Write::write_fmt(&mut out, format_args!("{line}\n"))?;
        runtime
            .workspace
            .append_bytes(logfile_path, format!("{line}\n").as_bytes())?;
    }
    Ok(())
}

fn run_generic_json_stream(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    reader: StreamingLineReader<CancelAwareReceiverBufRead>,
) -> Result<(), std::io::Error> {
    let logfile_path = Path::new(cmd.logfile);
    let stdout_io = std::io::stdout();
    let mut out = stdout_io.lock();
    let mut buf = String::new();
    for line in std::io::BufRead::lines(reader) {
        let line = line?;
        runtime
            .workspace
            .append_bytes(logfile_path, format!("{line}\n").as_bytes())?;
        buf.push_str(&line);
        buf.push('\n');
    }
    let formatted = format_generic_json_for_display(&buf, runtime.config.verbosity);
    std::io::Write::write_all(&mut out, formatted.as_bytes())
}

fn run_claude_parser_stream(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    reader: StreamingLineReader<CancelAwareReceiverBufRead>,
    tool_activity_tracker: Option<ToolActivityTracker>,
) -> Result<(), std::io::Error> {
    let mut p = crate::json_parser::ClaudeParser::new(*runtime.colors, runtime.config.verbosity)
        .with_display_name(cmd.display_name)
        .with_log_file(cmd.logfile)
        .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
    if let Some(tracker) = tool_activity_tracker {
        p = p.with_tool_activity_tracker(tracker);
    }
    p.parse_stream(reader, runtime.workspace)
}

fn run_codex_parser_stream(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    reader: StreamingLineReader<CancelAwareReceiverBufRead>,
    tool_activity_tracker: Option<ToolActivityTracker>,
) -> Result<(), std::io::Error> {
    let mut p = crate::json_parser::CodexParser::new(*runtime.colors, runtime.config.verbosity)
        .with_display_name(cmd.display_name)
        .with_log_file(cmd.logfile)
        .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
    if let Some(tracker) = tool_activity_tracker {
        p = p.with_tool_activity_tracker(tracker);
    }
    p.parse_stream(reader, runtime.workspace)
}

fn run_gemini_parser_stream(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    reader: StreamingLineReader<CancelAwareReceiverBufRead>,
    _tool_activity_tracker: Option<ToolActivityTracker>,
) -> Result<(), std::io::Error> {
    // Gemini parser does not emit structured tool-lifecycle events; tracker is ignored.
    let mut p = crate::json_parser::GeminiParser::new(*runtime.colors, runtime.config.verbosity)
        .with_display_name(cmd.display_name)
        .with_log_file(cmd.logfile)
        .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
    p.parse_stream(reader, runtime.workspace)
}

fn run_opencode_parser_stream(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    reader: StreamingLineReader<CancelAwareReceiverBufRead>,
    tool_activity_tracker: Option<ToolActivityTracker>,
) -> Result<(), std::io::Error> {
    let mut p = crate::json_parser::OpenCodeParser::new(*runtime.colors, runtime.config.verbosity)
        .with_display_name(cmd.display_name)
        .with_log_file(cmd.logfile)
        .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
    if let Some(tracker) = tool_activity_tracker {
        p = p.with_tool_activity_tracker(tracker);
    }
    p.parse_stream(reader, runtime.workspace)
}

fn run_typed_parser_stream(
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    reader: StreamingLineReader<CancelAwareReceiverBufRead>,
    tool_activity_tracker: Option<ToolActivityTracker>,
) -> Result<(), std::io::Error> {
    match cmd.parser_type {
        JsonParserType::Claude => {
            run_claude_parser_stream(cmd, runtime, reader, tool_activity_tracker)
        }
        JsonParserType::Codex => {
            run_codex_parser_stream(cmd, runtime, reader, tool_activity_tracker)
        }
        JsonParserType::Gemini => {
            run_gemini_parser_stream(cmd, runtime, reader, tool_activity_tracker)
        }
        JsonParserType::OpenCode => {
            run_opencode_parser_stream(cmd, runtime, reader, tool_activity_tracker)
        }
        JsonParserType::Generic => run_generic_json_stream(cmd, runtime, reader),
    }
}

fn should_use_json_parser(cmd: &PromptCommand<'_>) -> Result<bool, std::io::Error> {
    Ok(cmd.parser_type != JsonParserType::Generic
        || argv_requests_json(&split_command(cmd.cmd_str)?))
}

pub fn stream_agent_output_from_handle(
    stdout: Box<dyn std::io::Read + Send>,
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    activity_timestamp: SharedActivityTimestamp,
    cancel: &Arc<AtomicBool>,
    tool_activity_tracker: Option<ToolActivityTracker>,
) -> Result<(), std::io::Error> {
    let (tx, rx) = create_stdout_channel();
    let pump_handle = spawn_stdout_pump(stdout, activity_timestamp, tx, Arc::clone(cancel));

    let receiver_reader =
        CancelAwareReceiverBufRead::new(rx, Arc::clone(cancel), Duration::from_millis(50));
    let reader = StreamingLineReader::new(receiver_reader);

    let parse_result = if should_use_json_parser(cmd)? {
        run_typed_parser_stream(cmd, runtime, reader, tool_activity_tracker)
    } else {
        run_raw_lines_stream(cmd, runtime, reader)
    };

    cleanup_stdout_pump(pump_handle, cancel, runtime.logger, &parse_result);
    parse_result
}
