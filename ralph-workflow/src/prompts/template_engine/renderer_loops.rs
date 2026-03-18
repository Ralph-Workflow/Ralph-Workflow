// Template loop processing: {% for %} / {% endfor %} blocks.

impl Template {
    /// Process loops in the content based on variable values, returning substitution tracking.
    fn process_loops_with_log(
        content: &str,
        variables: &HashMap<&str, String>,
        literal_segments: &mut Vec<LiteralSegment>,
    ) -> (String, Vec<LoopRenderLog>) {
        // Find all {% for ... %} blocks with their positions
        let for_blocks: Vec<(usize, usize, usize, String, String)> = content
            .match_indices("{% for ")
            .filter_map(|(start, _)| {
                let for_end_start = start + 7;
                let for_end = content[for_end_start..]
                    .find("%}")
                    .map(|pos| for_end_start + pos + 2)?;

                let condition = content[for_end_start..for_end - 2].trim();
                let parts: Vec<&str> = condition.split(" in ").collect();

                if parts.len() != 2 {
                    return None;
                }

                let loop_var = parts[0].trim().to_string();
                let list_var = parts[1].trim().to_string();

                let endfor_start = content[for_end..]
                    .find("{% endfor %}")
                    .map(|pos| for_end + pos)?;

                let endfor_end = endfor_start + 12;

                Some((start, for_end, endfor_end, loop_var, list_var))
            })
            .collect();

        // Process in reverse order to maintain positions
        for_blocks.into_iter().rev().fold(
            (content.to_string(), Vec::new()),
            |(acc_result, mut acc_logs), (start, for_end, endfor_end, loop_var, list_var)| {
                let block_template = acc_result[for_end..endfor_end].to_string();

                // Get the list of values
                let items: Vec<String> = variables.get(list_var.as_str()).map_or(Vec::new(), |v| {
                    if v.is_empty() {
                        Vec::new()
                    } else {
                        v.split(',').map(|s| s.trim().to_string()).collect()
                    }
                });

                // Build the loop output using iterator pipeline
                let loop_results: Vec<(
                    String,
                    Vec<crate::prompts::SubstitutionEntry>,
                    Vec<String>,
                )> = items
                    .iter()
                    .map(|item| {
                        let loop_vars: HashMap<&str, String> = variables
                            .iter()
                            .map(|(k, v)| (*k, v.clone()))
                            .chain(std::iter::once((loop_var.as_str(), item.clone())))
                            .collect();

                        let processed = Self::process_conditionals(&block_template, &loop_vars);
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
                let token = Self::next_literal_token(&acc_result, &loop_output, literal_segments);
                literal_segments.push(LiteralSegment {
                    token: token.clone(),
                    content: loop_output,
                });

                let new_result = format!(
                    "{}{}{}",
                    &acc_result[..start],
                    token,
                    &acc_result[endfor_end..]
                );

                acc_logs.push(LoopRenderLog {
                    token,
                    substituted,
                    unsubstituted,
                });

                (new_result, acc_logs)
            },
        )
    }
}
