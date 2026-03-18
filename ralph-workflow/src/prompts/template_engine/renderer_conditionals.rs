// Template conditional processing: {% if %} / {% endif %} blocks.

impl Template {
    /// Process conditionals in the content based on variable values.
    ///
    /// Supports:
    /// - `{% if VARIABLE %}...{% endif %}` - show content if VARIABLE is truthy
    /// - `{% if !VARIABLE %}...{% endif %}` - show content if VARIABLE is falsy
    ///
    /// A variable is considered "truthy" if it exists and is non-empty.
    fn process_conditionals(content: &str, variables: &HashMap<&str, String>) -> String {
        // Find all {% if ... %} blocks with their positions
        let if_blocks: Vec<(usize, usize, usize, usize, usize)> = content
            .match_indices("{% if ")
            .filter_map(|(start, _)| {
                let if_end_start = start + 6;
                let if_end = content[if_end_start..]
                    .find("%}")
                    .map(|pos| if_end_start + pos + 2)?;

                let _condition = content[if_end_start..if_end - 2].trim();

                let endif_start = content[if_end..]
                    .find("{% endif %}")
                    .map(|pos| if_end + pos)?;

                let endif_end = endif_start + 11;

                Some((start, if_end_start, if_end, endif_start, endif_end))
            })
            .collect();

        // Process in reverse order to maintain positions
        if_blocks.into_iter().rev().fold(
            content.to_string(),
            |acc, (start, if_end_start, if_end, endif_start, endif_end)| {
                let condition = acc[if_end_start..if_end - 2].trim().to_string();
                let block_content = acc[if_end..endif_start].to_string();
                let should_show = Self::evaluate_condition(&condition, variables);

                let replacement = if should_show {
                    block_content
                } else {
                    String::new()
                };

                format!("{}{}{}", &acc[..start], replacement, &acc[endif_end..])
            },
        )
    }

    /// Evaluate a conditional expression.
    ///
    /// Supports:
    /// - `VARIABLE` - true if variable exists and is non-empty
    /// - `!VARIABLE` - true if variable doesn't exist or is empty
    fn evaluate_condition(condition: &str, variables: &HashMap<&str, String>) -> bool {
        let condition = condition.trim();

        // Check for negation
        if let Some(rest) = condition.strip_prefix('!') {
            let var_name = rest.trim();
            let value = variables.get(var_name);
            return value.is_none_or(String::is_empty);
        }

        // Normal condition - check if variable exists and is non-empty
        let value = variables.get(condition);
        value.is_some_and(|v| !v.is_empty())
    }
}
