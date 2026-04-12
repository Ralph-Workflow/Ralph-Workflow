// XSD retry and issue snippet helpers for the review boundary.
// Split from run_review.rs to keep file size < 1000 lines.
// This file is included (not mod'd) from run_review.rs.

fn write_xsd_last_output(ctx: &PhaseContext<'_>, path: &Path, content: &str) -> Result<()> {
    ctx.workspace.write_atomic(path, content).map_err(|err| {
        ErrorEvent::WorkspaceWriteFailed {
            path: path.display().to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()
    })
}

fn build_xsd_last_output_input(
    candidate: &crate::phases::review::boundary_domain::XsdRetryMaterializationSignature,
    last_output_bytes: u64,
    inline_budget_bytes: u64,
    last_output_path: &Path,
) -> MaterializedPromptInput {
    MaterializedPromptInput {
        kind: PromptInputKind::LastOutput,
        content_id_sha256: candidate.content_id_sha256.clone(),
        consumer_signature_sha256: candidate.consumer_signature_sha256.clone(),
        original_bytes: last_output_bytes,
        final_bytes: last_output_bytes,
        model_budget_bytes: None,
        inline_budget_bytes: Some(inline_budget_bytes),
        representation: PromptInputRepresentation::FileReference {
            path: last_output_path.to_path_buf(),
        },
        reason: PromptMaterializationReason::PolicyForcedReference,
    }
}

fn build_xsd_materialized_events(
    input: MaterializedPromptInput,
    pass: u32,
    content_id_sha256: &str,
    last_output_bytes: u64,
    inline_budget_bytes: u64,
) -> Vec<PipelineEvent> {
    let base_event = PipelineEvent::xsd_retry_last_output_materialized(
        crate::reducer::event::PipelinePhase::Review,
        pass,
        input,
    );
    if last_output_bytes > inline_budget_bytes {
        vec![
            base_event,
            PipelineEvent::prompt_input_oversize_detected(
                crate::reducer::event::PipelinePhase::Review,
                PromptInputKind::LastOutput,
                content_id_sha256.to_string(),
                last_output_bytes,
                inline_budget_bytes,
                "xsd-retry-context".to_string(),
            ),
        ]
    } else {
        vec![base_event]
    }
}

fn extract_issue_snippets(
    issues: &[String],
    workspace: &dyn crate::workspace::Workspace,
) -> Vec<XmlCodeSnippet> {
    let requests = crate::phases::review::snippet_domain::collect_issue_snippet_requests(
        issues,
        workspace.root(),
    );

    requests
        .into_iter()
        .filter_map(|request| {
            let content = workspace.read(Path::new(&request.file)).ok()?;
            let snippet = crate::phases::review::snippet_domain::extract_snippet_lines(
                &content,
                request.start,
                request.end,
            )?;
            Some(XmlCodeSnippet {
                file: request.file,
                line_start: request.start,
                line_end: request.end,
                content: snippet,
            })
        })
        .collect()
}
