fn render_cloud_pr_title_and_body(state: &PipelineState) -> (String, String) {
    use std::collections::HashMap;

    let run_id = state.cloud.run_id.as_deref().unwrap_or("unknown");

    // Intentionally avoid using any prompt text or other potentially sensitive input.
    // This value is safe to publish in a PR title/body.
    let prompt_summary = format!("Ralph workflow run {run_id}");

    let vars: HashMap<_, _> = [
        ("run_id", run_id.to_string()),
        ("prompt_summary", prompt_summary),
    ]
    .into_iter()
    .collect();

    let default_title = "Ralph workflow changes".to_string();

    let title = state
        .cloud
        .git_remote
        .pr_title_template
        .as_deref()
        .and_then(|t| try_render_cloud_pr_template(t, &vars))
        .unwrap_or(default_title);

    let body = state
        .cloud
        .git_remote
        .pr_body_template
        .as_deref()
        .and_then(|t| try_render_cloud_pr_template(t, &vars))
        .unwrap_or_default();

    (title, body)
}

fn try_render_cloud_pr_template(
    template: &str,
    vars: &std::collections::HashMap<&str, String>,
) -> Option<String> {
    let converted = convert_cloud_pr_template_placeholders(template)?;

    let partials: std::collections::HashMap<String, String> = std::iter::empty().collect(); // Empty - no partials for cloud PR templates
    let t = crate::prompts::template_engine::Template::new(&converted);
    t.render_with_partials(vars, &partials).ok()
}

fn convert_cloud_pr_template_placeholders(input: &str) -> Option<String> {
    // Supported placeholders are documented as {run_id} and {prompt_summary}.
    // We render them using the existing template engine's {{var}} syntax.
    const ALLOWED: [&str; 2] = ["run_id", "prompt_summary"];

    let mut out = String::with_capacity(input.len());
    let mut chars = input.chars().peekable();

    while let Some(ch) = chars.next() {
        if ch != '{' {
            out.push(ch);
            continue;
        }

        // Preserve template-engine escapes/variables like {{run_id}}.
        if chars.peek() == Some(&'{') {
            out.push('{');
            out.push('{');
            let _ = chars.next();
            continue;
        }

        let mut name = String::new();
        while let Some(&next) = chars.peek() {
            if next == '}' {
                break;
            }
            name.push(next);
            let _ = chars.next();
        }

        // No closing brace; treat as literal.
        if chars.peek() != Some(&'}') {
            out.push('{');
            out.push_str(&name);
            continue;
        }
        let _ = chars.next();

        let trimmed = name.trim();
        if is_simple_placeholder_name(trimmed) {
            if ALLOWED.contains(&trimmed) {
                out.push_str("{{");
                out.push_str(trimmed);
                out.push_str("}}");
            } else {
                // Fail-fast: unknown placeholders must not pass through verbatim.
                return None;
            }
        } else {
            // Not a placeholder shape; keep original braces.
            out.push('{');
            out.push_str(&name);
            out.push('}');
        }
    }

    Some(out)
}

fn is_simple_placeholder_name(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}
