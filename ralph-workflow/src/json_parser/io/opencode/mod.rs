//! OpenCode I/O Boundary Module
//!
//! Contains streaming parsers and formatting code that requires mutation,
//! interior mutability (RefCell), and loops. This module is exempt from
//! functional programming lints per docs/code-style/boundaries.md.

use crate::common::truncate_text;
use crate::config::Verbosity;
use crate::json_parser::delta_display::TextDeltaRenderer;
use crate::json_parser::io::health::HealthMonitor;
#[cfg(any(test, feature = "test-utils"))]
use crate::json_parser::io::health::StreamingQualityMetrics;
use crate::json_parser::printer::SharedPrinter;
use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{format_tool_input, format_unknown_json_event, ContentType};
use crate::logger::{Colors, CHECK, CROSS};
use std::cell::{Cell, RefCell};
use std::io::{self, BufRead};
use std::path::Path;
use std::rc::Rc;

include!("../../opencode/event_types.rs");

include!("parser_core.rs");
include!("parser_stream.rs");

include!("formatting/step.rs");
include!("formatting/tool.rs");
include!("formatting/text_and_error.rs");
