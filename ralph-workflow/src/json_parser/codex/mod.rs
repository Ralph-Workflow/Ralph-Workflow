//! Codex CLI JSON parser.
//!
//! This module provides the functional core implementation of the Codex parser.

use crate::config::Verbosity;
use crate::logger::Colors;
use crate::workspace::Workspace;
use std::cell::RefCell;
use std::io::{self, BufRead};
use std::path::PathBuf;
use std::rc::Rc;

use crate::json_parser::health::HealthMonitor;
use crate::json_parser::printer::SharedPrinter;
use crate::json_parser::types::{format_unknown_json_event, CodexEvent};

pub mod event_handlers;

include!("parser.rs");
include!("stream_parsing.rs");
include!("event_parsing.rs");
include!("event_handlers.rs");
