// Commit message XSD validation retry prompt functions.

/// Generate XSD validation retry prompt for commit message XML with substitution log.
///
/// This is the new log-based version that returns both content and substitution tracking.
/// Use this version in handlers to enable log-based validation.
pub fn prompt_commit_xsd_retry_with_log(
    context: &TemplateContext,
    xsd_error: &str,
    workspace: &dyn Workspace,
    template_name: &str,
) -> RenderedTemplate {
    use std::path::Path;

    // Ensure the schema file is present.
    let tmp_dir = Path::new(".agent/tmp");
    let _ = workspace.create_dir_all(tmp_dir);
    let _ = workspace.write(
        &tmp_dir.join("commit_message.xsd"),
        COMMIT_MESSAGE_XSD_SCHEMA,
    );

    // Check that required files exist
    let schema_path = Path::new(".agent/tmp/commit_message.xsd");
    let canonical_output_path = Path::new(".agent/tmp/commit_message.xml");
    let processed_output_path = Path::new(".agent/tmp/commit_message.xml.processed");

    let schema_exists = workspace.exists(schema_path);
    let canonical_output_exists = workspace.exists(canonical_output_path);
    let processed_output_exists = workspace.exists(processed_output_path);

    // If canonical file was archived, try using the .processed file as fallback
    let (last_output_path, last_output_exists, used_processed) =
        if !canonical_output_exists && processed_output_exists {
            (processed_output_path, true, true)
        } else {
            (canonical_output_path, canonical_output_exists, false)
        };

    // Build diagnostic prefix for missing files
    let mut diagnostic_prefix = String::new();
    if !schema_exists || !last_output_exists {
        diagnostic_prefix.push_str("WARNING: Required XSD retry files are missing:\n");
        if !schema_exists {
            writeln!(
                diagnostic_prefix,
                "  - Schema file: {} (workspace.root() = {})",
                workspace.absolute_str(".agent/tmp/commit_message.xsd"),
                workspace.root().display()
            )
            .unwrap();
        }
        if !last_output_exists {
            if used_processed {
                writeln!(
                    diagnostic_prefix,
                    "  - Last output: Neither canonical nor processed file exists:\n\
                     \t  Tried: {}\n\
                     \t  Tried: {}\n\
                     \t  (workspace.root() = {})",
                    workspace.absolute_str(".agent/tmp/commit_message.xml"),
                    workspace.absolute_str(".agent/tmp/commit_message.xml.processed"),
                    workspace.root().display()
                )
                .unwrap();
            } else {
                let processed_note = if processed_output_exists {
                    " (note: .processed file exists but canonical file is missing)"
                } else {
                    ""
                };
                writeln!(
                    diagnostic_prefix,
                    "  - Last output: {}{}\n\
                     \t  (workspace.root() = {})",
                    workspace.absolute_str(
                        canonical_output_path
                            .to_str()
                            .unwrap_or(".agent/tmp/commit_message.xml")
                    ),
                    processed_note,
                    workspace.root().display()
                )
                .unwrap();
            }
        }
        diagnostic_prefix
            .push_str("This likely indicates CWD != workspace.root() path mismatch.\n\n");
    }

    // If both files are missing, return fallback with manual log
    if !schema_exists && !last_output_exists {
        let prompt_content = format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - GENERATE COMMIT MESSAGE\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please generate a conventional commit message for the current changes.\n\n\
             Output format: <ralph-commit><ralph-subject>type: description</ralph-subject></ralph-commit>\n"
        );
        return RenderedTemplate {
            content: prompt_content,
            log: SubstitutionLog {
                template_name: template_name.to_string(),
                substituted: vec![SubstitutionEntry {
                    name: "XSD_ERROR".to_string(),
                    source: SubstitutionSource::Value,
                }],
                unsubstituted: vec![],
            },
        };
    }

    // Proceed with normal XSD retry prompt generation using render_with_log
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("commit_xsd_retry")
        .unwrap_or_else(|_| include_str!("../templates/commit_xsd_retry.txt").to_string());
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "COMMIT_MESSAGE_XML_PATH",
            workspace.absolute_str(
                last_output_path
                    .to_str()
                    .unwrap_or(".agent/tmp/commit_message.xml"),
            ),
        ),
        (
            "COMMIT_MESSAGE_XSD_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xsd"),
        ),
    ]);

    let template = Template::new(&template_content);
    if let Ok(mut rendered) = template.render_with_log(template_name, &variables, &partials) {
        // Prepend diagnostic prefix if files were missing but we continued anyway
        if !diagnostic_prefix.is_empty() {
            rendered.content = format!("{}\n{}", diagnostic_prefix, rendered.content);
        }
        rendered
    } else {
        // Fallback with manual log
        let prompt_content = format!(
            "XSD VALIDATION FAILED - FIX XML ONLY\n\nError: {xsd_error}\n\n\
             Read .agent/tmp/commit_message.xsd for the schema and .agent/tmp/commit_message.xml for your previous output.\n\
             Rewrite .agent/tmp/commit_message.xml with valid XML.\n"
        );
        RenderedTemplate {
            content: prompt_content,
            log: SubstitutionLog {
                template_name: template_name.to_string(),
                substituted: vec![SubstitutionEntry {
                    name: "XSD_ERROR".to_string(),
                    source: SubstitutionSource::Value,
                }],
                unsubstituted: vec![],
            },
        }
    }
}

/// Generate XSD validation retry prompt for commit message XML.
///
/// This prompt is used when a commit message XML output fails XSD validation.
///
/// The agent should read the XSD schema and the previous output from
/// `.agent/tmp/commit_message.xsd` and `.agent/tmp/commit_message.xml`, then rewrite the XML
/// to conform to the schema.
///
/// Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
/// If required files are missing, a deterministic fallback prompt is produced that includes
/// diagnostic information but still provides valid instructions to the agent.
pub fn prompt_commit_xsd_retry_with_context(
    context: &TemplateContext,
    xsd_error: &str,
    workspace: &dyn Workspace,
) -> String {
    use std::path::Path;

    // Ensure the schema file is present.
    // Note: Silent failure (let _) is acceptable here because if the schema file
    // write fails, the subsequent workspace.exists(schema_path) check will return
    // false and generate a fallback prompt with diagnostic information.
    // This approach avoids unnecessary error handling while still providing actionable feedback.
    let tmp_dir = Path::new(".agent/tmp");
    let _ = workspace.create_dir_all(tmp_dir);
    let _ = workspace.write(
        &tmp_dir.join("commit_message.xsd"),
        COMMIT_MESSAGE_XSD_SCHEMA,
    );

    // Check that required files exist
    let schema_path = Path::new(".agent/tmp/commit_message.xsd");
    let canonical_output_path = Path::new(".agent/tmp/commit_message.xml");
    let processed_output_path = Path::new(".agent/tmp/commit_message.xml.processed");

    let schema_exists = workspace.exists(schema_path);
    let canonical_output_exists = workspace.exists(canonical_output_path);
    let processed_output_exists = workspace.exists(processed_output_path);

    // If canonical file was archived, try using the .processed file as fallback
    let (last_output_path, last_output_exists, used_processed) =
        if !canonical_output_exists && processed_output_exists {
            (processed_output_path, true, true)
        } else {
            (canonical_output_path, canonical_output_exists, false)
        };

    // Build diagnostic prefix for missing files (per acceptance criteria #3)
    let mut diagnostic_prefix = String::new();
    if !schema_exists || !last_output_exists {
        diagnostic_prefix.push_str("WARNING: Required XSD retry files are missing:\n");
        if !schema_exists {
            writeln!(
                diagnostic_prefix,
                "  - Schema file: {} (workspace.root() = {})",
                workspace.absolute_str(".agent/tmp/commit_message.xsd"),
                workspace.root().display()
            )
            .unwrap();
        }
        if !last_output_exists {
            // Show both attempted paths for clarity
            if used_processed {
                // We tried processed as fallback and it's also missing
                writeln!(
                    diagnostic_prefix,
                    "  - Last output: Neither canonical nor processed file exists:\n\
                     \t  Tried: {}\n\
                     \t  Tried: {}\n\
                     \t  (workspace.root() = {})",
                    workspace.absolute_str(".agent/tmp/commit_message.xml"),
                    workspace.absolute_str(".agent/tmp/commit_message.xml.processed"),
                    workspace.root().display()
                )
                .unwrap();
            } else {
                // Canonical path doesn't exist
                let processed_note = if processed_output_exists {
                    " (note: .processed file exists but canonical file is missing)"
                } else {
                    ""
                };
                writeln!(
                    diagnostic_prefix,
                    "  - Last output: {}{}\n\
                     \t  (workspace.root() = {})",
                    workspace.absolute_str(
                        canonical_output_path
                            .to_str()
                            .unwrap_or(".agent/tmp/commit_message.xml")
                    ),
                    processed_note,
                    workspace.root().display()
                )
                .unwrap();
            }
        }
        diagnostic_prefix
            .push_str("This likely indicates CWD != workspace.root() path mismatch.\n\n");
    }

    // If both files are missing, return fallback prompt with diagnostics (per AC #5)
    if !schema_exists && !last_output_exists {
        return format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - GENERATE COMMIT MESSAGE\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please generate a conventional commit message for the current changes.\n\n\
             Output format: <ralph-commit><ralph-subject>type: description</ralph-subject></ralph-commit>\n"
        );
    }

    // Proceed with normal XSD retry prompt generation if at least schema exists
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("commit_xsd_retry")
        .unwrap_or_else(|_| include_str!("../templates/commit_xsd_retry.txt").to_string());
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "COMMIT_MESSAGE_XML_PATH",
            workspace.absolute_str(
                last_output_path
                    .to_str()
                    .unwrap_or(".agent/tmp/commit_message.xml"),
            ),
        ),
        (
            "COMMIT_MESSAGE_XSD_PATH",
            workspace.absolute_str(".agent/tmp/commit_message.xsd"),
        ),
    ]);

    let template = Template::new(&template_content);
    let rendered_prompt = template
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            format!(
                "XSD VALIDATION FAILED - FIX XML ONLY\n\nError: {xsd_error}\n\n\
                 Read .agent/tmp/commit_message.xsd for the schema and .agent/tmp/commit_message.xml for your previous output.\n\
                 Rewrite .agent/tmp/commit_message.xml with valid XML.\n"
            )
        });

    // Prepend diagnostic prefix if files were missing but we continued anyway
    if diagnostic_prefix.is_empty() {
        rendered_prompt
    } else {
        format!("{diagnostic_prefix}\n{rendered_prompt}")
    }
}
