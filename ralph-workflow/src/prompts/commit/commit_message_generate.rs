// Commit message generation prompt functions.

/// Generate prompt for creating commit message from provided diff.
///
/// This is used by the orchestrator (not agents) to generate commit messages.
/// The diff is provided directly in the prompt, so the LLM doesn't need to
/// run git commands or access files.
///
/// Uses the XML-based template format for output, which is more reliable than JSON
/// because:
/// - No escape sequence issues (actual newlines work fine in XML)
/// - Distinctive tags (`<ralph-commit>`) unlikely to appear in LLM analysis
/// - Clear boundaries for parsing
///
/// # Arguments
///
/// * `diff` - The git diff to generate a commit message for. If empty or
///   whitespace-only, the prompt will indicate no changes were detected.
///
/// # Note
///
/// This function includes a defensive check for empty diffs - if an empty diff
/// is passed, it returns an error prompt. Callers should check for meaningful
/// changes before calling this function to avoid wasting LLM API calls.
/// The `generate_commit_message` function in phases/commit.rs handles empty
/// diffs by returning the hardcoded fallback commit message.
///
/// # Panics
///
/// Panics if the current working directory cannot be determined.
#[cfg(test)]
#[must_use]
pub fn prompt_generate_commit_message_with_diff(diff: &str) -> String {
    use crate::workspace::WorkspaceFs;
    use std::env;

    let workspace = WorkspaceFs::new(env::current_dir().unwrap());
    // Check if diff is empty or whitespace-only
    let diff_content = diff.trim();
    let has_changes = !diff_content.is_empty();

    if !has_changes {
        // Return an error message instead of a placeholder.
        // Callers should check for empty diffs before calling this function.
        // The generate_commit_message function in phases/commit.rs handles this case.
        return "ERROR: Empty diff provided. This indicates a bug in the caller - \
                meaningful changes should be checked before requesting a commit message."
            .to_string();
    }

    let template_content = include_str!("../templates/commit_message_xml.txt");
    let template = Template::new(template_content);
    let partials = get_shared_partials();
    let variables = HashMap::from([
        ("DIFF", diff_content.to_string()),
        (
            "COMMIT_MESSAGE_XML_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xml"),
        ),
        (
            "COMMIT_MESSAGE_XSD_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xsd"),
        ),
    ]);

    template
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_e| {
        // Last resort: simple inline prompt (no fallback template needed)
        format!(
            "Generate a conventional commit message for this diff:\n\n{diff_content}\n\n\
             Output format: <ralph-commit><ralph-subject>type: description</ralph-subject></ralph-commit>"
        )
    })
}

/// Generate prompt for commit message from diff with substitution log.
///
/// This is the new log-based version that returns both content and substitution tracking.
/// Use this version in handlers to enable log-based validation.
pub fn prompt_generate_commit_message_with_diff_with_log(
    context: &TemplateContext,
    diff: &str,
    workspace: &dyn Workspace,
    template_name: &str,
) -> RenderedTemplate {
    // Ensure the commit XSD schema is available on disk for agents to reference.
    let tmp_dir = std::path::Path::new(".agent/tmp");
    let _ = workspace.create_dir_all(tmp_dir);
    let _ = workspace.write(
        &tmp_dir.join("commit_message.xsd"),
        COMMIT_MESSAGE_XSD_SCHEMA,
    );

    // Check if diff is empty or whitespace-only
    let diff_content = diff.trim();
    let has_changes = !diff_content.is_empty();

    if !has_changes {
        let prompt_content = "ERROR: Empty diff provided. This indicates a bug in the caller - \
                meaningful changes should be checked before requesting a commit message."
            .to_string();
        return RenderedTemplate {
            content: prompt_content,
            log: SubstitutionLog {
                template_name: template_name.to_string(),
                substituted: vec![],
                unsubstituted: vec![],
            },
        };
    }

    let template_content = context
        .registry()
        .get_template("commit_message_xml")
        .unwrap_or_else(|_| include_str!("../templates/commit_message_xml.txt").to_string());
    let template = Template::new(&template_content);
    let partials = get_shared_partials();

    // Base variables for commit message prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("DIFF", diff_content.to_string()),
        (
            "COMMIT_MESSAGE_XML_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xml"),
        ),
        (
            "COMMIT_MESSAGE_XSD_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xsd"),
        ),
    ]);

    // Compute capability variables using Commit drain defaults
    let capability_vars = crate::prompts::template_variables::capability_template_variables(
        &crate::agents::session::CapabilitySet::defaults_for_drain(
            crate::agents::session::SessionDrain::Commit,
        ),
        &crate::agents::session::PolicyFlagSet::defaults_for_drain(
            crate::agents::session::SessionDrain::Commit,
        ),
    );

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    match template.render_with_log(template_name, &variables_ref, &partials) {
        Ok(rendered) => rendered,
        Err(_e) => {
            // Last resort: simple inline prompt with manual log
            let prompt_content = format!(
                "Generate a conventional commit message for this diff:\n\n{diff_content}\n\n\
                 Output format: <ralph-commit><ralph-subject>type: description</ralph-subject></ralph-commit>"
            );
            RenderedTemplate {
                content: prompt_content,
                log: SubstitutionLog {
                    template_name: template_name.to_string(),
                    substituted: vec![SubstitutionEntry {
                        name: "DIFF".to_string(),
                        source: SubstitutionSource::Value,
                    }],
                    unsubstituted: vec![],
                },
            }
        }
    }
}

/// Generate prompt for creating commit message from provided diff using template registry.
///
/// This version uses the template registry which supports user template overrides.
/// It's the recommended way to generate prompts going forward.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `diff` - The git diff to generate a commit message for
/// * `workspace` - Workspace for resolving absolute paths (accepts any Workspace implementation)
pub fn prompt_generate_commit_message_with_diff_with_context(
    context: &TemplateContext,
    diff: &str,
    workspace: &dyn Workspace,
) -> String {
    // Ensure the commit XSD schema is available on disk for agents to reference.
    // In production this is also written during app bootstrap, but tests and some
    // entrypoints may call prompt generation directly.
    let tmp_dir = std::path::Path::new(".agent/tmp");
    let _ = workspace.create_dir_all(tmp_dir);
    let _ = workspace.write(
        &tmp_dir.join("commit_message.xsd"),
        COMMIT_MESSAGE_XSD_SCHEMA,
    );

    // Check if diff is empty or whitespace-only
    let diff_content = diff.trim();
    let has_changes = !diff_content.is_empty();

    if !has_changes {
        return "ERROR: Empty diff provided. This indicates a bug in the caller - \
                meaningful changes should be checked before requesting a commit message."
            .to_string();
    }

    let template_content = context
        .registry()
        .get_template("commit_message_xml")
        .unwrap_or_else(|_| include_str!("../templates/commit_message_xml.txt").to_string());
    let template = Template::new(&template_content);
    let partials = get_shared_partials();

    // Base variables for commit message prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("DIFF", diff_content.to_string()),
        (
            "COMMIT_MESSAGE_XML_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xml"),
        ),
        (
            "COMMIT_MESSAGE_XSD_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xsd"),
        ),
    ]);

    // Compute capability variables using Commit drain defaults
    // since the session is created after prompt generation in invoke_agent.
    let capability_vars = crate::prompts::template_variables::capability_template_variables(
        &crate::agents::session::CapabilitySet::defaults_for_drain(
            crate::agents::session::SessionDrain::Commit,
        ),
        &crate::agents::session::PolicyFlagSet::defaults_for_drain(
            crate::agents::session::SessionDrain::Commit,
        ),
    );

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_e| {
        // Last resort: simple inline prompt (no fallback template needed)
        format!(
            "Generate a conventional commit message for this diff:\n\n{diff_content}\n\n\
             Output format: <ralph-commit><ralph-subject>type: description</ralph-subject></ralph-commit>"
        )
    })
}
