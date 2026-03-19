use super::*;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::io::{self, Cursor, Read};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;

mod archive_filename;
mod io_spawn_idle_timeout;
mod io_spawn_logfile;
mod io_spawn_streaming_error;
mod io_stderr_collector;
mod io_stdout_cancel;
mod io_streaming_line_reader;
mod truncate;

fn test_logger() -> Logger {
    Logger::new(Colors::new())
}
