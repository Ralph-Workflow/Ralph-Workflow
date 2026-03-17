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

            let Some(loop_var_part) = parts.first() else {
                result = result[start + 1..].to_string();
                continue;
            };
            let loop_var = loop_var_part.trim().to_string();
            let Some(list_var_part) = parts.get(1) else {
                result = result[start + 1..].to_string();
                continue;
            };
            let list_var = list_var_part.trim();

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

            // Build the loop output using iterator pipeline
            let loop_results: Vec<(String, Vec<crate::prompts::SubstitutionEntry>, Vec<String>)> =
                items
                    .iter()
                    .map(|item| {
                        // Create a temporary variable map with the loop variable
                        let mut loop_vars: HashMap<&str, String> = variables.clone();
                        loop_vars.insert(&loop_var, item.clone());

                        // Process conditionals first with loop variables
                        let processed = Self::process_conditionals(&block_template, &loop_vars);

                        // Then substitute variables (collect log for loop substitutions)
                        Self::substitute_variables(&processed, &loop_vars)
                    })
                    .collect();

            let loop_output: String = loop_results
                .iter()
                .map(|(processed, _, _)| processed.clone())
                .collect::<Vec<_>>()
                .join("");

            let substituted: Vec<crate::prompts::SubstitutionEntry> = loop_results
                .iter()
                .flat_map(|(_, sub, _)| sub.clone())
                .collect();

            let unsubstituted: Vec<String> = loop_results
                .iter()
                .flat_map(|(_, _, unsub)| unsub.clone())
                .collect::<std::collections::HashSet<_>>()
                .into_iter()
                .collect();

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
