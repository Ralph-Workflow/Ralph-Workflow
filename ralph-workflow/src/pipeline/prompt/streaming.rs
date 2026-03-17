//! Streaming I/O for agent output.
//!
//! This module re-exports streaming utilities from the runtime boundary module.

mod error_extraction;

pub(super) use error_extraction::extract_session_id_from_logfile;
pub use error_extraction::{
    extract_error_identifier_from_logfile, extract_error_message_from_logfile,
};

// Re-export private helpers for use in tests (streaming/tests.rs uses `super::*`)
#[cfg(test)]
use error_extraction::{
    extract_error_identifier_from_json_line, extract_error_message_from_json_line,
};

use super::types::{PipelineRuntime, PromptCommand};
use crate::agents::JsonParserType;
use crate::common::split_command;
use crate::logger::argv_requests_json;
use crate::rendering::json_pretty::format_generic_json_for_display;

use std::io::{self, BufRead, Write};
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Duration;

use crate::pipeline::idle_timeout::SharedActivityTimestamp;

// Re-export from runtime boundary module
pub use crate::runtime::streaming::StreamingLineReader;
pub use crate::runtime::streaming::{
    cleanup_stdout_pump, create_stdout_channel, spawn_stdout_pump, CancelAwareReceiverBufRead,
};

/// Stream agent output from an `AgentChildHandle`.
///
/// This function streams the agent's stdout in real-time, parsing JSON
/// output based on the parser type, and tracking activity for idle timeout detection.
pub(super) fn stream_agent_output_from_handle(
    stdout: Box<dyn io::Read + Send>,
    cmd: &PromptCommand<'_>,
    runtime: &PipelineRuntime<'_>,
    activity_timestamp: SharedActivityTimestamp,
    cancel: &Arc<AtomicBool>,
) -> io::Result<()> {
    // Bounded channel: prevents unbounded buffering when stdout pumping outpaces parsing.
    //
    // Backpressure is acceptable here: if the parser stalls, we prefer blocking the pump thread
    // (and therefore the child stdout pipe) over unbounded memory growth.
    let (tx, rx) = create_stdout_channel();
    let pump_handle = spawn_stdout_pump(stdout, activity_timestamp, tx, Arc::clone(cancel));

    // Cancel-aware buffering: lets the main thread stop parsing promptly when the
    // idle-timeout monitor fires, even if the underlying stdout read is blocked.
    let receiver_reader =
        CancelAwareReceiverBufRead::new(rx, Arc::clone(cancel), Duration::from_millis(50));
    let reader = StreamingLineReader::new(receiver_reader);

    let parse_result = (|| {
        if cmd.parser_type != JsonParserType::Generic
            || argv_requests_json(&split_command(cmd.cmd_str)?)
        {
            let stdout_io = io::stdout();
            let mut out = stdout_io.lock();

            match cmd.parser_type {
                JsonParserType::Claude => {
                    let p = crate::json_parser::ClaudeParser::new(
                        *runtime.colors,
                        runtime.config.verbosity,
                    )
                    .with_display_name(cmd.display_name)
                    .with_log_file(cmd.logfile)
                    .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
                    p.parse_stream(reader, runtime.workspace)?;
                }
                JsonParserType::Codex => {
                    let p = crate::json_parser::CodexParser::new(
                        *runtime.colors,
                        runtime.config.verbosity,
                    )
                    .with_display_name(cmd.display_name)
                    .with_log_file(cmd.logfile)
                    .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
                    p.parse_stream(reader, runtime.workspace)?;
                }
                JsonParserType::Gemini => {
                    let p = crate::json_parser::GeminiParser::new(
                        *runtime.colors,
                        runtime.config.verbosity,
                    )
                    .with_display_name(cmd.display_name)
                    .with_log_file(cmd.logfile)
                    .with_show_streaming_metrics(runtime.config.show_streaming_metrics);
                    p.parse_stream(reader, runtime.workspace)?;
                }
                JsonParserType::OpenCode => {
                    let p = crate::json_parser::OpenCodeParser::new(
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
                    for line in reader.lines() {
                        let line = line?;
                        runtime
                            .workspace
                            .append_bytes(logfile_path, format!("{line}\n").as_bytes())?;
                        buf.push_str(&line);
                        buf.push('\n');
                    }

                    let formatted = format_generic_json_for_display(&buf, runtime.config.verbosity);
                    out.write_all(formatted.as_bytes())?;
                }
            }
        } else {
            let logfile_path = Path::new(cmd.logfile);
            let stdout_io = io::stdout();
            let mut out = stdout_io.lock();

            for line in reader.lines() {
                let line = line?;
                writeln!(out, "{line}")?;
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

#[cfg(test)]
mod tests;
