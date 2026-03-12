enum CommitExtractionOutcome {
    MissingFile(String),
    InvalidXml(String),
    Valid {
        extracted: CommitExtractionResult,
        files: Vec<String>,
        excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
    },
    Skipped(String),
}

fn extract_commit_message_from_file_with_workspace(
    workspace: &dyn Workspace,
) -> CommitExtractionOutcome {
    let Ok(xml) = workspace.read(Path::new(xml_paths::COMMIT_MESSAGE_XML)) else {
        return CommitExtractionOutcome::MissingFile(
            "XML output missing or invalid; agent must write .agent/tmp/commit_message.xml"
                .to_string(),
        );
    };

    let (message, skip_reason, files, excluded_files, detail) =
        try_extract_xml_commit_document_with_trace(&xml);

    // Check for skip first
    if let Some(reason) = skip_reason {
        return CommitExtractionOutcome::Skipped(reason);
    }

    message.map_or(CommitExtractionOutcome::InvalidXml(detail), |msg| {
        CommitExtractionOutcome::Valid {
            extracted: CommitExtractionResult::new(msg),
            files,
            excluded_files,
        }
    })
}
