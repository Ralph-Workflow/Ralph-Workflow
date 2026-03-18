//! Event handlers for Codex parser.
//!
//! This module contains individual handler functions for each `CodexEvent` variant.
//! Each handler is responsible for formatting the output for its specific event type.

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::logger::{Colors, CHECK, CROSS};
use std::cell::RefCell;
use std::rc::Rc;

use crate::json_parser::delta_display::{
    sanitize_for_display, DeltaRenderer, TextDeltaRenderer, ThinkingDeltaRenderer,
};
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{
    format_tool_input, CodexItem, CodexUsage, ContentType, DeltaAccumulator,
};

include!("event_handlers/context.rs");
include!("event_handlers/item_dispatch.rs");
include!("event_handlers/turn.rs");
include!("event_handlers/items_started.rs");
include!("event_handlers/items_completed.rs");
include!("event_handlers/error.rs");
