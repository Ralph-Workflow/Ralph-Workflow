// Template rendering logic: variable substitution, conditional expansion, loops.
// Split into sub-files by concern:
//   - renderer_conditionals.rs: {% if %} / {% endif %} processing
//   - renderer_loops.rs: {% for %} / {% endfor %} processing
//   - renderer_substitution.rs: variable substitution and literal segment helpers

struct LiteralSegment {
    token: String,
    content: String,
}

struct LoopRenderLog {
    token: String,
    substituted: Vec<crate::prompts::SubstitutionEntry>,
    unsubstituted: Vec<String>,
}

include!("renderer_conditionals.rs");
include!("renderer_loops.rs");
include!("renderer_substitution.rs");

impl Template {
    /// Render the template with the provided variables.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn render(&self, variables: &HashMap<&str, String>) -> Result<String, TemplateError> {
        let mut literal_segments = Vec::new();
        // Process loops first (they may generate new variable references)
        let (result, loop_logs) =
            Self::process_loops_with_log(&self.content, variables, &mut literal_segments);

        // Process conditionals
        let result = Self::process_conditionals(&result, variables);

        // Substitute variables (with default values and substitution tracking)
        let (result_after_sub, _substituted, unsubstituted) =
            Self::substitute_variables_allow_empty(&result, variables);

        // Check for missing variables using iterator pipeline
        let missing: Vec<String> = loop_logs
            .iter()
            .filter(|loop_log| result.contains(&loop_log.token))
            .flat_map(|loop_log| loop_log.unsubstituted.clone())
            .chain(unsubstituted)
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        if let Some(first_missing) = missing.first() {
            return Err(TemplateError::MissingVariable(first_missing.clone()));
        }

        Ok(Self::restore_literal_segments(
            result_after_sub,
            &literal_segments,
        ))
    }

    /// Render the template with variables and partials support.
    ///
    /// Partials are processed recursively, with the same variables passed to each partial.
    /// Circular references are detected and reported with a clear error.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn render_with_partials(
        &self,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
    ) -> Result<String, TemplateError> {
        self.render_with_partials_recursive(variables, partials, &mut Vec::new())
    }

    /// Render the template with variables and partials, returning substitution log.
    ///
    /// This is the primary method for reducer-integrated rendering. It returns both
    /// the rendered content and a detailed log of all substitutions, enabling
    /// validation based on what was actually substituted rather than regex scanning.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn render_with_log(
        &self,
        template_name: &str,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
    ) -> Result<crate::prompts::RenderedTemplate, TemplateError> {
        self.render_with_log_recursive(template_name, variables, partials, &mut Vec::new())
    }

    /// Internal recursive rendering with circular reference detection.
    /// `visited` is a Vec that tracks the order of partials visited for proper error reporting.
    fn render_with_partials_recursive(
        &self,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
        visited: &mut Vec<String>,
    ) -> Result<String, TemplateError> {
        // First, extract and resolve all partials in this template
        let mut literal_segments = Vec::new();
        let mut result = self.content.clone();

        // Find all {{> partial}} references
        let partial_refs = Self::extract_partials(&result);

        // Process partials in reverse order to maintain correct positions when replacing
        for (full_match, partial_name) in partial_refs.into_iter().rev() {
            // Check for circular reference
            if visited.contains(&partial_name) {
                let mut chain = visited.clone();
                chain.push(partial_name);
                return Err(TemplateError::CircularReference(chain));
            }

            // Look up the partial content
            let partial_content = partials
                .get(&partial_name)
                .ok_or_else(|| TemplateError::PartialNotFound(partial_name.clone()))?;

            // Create a template from the partial and render it recursively
            let partial_template = Self::new(partial_content);
            visited.push(partial_name.clone());
            let rendered_partial =
                partial_template.render_with_partials_recursive(variables, partials, visited)?;
            visited.pop();

            // Replace the partial reference with rendered content
            let token = Self::next_literal_token(&result, &rendered_partial, &literal_segments);
            literal_segments.push(LiteralSegment {
                token: token.clone(),
                content: rendered_partial,
            });
            result = result.replace(&full_match, &token);
        }

        // Process loops (they may generate new variable references)
        let (loop_processed, loop_logs) =
            Self::process_loops_with_log(&result, variables, &mut literal_segments);
        let result = loop_processed;

        // Process conditionals
        let result = Self::process_conditionals(&result, variables);

        // Now substitute variables in the result (using the new method that handles defaults)
        let (result_after_sub, _substituted, unsubstituted) =
            Self::substitute_variables_allow_empty(&result, variables);

        // Check for missing variables using iterator pipeline
        let missing: Vec<String> = loop_logs
            .iter()
            .filter(|loop_log| result.contains(&loop_log.token))
            .flat_map(|loop_log| loop_log.unsubstituted.clone())
            .chain(unsubstituted)
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        if let Some(first_missing) = missing.first() {
            return Err(TemplateError::MissingVariable(first_missing.clone()));
        }

        Ok(Self::restore_literal_segments(
            result_after_sub,
            &literal_segments,
        ))
    }

    /// Internal recursive rendering with log tracking.
    fn render_with_log_recursive(
        &self,
        template_name: &str,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
        visited: &mut Vec<String>,
    ) -> Result<crate::prompts::RenderedTemplate, TemplateError> {
        use crate::prompts::{RenderedTemplate, SubstitutionLog};

        let mut log = SubstitutionLog {
            template_name: template_name.to_string(),
            substituted: Vec::new(),
            unsubstituted: Vec::new(),
        };
        let mut literal_segments = Vec::new();

        // Process partials (existing logic)
        let mut result = self.content.clone();
        let partial_refs = Self::extract_partials(&result);

        for (full_match, partial_name) in partial_refs.into_iter().rev() {
            if visited.contains(&partial_name) {
                let mut chain = visited.clone();
                chain.push(partial_name);
                return Err(TemplateError::CircularReference(chain));
            }

            let partial_content = partials
                .get(&partial_name)
                .ok_or_else(|| TemplateError::PartialNotFound(partial_name.clone()))?;

            let partial_template = Self::new(partial_content);
            visited.push(partial_name.clone());
            let rendered_partial = partial_template.render_with_log_recursive(
                template_name,
                variables,
                partials,
                visited,
            )?;
            visited.pop();

            let token =
                Self::next_literal_token(&result, &rendered_partial.content, &literal_segments);
            literal_segments.push(LiteralSegment {
                token: token.clone(),
                content: rendered_partial.content,
            });
            result = result.replace(&full_match, &token);
            log.substituted.extend(rendered_partial.log.substituted);
            let new_unsub: Vec<String> = rendered_partial
                .log
                .unsubstituted
                .into_iter()
                .filter(|name| !log.unsubstituted.contains(name))
                .collect();
            log.unsubstituted.extend(new_unsub);
        }

        // Process loops
        let (loop_processed, loop_logs) =
            Self::process_loops_with_log(&result, variables, &mut literal_segments);
        let result = loop_processed;

        // Process conditionals
        let result = Self::process_conditionals(&result, variables);

        for loop_log in loop_logs {
            if result.contains(&loop_log.token) {
                log.substituted.extend(loop_log.substituted);
                let new_unsub: Vec<String> = loop_log
                    .unsubstituted
                    .into_iter()
                    .filter(|name| !log.unsubstituted.contains(name))
                    .collect();
                log.unsubstituted.extend(new_unsub);
            }
        }

        // Substitute variables WITH log tracking
        let (result_after_sub, substituted, unsubstituted) =
            Self::substitute_variables(&result, variables);

        log.substituted.extend(substituted);
        let new_unsub: Vec<String> = unsubstituted
            .into_iter()
            .filter(|name| !log.unsubstituted.contains(name))
            .collect();
        log.unsubstituted.extend(new_unsub);

        Ok(RenderedTemplate {
            content: Self::restore_literal_segments(result_after_sub, &literal_segments),
            log,
        })
    }
}
