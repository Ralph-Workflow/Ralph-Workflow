//! Codex I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

#![allow(clippy::all)]
#![allow(unsafe_code)]
#![allow(forbid_mut_binding)]
#![allow(forbid_imperative_loops)]
#![allow(forbid_mutating_receiver_methods)]
#![allow(forbid_interior_mutability)]

mod event_handlers;

use crate::config::Verbosity;
use crate::logger::Colors;
use crate::workspace::Workspace;
use std::cell::RefCell;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::rc::Rc;

use crate::json_parser::health::HealthMonitor;
#[cfg(any(test, feature = "test-utils"))]
use crate::json_parser::health::StreamingQualityMetrics;
use crate::json_parser::printer::SharedPrinter;
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{format_unknown_json_event, CodexEvent};

use event_handlers::{
    handle_error, handle_item_completed, handle_item_started, handle_thread_started,
    handle_turn_completed, handle_turn_failed, handle_turn_started, EventHandlerContext,
};

include!("parser.rs");
include!("event_parsing.rs");
include!("stream_parsing.rs");
