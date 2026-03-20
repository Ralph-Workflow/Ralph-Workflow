//! Streaming I/O for agent output.
//!
//! This module provides streaming utilities for agent output.

mod error_extraction;

pub use error_extraction::{
    extract_error_identifier_from_logfile, extract_error_message_from_logfile,
    extract_session_id_from_logfile,
};

use crate::agents::JsonParserType;
use crate::common::split_command;
use crate::logger::argv_requests_json;
use crate::pipeline::prompt::types::{PipelineRuntime, PromptCommand};
use crate::rendering::json_pretty::format_generic_json_for_display;

use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Duration;

use crate::pipeline::idle_timeout::SharedActivityTimestamp;

pub type StreamingLineReader<R> = crate::runtime::streaming::StreamingLineReader<R>;
pub type CancelAwareReceiverBufRead = crate::runtime::streaming::CancelAwareReceiverBufRead;

pub fn create_stdout_channel() -> (
    std::sync::mpsc::SyncSender<Result<Vec<u8>, std::io::Error>>,
    std::sync::mpsc::Receiver<Result<Vec<u8>, std::io::Error>>,
) {
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

pub(crate) fn stream_agent_output_from_handle(
    stdout: Box<dyn std::io::Read + Send>,
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    activity_timestamp: SharedActivityTimestamp,
    cancel: &Arc<AtomicBool>,
) -> Result<(), std::io::Error> {
    let (tx, rx) = create_stdout_channel();
    let pump_handle = spawn_stdout_pump(stdout, activity_timestamp, tx, Arc::clone(cancel));

    let receiver_reader =
        CancelAwareReceiverBufRead::new(rx, Arc::clone(cancel), Duration::from_millis(50));
    let reader = StreamingLineReader::new(receiver_reader);

    let parse_result = (|| {
        if cmd.parser_type != JsonParserType::Generic
            || argv_requests_json(&split_command(cmd.cmd_str)?)
        {
            let stdout_io = std::io::stdout();
            let mut out = stdout_io.lock();

            match cmd.parser_type {
                JsonParserType::Claude => {
                    let mut p = crate::json_parser::ClaudeParser::new(
                        *runtime.colors,
                        runtime.config.verbosity,
                    )
                    .with_display_name(cmd.display_name)
                    .with_log_file(cmd.logfile)
                    .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
                    p.parse_stream(reader, runtime.workspace)?;
                }
                JsonParserType::Codex => {
                    let mut p = crate::json_parser::CodexParser::new(
                        *runtime.colors,
                        runtime.config.verbosity,
                    )
                    .with_display_name(cmd.display_name)
                    .with_log_file(cmd.logfile)
                    .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
                    p.parse_stream(reader, runtime.workspace)?;
                }
                JsonParserType::Gemini => {
                    let mut p = crate::json_parser::GeminiParser::new(
                        *runtime.colors,
                        runtime.config.verbosity,
                    )
                    .with_display_name(cmd.display_name)
                    .with_log_file(cmd.logfile)
                    .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
                    p.parse_stream(reader, runtime.workspace)?;
                }
                JsonParserType::OpenCode => {
                    let mut p = crate::json_parser::OpenCodeParser::new(
                        *runtime.colors,
                        runtime.config.verbosity,
                    )
                    .with_display_name(cmd.display_name)
                    .with_log_file(cmd.logfile)
                    .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
                    p.parse_stream(reader, runtime.workspace)?;
                }
                JsonParserType::Generic => {
                    let logfile_path = Path::new(cmd.logfile);
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
                    std::io::Write::write_all(&mut out, formatted.as_bytes())?;
                }
            }
        } else {
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
        }

        Ok(())
    })();

    cleanup_stdout_pump(pump_handle, cancel, runtime.logger, &parse_result);
    parse_result
}
