//! Codex CLI JSON parser.
//!
//! This module provides the functional core implementation of the Codex parser.

use crate::config::Verbosity;
use crate::logger::Colors;
use crate::workspace::Workspace;
use std::io::{BufRead, Write};
use std::path::PathBuf;

use crate::json_parser::health::monitor::HealthMonitor;
use crate::json_parser::types::{format_unknown_json_event, CodexEvent};

pub mod event_handlers;

pub mod io;

use event_handlers::{
    handle_error, handle_item_completed, handle_item_started, handle_thread_started,
    handle_turn_completed, handle_turn_failed, handle_turn_started, EventHandlerContext,
};

include!("parser.rs");
include!("stream_parsing.rs");
include!("event_parsing.rs");
