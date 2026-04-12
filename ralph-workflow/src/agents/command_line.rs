pub fn strip_claude_harness_args(cmd: &str) -> String {
    let tokens = crate::agents::session::command_policy::parse_command(cmd);
    if tokens.is_empty() {
        return String::new();
    }

    tokens
        .iter()
        .enumerate()
        .filter(|(idx, token)| {
            let previous = idx
                .checked_sub(1)
                .and_then(|previous_idx| tokens.get(previous_idx));
            let skipped_by_previous = previous
                .map(|prior| prior == "--settings" || prior == "--mcp-config")
                .unwrap_or(false);
            !skipped_by_previous
                && **token != "--settings"
                && **token != "--mcp-config"
                && **token != "--strict-mcp-config"
                && !token.starts_with("--settings=")
                && !token.starts_with("--mcp-config=")
        })
        .map(|(_, token)| token.clone())
        .collect::<Vec<_>>()
        .join(" ")
}

pub fn append_agent_command_args(
    base_cmd: &str,
    extra_cmd_args: &[String],
    strip_existing_claude_harness: bool,
) -> String {
    let normalized_base = if strip_existing_claude_harness {
        strip_claude_harness_args(base_cmd)
    } else {
        base_cmd.to_string()
    };
    if extra_cmd_args.is_empty() {
        normalized_base
    } else {
        format!("{} {}", normalized_base, extra_cmd_args.join(" "))
    }
}
