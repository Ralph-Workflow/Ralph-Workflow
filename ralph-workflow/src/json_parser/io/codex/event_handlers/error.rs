/// Handle `Error` event.
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
