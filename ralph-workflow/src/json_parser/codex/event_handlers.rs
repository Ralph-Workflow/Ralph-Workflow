use crate::common::truncate_text;
use crate::json_parser::codex::event_interpretation::{
    compute_reasoning_incremental_delta, interpret_item_completed_type,
    interpret_item_started_type, ItemCompletedInterpretation, ItemStartedInterpretation,
};
use crate::json_parser::delta_display::{
    sanitize_for_display, DeltaRenderer, TextDeltaRenderer, ThinkingDeltaRenderer,
};
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::{
    format_dim_continuation_line, format_token_counts, format_tokens_suffix, format_tool_input,
    CodexItem, CodexUsage, ContentType,
};
use crate::logger::{CHECK, CROSS};

#[cfg(any(test, debug_assertions))]
use std::io::Write;
pub struct EventHandlerContext<'a> {
    pub colors: &'a crate::logger::Colors,
    pub verbosity: crate::config::Verbosity,
    pub display_name: &'a str,
    pub streaming_session:
        &'a std::rc::Rc<std::cell::RefCell<crate::json_parser::streaming_state::StreamingSession>>,
    pub reasoning_accumulator:
        &'a std::rc::Rc<std::cell::RefCell<crate::json_parser::types::DeltaAccumulator>>,
    pub terminal_mode: crate::json_parser::terminal::TerminalMode,
    pub show_streaming_metrics: bool,
    pub last_rendered_content: &'a std::cell::RefCell<std::collections::HashMap<String, String>>,
}

impl<'a> EventHandlerContext<'a> {
    pub fn with_session_mut<R>(
        &self,
        f: impl FnOnce(&mut crate::json_parser::streaming_state::StreamingSession) -> R,
    ) -> R {
        f(&mut self.streaming_session.borrow_mut())
    }
    pub fn with_reasoning_accumulator_mut<R>(
        &self,
        f: impl FnOnce(&mut crate::json_parser::types::DeltaAccumulator) -> R,
    ) -> R {
        f(&mut self.reasoning_accumulator.borrow_mut())
    }
    pub fn with_last_rendered_content_mut<R>(
        &self,
        f: impl FnOnce(&mut std::collections::HashMap<String, String>) -> R,
    ) -> R {
        f(&mut self.last_rendered_content.borrow_mut())
    }
}
pub fn handle_item_started(
    ctx: &EventHandlerContext<'_>,
    item: Option<&CodexItem>,
) -> Option<String> {
    item.and_then(
        |item| match interpret_item_started_type(item.item_type.as_deref()) {
            Some(ItemStartedInterpretation::CommandExecution) => {
                let output = handle_command_execution_started(ctx, item.command.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::AgentMessage) => {
                Some(handle_agent_message_started(ctx, item.text.as_ref()))
            }
            Some(ItemStartedInterpretation::Reasoning) => {
                Some(handle_reasoning_started(ctx, item.text.as_ref()))
            }
            Some(ItemStartedInterpretation::FileRead) => {
                let output = handle_file_io_started(ctx, item.path.clone(), "file_read");
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::FileWrite) => {
                let output = handle_file_io_started(ctx, item.path.clone(), "file_write");
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::McpTool) => {
                let output =
                    handle_mcp_tool_started(ctx, item.tool.as_ref(), item.arguments.as_ref());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::WebSearch) => {
                let output = handle_web_search_started(ctx, item.query.as_ref());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::PlanUpdate) => {
                let output = handle_plan_update_started(ctx);
                (!output.is_empty()).then_some(output)
            }
            Some(ItemStartedInterpretation::Unknown(item_type)) => {
                let output = handle_unknown_item_started(ctx, Some(item_type), item.path.clone());
                (!output.is_empty()).then_some(output)
            }
            None => None,
        },
    )
}
pub fn handle_item_completed(
    ctx: &EventHandlerContext<'_>,
    item: Option<&CodexItem>,
) -> Option<String> {
    item.and_then(
        |item| match interpret_item_completed_type(item.item_type.as_deref()) {
            Some(ItemCompletedInterpretation::AgentMessage) => {
                Some(handle_agent_message_completed(ctx, item.text.as_ref()))
            }
            Some(ItemCompletedInterpretation::Reasoning) => {
                Some(handle_reasoning_completed(ctx, item.text.as_ref()))
            }
            Some(ItemCompletedInterpretation::CommandExecution) => {
                let output = handle_command_execution_completed(ctx);
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::FileWrite) => {
                let output = handle_file_write_completed(ctx, item.path.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::FileRead) => {
                let output = handle_file_read_completed(ctx, item.path.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::McpTool) => {
                let output = handle_mcp_tool_completed(ctx, item.tool.clone());
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::WebSearch) => {
                let output = handle_web_search_completed(ctx);
                (!output.is_empty()).then_some(output)
            }
            Some(ItemCompletedInterpretation::PlanUpdate) => {
                let output = handle_plan_update_completed(ctx, item.plan.as_ref());
                (!output.is_empty()).then_some(output)
            }
            _ => None,
        },
    )
}
pub fn handle_thread_started(ctx: &EventHandlerContext<'_>, thread_id: Option<String>) -> String {
    let tid = thread_id.unwrap_or_else(|| "unknown".to_string());

    // Thread start indicates a new logical stream in Codex; reset any append-only tracking
    // so subsequent deltas start fresh.
    ctx.last_rendered_content.borrow_mut().clear();

    ctx.streaming_session
        .borrow_mut()
        .set_current_message_id(Some(tid.clone()));
    let hash_display = crate::json_parser::types::format_short_hash(&tid);
    let hash_suffix = if hash_display.is_empty() {
        String::new()
    } else {
        format!(
            " {}{}{}",
            ctx.colors.dim(),
            hash_display,
            ctx.colors.reset()
        )
    };
    format!(
        "{}[{}]{} {}Thread started{}{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.cyan(),
        ctx.colors.reset(),
        hash_suffix
    )
}

pub fn handle_turn_started(ctx: &EventHandlerContext<'_>, turn_id: String) -> String {
    ctx.streaming_session.borrow_mut().on_message_start();
    let mut acc = ctx.reasoning_accumulator.borrow_mut();
    let placeholder = crate::json_parser::types::DeltaAccumulator::new();
    let old = std::mem::replace(&mut *acc, placeholder);
    let new = old.clear();
    *acc = new;

    // Each Codex turn is a new logical stream. Clear append-only renderer state so the
    // first delta of the new turn re-emits the prefix/label instead of computing a suffix
    // against the previous turn's content.
    ctx.last_rendered_content.borrow_mut().clear();

    ctx.streaming_session
        .borrow_mut()
        .set_current_message_id(Some(turn_id));
    format!(
        "{}[{}]{} {}Turn started{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.blue(),
        ctx.colors.reset()
    )
}

pub fn handle_turn_completed(ctx: &EventHandlerContext<'_>, usage: Option<CodexUsage>) -> String {
    let was_in_block = ctx.streaming_session.borrow_mut().on_message_stop();
    let (input, output) = usage.map_or((0, 0), |u| {
        (u.input_tokens.unwrap_or(0), u.output_tokens.unwrap_or(0))
    });
    let completion = if was_in_block {
        TextDeltaRenderer::render_completion(ctx.terminal_mode)
    } else {
        String::new()
    };
    let tokens_str = format_token_counts(input, output, 0, 0);
    let tokens_suffix = format_tokens_suffix(&tokens_str);
    format!(
        "{}{}[{}]{} {}{} Turn completed{}{}{}{}\n",
        completion,
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.green(),
        CHECK,
        ctx.colors.reset(),
        ctx.colors.dim(),
        tokens_suffix,
        ctx.colors.reset()
    )
}

pub fn handle_turn_failed(ctx: &EventHandlerContext<'_>, error: Option<String>) -> String {
    let was_in_block = ctx.streaming_session.borrow_mut().on_message_stop();
    let completion = if was_in_block {
        TextDeltaRenderer::render_completion(ctx.terminal_mode)
    } else {
        String::new()
    };
    let err = error.unwrap_or_else(|| "unknown error".to_string());
    format!(
        "{}{}[{}]{} {}{} Turn failed:{} {}\n",
        completion,
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.red(),
        CROSS,
        ctx.colors.reset(),
        err
    )
}

pub fn handle_command_execution_started(
    ctx: &EventHandlerContext<'_>,
    command: Option<String>,
) -> String {
    let cmd = command.unwrap_or_default();
    let limit = ctx.verbosity.truncate_limit("command");
    let preview = truncate_text(&cmd, limit);
    format!(
        "{}[{}]{} {}Exec{}: {}{}{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.magenta(),
        ctx.colors.reset(),
        ctx.colors.white(),
        preview,
        ctx.colors.reset()
    )
}

pub fn handle_file_io_started(
    ctx: &EventHandlerContext<'_>,
    path: Option<String>,
    action: &str,
) -> String {
    let path = path.unwrap_or_default();
    format!(
        "{}[{}]{} {}{}:{} {}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.yellow(),
        action,
        ctx.colors.reset(),
        path
    )
}

fn maybe_format_mcp_tool_input(
    ctx: &EventHandlerContext<'_>,
    arguments: Option<&serde_json::Value>,
) -> String {
    arguments
        .filter(|_| ctx.verbosity.show_tool_input())
        .map_or_else(String::new, |args| {
            let args_str = format_tool_input(args);
            let limit = ctx.verbosity.truncate_limit("tool_input");
            let preview = truncate_text(&args_str, limit);
            if preview.is_empty() {
                String::new()
            } else {
                // TerminalMode::None must not emit ANSI codes even when colors are enabled.
                match ctx.terminal_mode {
                    TerminalMode::None => {
                        format!("[{}]   \u{2514}\u{2500} {}\n", ctx.display_name, preview)
                    }
                    _ => format_dim_continuation_line(&preview, ctx.display_name, *ctx.colors),
                }
            }
        })
}

pub fn handle_mcp_tool_started(
    ctx: &EventHandlerContext<'_>,
    tool_name: Option<&String>,
    arguments: Option<&serde_json::Value>,
) -> String {
    let default = String::from("unknown");
    let tool_name = tool_name.unwrap_or(&default);

    let base = match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}MCP Tool{}: {}{}{}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.magenta(),
            ctx.colors.reset(),
            ctx.colors.bold(),
            tool_name,
            ctx.colors.reset()
        ),
        TerminalMode::None => format!("[{}] MCP Tool: {}\n", ctx.display_name, tool_name),
    };

    let tool_input = maybe_format_mcp_tool_input(ctx, arguments);
    format!("{base}{tool_input}")
}

pub fn handle_web_search_started(ctx: &EventHandlerContext<'_>, query: Option<&String>) -> String {
    let default = String::new();
    let query = query.unwrap_or(&default);
    let limit = ctx.verbosity.truncate_limit("command");
    let preview = truncate_text(query, limit);
    format!(
        "{}[{}]{} {}Search{}: {}{}{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.cyan(),
        ctx.colors.reset(),
        ctx.colors.white(),
        preview,
        ctx.colors.reset()
    )
}

pub fn handle_plan_update_started(ctx: &EventHandlerContext<'_>) -> String {
    format!(
        "{}[{}]{} {}Updating plan...{}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.blue(),
        ctx.colors.reset()
    )
}

pub fn handle_unknown_item_started(
    ctx: &EventHandlerContext<'_>,
    item_type: Option<String>,
    path: Option<String>,
) -> String {
    if ctx.verbosity.is_verbose() {
        if let Some(t) = item_type {
            return format!(
                "{}[{}]{} {}{}:{} {}\n",
                ctx.colors.dim(),
                ctx.display_name,
                ctx.colors.reset(),
                ctx.colors.dim(),
                t,
                ctx.colors.reset(),
                path.unwrap_or_default()
            );
        }
    }
    String::new()
}

pub fn handle_command_execution_completed(ctx: &EventHandlerContext<'_>) -> String {
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}{} Command done{}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset()
        ),
        TerminalMode::None => format!("[{}] Command done\n", ctx.display_name),
    }
}

pub fn handle_file_write_completed(ctx: &EventHandlerContext<'_>, path: Option<String>) -> String {
    let path = path.unwrap_or_else(|| "unknown".to_string());
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}File{}: {}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.yellow(),
            ctx.colors.reset(),
            path
        ),
        TerminalMode::None => format!("[{}] File: {}\n", ctx.display_name, path),
    }
}

pub fn handle_file_read_completed(ctx: &EventHandlerContext<'_>, path: Option<String>) -> String {
    if ctx.verbosity.is_verbose() {
        let path = path.unwrap_or_else(|| "unknown".to_string());
        format!(
            "{}[{}]{} {}{} Read:{} {}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset(),
            path
        )
    } else {
        String::new()
    }
}

pub fn handle_mcp_tool_completed(
    ctx: &EventHandlerContext<'_>,
    tool_name: Option<String>,
) -> String {
    let tool_name = tool_name.unwrap_or_else(|| "tool".to_string());
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}{} MCP:{} {} done\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset(),
            tool_name
        ),
        TerminalMode::None => format!("[{}] MCP: {} done\n", ctx.display_name, tool_name),
    }
}

pub fn handle_web_search_completed(ctx: &EventHandlerContext<'_>) -> String {
    match ctx.terminal_mode {
        TerminalMode::Full | TerminalMode::Basic => format!(
            "{}[{}]{} {}{} Search completed{}\n",
            ctx.colors.dim(),
            ctx.display_name,
            ctx.colors.reset(),
            ctx.colors.green(),
            CHECK,
            ctx.colors.reset()
        ),
        TerminalMode::None => format!("[{}] Search completed\n", ctx.display_name),
    }
}

pub fn handle_plan_update_completed(
    ctx: &EventHandlerContext<'_>,
    plan: Option<&String>,
) -> String {
    if ctx.verbosity.is_verbose() {
        let limit = ctx.verbosity.truncate_limit("text");
        plan.map_or_else(
            || match ctx.terminal_mode {
                TerminalMode::Full | TerminalMode::Basic => format!(
                    "{}[{}]{} {}{} Plan updated{}\n",
                    ctx.colors.dim(),
                    ctx.display_name,
                    ctx.colors.reset(),
                    ctx.colors.green(),
                    CHECK,
                    ctx.colors.reset()
                ),
                TerminalMode::None => format!("[{}] Plan updated\n", ctx.display_name),
            },
            |plan| {
                let preview = truncate_text(plan, limit);
                match ctx.terminal_mode {
                    TerminalMode::Full | TerminalMode::Basic => format!(
                        "{}[{}]{} {}Plan:{} {}\n",
                        ctx.colors.dim(),
                        ctx.display_name,
                        ctx.colors.reset(),
                        ctx.colors.blue(),
                        ctx.colors.reset(),
                        preview
                    ),
                    TerminalMode::None => format!("[{}] Plan: {}\n", ctx.display_name, preview),
                }
            },
        )
    } else {
        String::new()
    }
}

pub fn handle_error(
    ctx: &EventHandlerContext<'_>,
    message: Option<String>,
    error: Option<String>,
) -> String {
    let err = message
        .or(error)
        .unwrap_or_else(|| "unknown error".to_string());
    format!(
        "{}[{}]{} {}{} Error:{} {}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.red(),
        CROSS,
        ctx.colors.reset(),
        err
    )
}

include!("event_handlers_agent_message.rs");
