use super::common::TestFixture;
use crate::files::artifact_paths;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::PipelineEvent;
use crate::reducer::state::{PipelineState, ReviewValidatedOutcome};
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

#[test]
fn test_validate_fix_result_xml_emits_ui_output() {
    // Fix validation is JSON-only; supply a JSON artifact and verify the
    // validated event and XmlOutput UI event are both emitted.
    let workspace = MemoryWorkspace::new_test();
    workspace
        .write_artifact_json(&crate::workspace::ArtifactEnvelope::new(
            "fix_result",
            serde_json::json!({
                "status": "all_issues_addressed"
            }),
            "2026-03-26T00:00:00Z",
        ))
        .expect("should write fix_result json artifact");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler.validate_fix_result_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::FixResultXmlValidated {
            pass: 0,
            ..
        })
    ));

    assert!(result.ui_events.iter().any(|event| matches!(
        event,
        UIEvent::XmlOutput {
            xml_type: XmlOutputType::FixResult,
            context: Some(XmlOutputContext {
                pass: Some(0),
                ..
            }),
            ..
        }
    )));
}

#[test]
fn test_write_issues_markdown_renders_from_validated_issues() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.review_validated_outcome = Some(ReviewValidatedOutcome {
        pass: 0,
        issues_found: false,
        clean_no_issues: true,
        issues: Vec::new().into_boxed_slice(),
        no_issues_found: Some("No issues found.".to_string()),
    });

    let result = handler
        .write_issues_markdown(&ctx, 0)
        .expect("write_issues_markdown should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::IssuesMarkdownWritten {
            pass: 0
        })
    ));

    let content = fixture
        .workspace
        .read(Path::new(".agent/ISSUES.md"))
        .expect("ISSUES.md should be written");
    assert_eq!(content, "# Issues\n\nNo issues found.\n");
}

#[test]
fn test_extract_review_issue_snippets_includes_snippets_for_locations() {
    let issues_xml = "<ralph-issues><ralph-issue>[high] src/lib.rs:2 - adjust logic</ralph-issue></ralph-issues>";
    let workspace = MemoryWorkspace::new_test()
        .with_file(artifact_paths::ISSUES_XML, issues_xml)
        .with_file("src/lib.rs", "fn main() {\n    let x = 1;\n}\n");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.review_validated_outcome = Some(ReviewValidatedOutcome {
        pass: 0,
        issues_found: true,
        clean_no_issues: false,
        issues: vec!["[high] src/lib.rs:2 - adjust logic".to_string()].into_boxed_slice(),
        no_issues_found: None,
    });
    let result = handler
        .extract_review_issue_snippets(&ctx, 0)
        .expect("extract_review_issue_snippets should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::IssueSnippetsExtracted {
            pass: 0
        })
    ));

    let snippets = result.ui_events.iter().find_map(|event| {
        if let UIEvent::XmlOutput { context, .. } = event {
            context.as_ref().map(|ctx| ctx.snippets.clone())
        } else {
            None
        }
    });

    let snippets = snippets.expect("expected XmlOutput context with snippets");
    assert_eq!(snippets.len(), 1);
    assert_eq!(snippets[0].file, "src/lib.rs");
    assert_eq!(snippets[0].line_start, 2);
    assert_eq!(snippets[0].line_end, 2);
    assert!(snippets[0].content.contains("2 |"));
    assert!(snippets[0].content.contains("let x = 1;"));
}

#[test]
fn test_extract_review_issue_snippets_includes_snippets_for_windows_paths() {
    let issues_xml =
        "<ralph-issues><ralph-issue>[high] C:\\repo\\src\\lib.rs:2 - adjust logic</ralph-issue></ralph-issues>";
    let workspace = MemoryWorkspace::new_test()
        .with_file("src/lib.rs", "fn main() {\n    let y = 2;\n}\n")
        .with_file(artifact_paths::ISSUES_XML, issues_xml);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.review_validated_outcome = Some(ReviewValidatedOutcome {
        pass: 0,
        issues_found: true,
        clean_no_issues: false,
        issues: vec!["[high] C:\\repo\\src\\lib.rs:2 - adjust logic".to_string()]
            .into_boxed_slice(),
        no_issues_found: None,
    });
    let result = handler
        .extract_review_issue_snippets(&ctx, 0)
        .expect("extract_review_issue_snippets should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::IssueSnippetsExtracted {
            pass: 0
        })
    ));

    let snippets = result.ui_events.iter().find_map(|event| {
        if let UIEvent::XmlOutput { context, .. } = event {
            context.as_ref().map(|ctx| ctx.snippets.clone())
        } else {
            None
        }
    });

    let snippets = snippets.expect("expected XmlOutput context with snippets");
    assert_eq!(snippets.len(), 1);
    assert!(snippets[0].content.contains("2 |"));
    assert!(snippets[0].content.contains("let y = 2;"));
}

#[test]
fn test_write_issues_markdown_returns_error_when_missing_validated_outcome() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let err = handler
        .write_issues_markdown(&ctx, 0)
        .expect_err("write_issues_markdown should return error when validated outcome is missing");

    assert!(
        err.to_string().contains("validated review outcome"),
        "Expected error about missing validated review outcome, got: {err}"
    );
}
