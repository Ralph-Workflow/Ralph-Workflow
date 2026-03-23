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

pub(crate) enum ParsedCommitXmlOutcome {
    Skipped(String),
    Invalid(String),
    Valid {
        message: String,
        files: Vec<String>,
        excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
    },
}

pub(crate) fn parse_commit_xml_document(xml_content: &str) -> ParsedCommitXmlOutcome {
    let (message, skip_reason, files, excluded_files, detail) =
        try_extract_xml_commit_document_with_trace(xml_content);

    if let Some(reason) = skip_reason {
        return ParsedCommitXmlOutcome::Skipped(reason);
    }

    message.map_or(ParsedCommitXmlOutcome::Invalid(detail), |message| {
        ParsedCommitXmlOutcome::Valid {
            message,
            files,
            excluded_files,
        }
    })
}

pub(crate) fn commit_outcome_event_from_validated(
    message: Option<String>,
    reason: Option<String>,
    attempt: u32,
) -> crate::reducer::event::PipelineEvent {
    match (message, reason) {
        (Some(message), _) => {
            crate::reducer::event::PipelineEvent::commit_message_generated(message, attempt)
        }
        (None, Some(reason)) => {
            crate::reducer::event::PipelineEvent::commit_message_validation_failed(reason, attempt)
        }
        (None, None) => crate::reducer::event::PipelineEvent::commit_generation_failed(
            "Commit validation outcome missing message and reason".to_string(),
        ),
    }
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

    match parse_commit_xml_document(&xml) {
        ParsedCommitXmlOutcome::Skipped(reason) => CommitExtractionOutcome::Skipped(reason),
        ParsedCommitXmlOutcome::Invalid(detail) => CommitExtractionOutcome::InvalidXml(detail),
        ParsedCommitXmlOutcome::Valid {
            message,
            files,
            excluded_files,
        } => CommitExtractionOutcome::Valid {
            extracted: CommitExtractionResult::new(message),
            files,
            excluded_files,
        },
    }
}
