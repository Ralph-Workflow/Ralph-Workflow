// Review phase XML validation.
//
// This module handles validation of reviewer output XML against the schema, parsing issues,
// and determining whether the review detected any problems.
//
// ## Responsibilities
//
// - Checking if `.agent/tmp/issues.xml` exists after agent runs
// - Parsing and validating XML against schema
// - Extracting `<issue>` and `<no_issues_found>` elements
// - Detecting issues vs clean outcomes
// - Emitting validation events (success or failure with AI-formatted error messages)
// - Creating UI events with XML output
//
// ## Validation Flow
//
// 1. Agent runs and writes `.agent/tmp/issues.xml`
// 2. `extract_review_issues_xml` - Check file exists
// 3. `validate_review_issues_xml` - Parse and validate XML structure
// 4. If validation fails, XSD retry is triggered
//
// ## See Also
//
// - `prompt_generation.rs` - XSD retry prompt building
// - `output_rendering.rs` - Converting validated XML to markdown

impl MainEffectHandler {
    pub(in crate::reducer::handler) fn extract_review_issues_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use std::path::Path;

        // Only the canonical path is considered input. Archived `.processed` files
        // are debug artifacts and must not be used as fallback inputs.
        let issues_xml = Path::new(xml_paths::ISSUES_XML);
        let content = ctx.workspace.read(issues_xml);

        match content {
            Ok(_) => EffectResult::event(PipelineEvent::review_issues_xml_extracted(pass)),
            Err(err) => {
                let detail = if err.kind() == std::io::ErrorKind::NotFound {
                    None
                } else {
                    Some(format!(
                        "{:?}: {}",
                        WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        err
                    ))
                };

                EffectResult::event(PipelineEvent::review_issues_xml_missing(
                    pass,
                    self.state.continuation.invalid_output_attempts,
                    detail,
                ))
            }
        }
    }

    pub(in crate::reducer::handler) fn validate_review_issues_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use crate::files::llm_output_extraction::validate_issues_xml;
        use std::path::Path;

        let issues_xml = ctx.workspace.read(Path::new(xml_paths::ISSUES_XML));
        let issues_xml = match issues_xml {
            Ok(s) => s,
            Err(err) => {
                let detail = if err.kind() == std::io::ErrorKind::NotFound {
                    None
                } else {
                    Some(format!(
                        "{:?}: {}",
                        WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        err
                    ))
                };

                return EffectResult::event(PipelineEvent::review_output_validation_failed(
                    pass,
                    self.state.continuation.invalid_output_attempts,
                    detail,
                ));
            }
        };

        match validate_issues_xml(&issues_xml) {
            Ok(elements) => {
                let issues_found = !elements.issues.is_empty();
                let clean_no_issues =
                    elements.no_issues_found.is_some() && elements.issues.is_empty();
                EffectResult::with_ui(
                    PipelineEvent::review_issues_xml_validated(
                        pass,
                        issues_found,
                        clean_no_issues,
                        elements.issue_texts(),
                        elements.no_issues_found,
                    ),
                    vec![UIEvent::XmlOutput {
                        xml_type: XmlOutputType::ReviewIssues,
                        content: issues_xml,
                        context: Some(XmlOutputContext {
                            iteration: None,
                            pass: Some(pass),
                            snippets: Vec::new(),
                        }),
                    }],
                )
            }
            Err(err) => EffectResult::event(PipelineEvent::review_output_validation_failed(
                pass,
                self.state.continuation.invalid_output_attempts,
                Some(err.format_for_ai_retry()),
            )),
        }
    }
}
