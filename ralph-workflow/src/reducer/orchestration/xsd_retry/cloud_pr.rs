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
    const ALLOWED: [&str; 2] = ["run_id", "prompt_summary"];

    fn parse_char_by_char(chars: &[char], pos: usize) -> Option<String> {
        if pos >= chars.len() {
            return Some(String::new());
        }

        let ch = chars[pos];

        if ch == '}' {
            return parse_char_by_char(chars, pos + 1);
        }

        if ch == '{' && pos + 1 < chars.len() && chars[pos + 1] == '{' {
            return Some(String::from("{") + &parse_char_by_char(chars, pos + 2)?);
        }

        if ch == '{' {
            let name_end = chars[pos..]
                .iter()
                .position(|&c| c == '}')
                .map(|offset| pos + offset);

            let name: String = match name_end {
                Some(end) => chars[pos..end].iter().collect(),
                None => {
                    return Some(format!(
                        "{{{rest}",
                        rest = parse_char_by_char(chars, pos + 1)?
                    ));
                }
            };

            let trimmed = name.trim();
            let replacement = if is_simple_placeholder_name(trimmed) && ALLOWED.contains(&trimmed) {
                format!("{{{trimmed}}}")
            } else if is_simple_placeholder_name(trimmed) {
                return None;
            } else {
                format!("{{{name}}}")
            };

            let end = name_end.unwrap() + 1;
            return Some(format!(
                "{replacement}{rest}",
                rest = parse_char_by_char(chars, end)?
            ));
        }

        Some(format!(
            "{ch}{rest}",
            rest = parse_char_by_char(chars, pos + 1)?
        ))
    }

    let chars: Vec<char> = input.chars().collect();
    parse_char_by_char(&chars, 0)
}

fn is_simple_placeholder_name(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}
