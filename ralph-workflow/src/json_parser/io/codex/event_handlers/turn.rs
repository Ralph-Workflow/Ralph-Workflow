/// Handle `ThreadStarted` event.
pub fn handle_thread_started(ctx: &EventHandlerContext<'_>, thread_id: Option<String>) -> String {
    let tid = thread_id.unwrap_or_else(|| "unknown".to_string());

    // Thread start indicates a new logical stream in Codex; reset any append-only tracking
    // so subsequent deltas start fresh.
    ctx.last_rendered_content.borrow_mut().clear();

    ctx.streaming_session
        .borrow_mut()
        .set_current_message_id(Some(tid.clone()));
    format!(
        "{}[{}]{} {}Thread started{} {}({:.8}...){}\n",
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.cyan(),
        ctx.colors.reset(),
        ctx.colors.dim(),
        tid,
        ctx.colors.reset()
    )
}

/// Handle `TurnStarted` event.
pub fn handle_turn_started(ctx: &EventHandlerContext<'_>, turn_id: String) -> String {
    ctx.streaming_session.borrow_mut().on_message_start();
    ctx.reasoning_accumulator.borrow_mut().clear();

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

/// Handle `TurnCompleted` event.
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
    format!(
        "{}{}[{}]{} {}{} Turn completed{} {}(in:{} out:{}){}\n",
        completion,
        ctx.colors.dim(),
        ctx.display_name,
        ctx.colors.reset(),
        ctx.colors.green(),
        CHECK,
        ctx.colors.reset(),
        ctx.colors.dim(),
        input,
        output,
        ctx.colors.reset()
    )
}

/// Handle `TurnFailed` event.
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
