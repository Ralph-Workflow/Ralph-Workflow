//! Tests for delta display module.
//!
//! This module tests the delta display system including:
//! - Formatting (thinking, tool input)
//! - Text and thinking delta renderers
//! - Streaming sequence integration
//! - Sanitization utilities
//! - Prefix debouncing
//!
//! # Test Organization
//!
//! - `formatting_tests` - Formatter methods (`format_thinking`, `format_tool_input`)
//! - `text_renderer_tests` - `TextDeltaRenderer` implementation
//! - `thinking_renderer_tests` - `ThinkingDeltaRenderer` implementation
//! - `io_streaming_tests` - Full streaming sequences and prefix display
//! - `sanitize_tests` - Whitespace sanitization logic
//! - `renderer_tests` - General renderer behavior (newlines, whitespace)
//! - `config_tests` - Configuration and debouncer behavior

use super::*;

/// Helper function to create Colors with disabled ANSI colors for testing.
fn test_colors() -> Colors {
    Colors { enabled: false }
}

mod config_tests;
mod formatting_tests;
mod io_streaming_tests;
mod renderer_tests;
mod sanitize_tests;
mod text_renderer_tests;
mod thinking_renderer_tests;
