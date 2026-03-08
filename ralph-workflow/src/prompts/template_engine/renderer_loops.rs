// Template loop processing: {% for %} / {% endfor %} blocks.

impl Template {
    /// Process loops in the content based on variable values, returning substitution tracking.
    fn process_loops_with_log(
        content: &str,
        variables: &HashMap<&str, String>,
        literal_segments: &mut Vec<LiteralSegment>,
    ) -> (String, Vec<LoopRenderLog>) {
        let mut result = content.to_string();
        let mut loop_logs = Vec::new();

        // Find all {% for ... %} blocks
        while let Some(start) = result.find("{% for ") {
            // Find the end of the for condition
            let for_end_start = start + 7; // "{% for " is 7 chars
            let for_end = if let Some(pos) = result[for_end_start..].find("%}") {
                for_end_start + pos + 2
            } else {
                // Unclosed for tag - skip it
                result = result[start + 1..].to_string();
                continue;
            };

            // Parse "item in ITEMS"
            let condition = result[for_end_start..for_end - 2].trim();
            let parts: Vec<&str> = condition.split(" in ").collect();
            if parts.len() != 2 {
                // Invalid for syntax - skip it
                result = result[start + 1..].to_string();
                continue;
            }

            let loop_var = parts[0].trim().to_string();
            let list_var = parts[1].trim();

            // Find the matching {% endfor %}
            let endfor_start = if let Some(pos) = result[for_end..].find("{% endfor %}") {
                for_end + pos
            } else {
                // Unclosed for block - skip it
                result = result[start + 1..].to_string();
                continue;
            };

            let endfor_end = endfor_start + 12; // "{% endfor %}" is 12 chars

            // Extract the template inside the for block
            let block_template = result[for_end..endfor_start].to_string();

            // Get the list of values
            let items: Vec<String> = variables.get(list_var).map_or(Vec::new(), |v| {
                if v.is_empty() {
                    Vec::new()
                } else {
                    // Split by comma and trim each item
                    v.split(',').map(|s| s.trim().to_string()).collect()
                }
            });

            // Build the loop output
            let mut loop_output = String::new();
            let mut substituted = Vec::new();
            let mut unsubstituted = Vec::new();
            for item in items {
                // Create a temporary variable map with the loop variable
                let mut loop_vars: HashMap<&str, String> = variables.clone();
                loop_vars.insert(&loop_var, item);

                // Process conditionals first with loop variables
                let processed = Self::process_conditionals(&block_template, &loop_vars);

                // Then substitute variables (collect log for loop substitutions)
                let (processed, loop_substituted, loop_unsubstituted) =
                    Self::substitute_variables(&processed, &loop_vars);
                substituted.extend(loop_substituted);
                for name in loop_unsubstituted {
                    if !unsubstituted.contains(&name) {
                        unsubstituted.push(name);
                    }
                }
                loop_output.push_str(&processed);
            }

            // Replace the entire for block with the loop output
            let token = Self::next_literal_token(&result, &loop_output, literal_segments);
            literal_segments.push(LiteralSegment {
                token: token.clone(),
                content: loop_output,
            });
            result.replace_range(start..endfor_end, &token);

            loop_logs.push(LoopRenderLog {
                token,
                substituted,
                unsubstituted,
            });
        }

        (result, loop_logs)
    }
}
