/// Handle `ItemStarted` event by delegating to type-specific handlers.
///
/// Returns `Some(output)` for events that should bypass the empty check (like streaming),
/// or `None` for events that should go through the normal empty check.
pub fn handle_item_started(
    ctx: &EventHandlerContext<'_>,
    item: Option<&CodexItem>,
) -> Option<String> {
    item.and_then(|item| match item.item_type.as_deref() {
        Some("command_execution") => {
            let output = handle_command_execution_started(ctx, item.command.clone());
            (!output.is_empty()).then_some(output)
        }
        Some("agent_message") => Some(handle_agent_message_started(ctx, item.text.as_ref())),
        Some("reasoning") => Some(handle_reasoning_started(ctx, item.text.as_ref())),
        Some("file_read") => {
            let output = handle_file_io_started(ctx, item.path.clone(), "file_read");
            (!output.is_empty()).then_some(output)
        }
        Some("file_write") => {
            let output = handle_file_io_started(ctx, item.path.clone(), "file_write");
            (!output.is_empty()).then_some(output)
        }
        Some("mcp_tool_call" | "mcp") => {
            let output = handle_mcp_tool_started(ctx, item.tool.as_ref(), item.arguments.as_ref());
            (!output.is_empty()).then_some(output)
        }
        Some("web_search") => {
            let output = handle_web_search_started(ctx, item.query.as_ref());
            (!output.is_empty()).then_some(output)
        }
        Some("plan_update") => {
            let output = handle_plan_update_started(ctx);
            (!output.is_empty()).then_some(output)
        }
        Some(t) => {
            let output = handle_unknown_item_started(ctx, Some(t.to_string()), item.path.clone());
            (!output.is_empty()).then_some(output)
        }
        None => None,
    })
}

/// Handle `ItemCompleted` event by delegating to type-specific handlers.
///
/// Returns `Some(output)` for events that should bypass the empty check (like streaming),
/// or `None` for events that should go through the normal empty check.
pub fn handle_item_completed(
    ctx: &EventHandlerContext<'_>,
    item: Option<&CodexItem>,
) -> Option<String> {
    item.and_then(|item| match item.item_type.as_deref() {
        Some("agent_message") => Some(handle_agent_message_completed(ctx, item.text.as_ref())),
        Some("reasoning") => Some(handle_reasoning_completed(ctx, item.text.as_ref())),
        Some("command_execution") => {
            let output = handle_command_execution_completed(ctx);
            (!output.is_empty()).then_some(output)
        }
        Some("file_change" | "file_write") => {
            let output = handle_file_write_completed(ctx, item.path.clone());
            (!output.is_empty()).then_some(output)
        }
        Some("file_read") => {
            let output = handle_file_read_completed(ctx, item.path.clone());
            (!output.is_empty()).then_some(output)
        }
        Some("mcp_tool_call" | "mcp") => {
            let output = handle_mcp_tool_completed(ctx, item.tool.clone());
            (!output.is_empty()).then_some(output)
        }
        Some("web_search") => {
            let output = handle_web_search_completed(ctx);
            (!output.is_empty()).then_some(output)
        }
        Some("plan_update") => {
            let output = handle_plan_update_completed(ctx, item.plan.as_ref());
            (!output.is_empty()).then_some(output)
        }
        _ => None,
    })
}
