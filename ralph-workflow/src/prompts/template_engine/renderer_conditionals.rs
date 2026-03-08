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
        let mut result = content.to_string();

        // Find all {% if ... %} blocks
        while let Some(start) = result.find("{% if ") {
            // Find the end of the if condition
            let if_end_start = start + 6; // "{% if " is 6 chars
            let if_end = if let Some(pos) = result[if_end_start..].find("%}") {
                if_end_start + pos + 2
            } else {
                // Unclosed if tag - skip it
                result = result[start + 1..].to_string();
                continue;
            };

            // Extract the condition
            let condition = result[if_end_start..if_end - 2].trim().to_string();

            // Find the matching {% endif %}
            let endif_start = if let Some(pos) = result[if_end..].find("{% endif %}") {
                if_end + pos
            } else {
                // Unclosed if block - skip it
                result = result[start + 1..].to_string();
                continue;
            };

            let endif_end = endif_start + 11; // "{% endif %}" is 11 chars

            // Extract the content inside the if block
            let block_content = result[if_end..endif_start].to_string();

            // Evaluate the condition
            let should_show = Self::evaluate_condition(&condition, variables);

            // Replace the entire if block with the content or empty string
            let replacement = if should_show {
                block_content
            } else {
                String::new()
            };
            result.replace_range(start..endif_end, &replacement);
        }

        result
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
