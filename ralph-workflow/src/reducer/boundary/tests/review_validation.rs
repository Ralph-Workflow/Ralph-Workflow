use super::common::TestFixture;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::state::{PipelineState, ReviewValidatedOutcome};
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use crate::workspace::MemoryWorkspace;
use crate::workspace::Workspace;
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
struct ReadFailingWorkspace {
    inner: MemoryWorkspace,
    forbidden_read_path: PathBuf,
    kind: io::ErrorKind,
}

impl ReadFailingWorkspace {
    fn new(inner: MemoryWorkspace, forbidden_read_path: PathBuf, kind: io::ErrorKind) -> Self {
        Self {
            inner,
            forbidden_read_path,
            kind,
        }
    }
}

impl Workspace for ReadFailingWorkspace {
    fn root(&self) -> &Path {
        self.inner.root()
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        if relative == self.forbidden_read_path.as_path() {
            return Err(io::Error::new(self.kind, "read forbidden (test)"));
        }
        self.inner.read(relative)
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        self.inner.read_bytes(relative)
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.append_bytes(relative, content)
    }

    fn exists(&self, relative: &Path) -> bool {
        self.inner.exists(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.inner.is_file(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.inner.is_dir(relative)
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove(relative)
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_if_exists(relative)
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all(relative)
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.inner.remove_dir_all_if_exists(relative)
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.inner.create_dir_all(relative)
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
        self.inner.read_dir(relative)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.inner.rename(from, to)
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.inner.write_atomic(relative, content)
    }

    fn set_readonly(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_readonly(relative)
    }

    fn set_writable(&self, relative: &Path) -> io::Result<()> {
        self.inner.set_writable(relative)
    }
}

#[test]
fn test_validate_review_issues_xml_emits_event_with_xml_output() {
    let issues_xml =
        "<ralph-issues><ralph-no-issues-found>ok</ralph-no-issues-found></ralph-issues>";
    let workspace = MemoryWorkspace::new_test().with_file(xml_paths::ISSUES_XML, issues_xml);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    let result = handler.validate_review_issues_xml(&ctx, 0);

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::IssuesXmlValidated {
            pass: 0,
            clean_no_issues: true,
            issues,
            no_issues_found,
            ..
        }) if issues.is_empty() && no_issues_found.as_deref() == Some("ok")
    ));

    assert!(result.ui_events.iter().any(|event| matches!(
        event,
        UIEvent::XmlOutput {
            xml_type: XmlOutputType::ReviewIssues,
            content,
            context: Some(XmlOutputContext {
                pass: Some(0),
                ..
            }),
        } if content == issues_xml
    )));
}

#[test]
fn test_validate_fix_result_xml_emits_ui_output() {
    let fix_xml =
        "<ralph-fix-result><ralph-status>all_issues_addressed</ralph-status></ralph-fix-result>";
    let workspace = MemoryWorkspace::new_test().with_file(xml_paths::FIX_RESULT_XML, fix_xml);
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
            content,
            context: Some(XmlOutputContext {
                pass: Some(0),
                ..
            }),
        } if content == fix_xml
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
fn test_write_issues_markdown_includes_skills_mcp_from_xml() {
    let issues_xml = r#"<ralph-issues>
<ralph-issue>src/main.rs:42 - Variable unused
<skills-mcp>
<skill reason="Start with failing test">test-driven-development</skill>
<mcp reason="Use for library research">context7</mcp>
</skills-mcp>
</ralph-issue>
</ralph-issues>"#;

    let workspace = MemoryWorkspace::new_test().with_file(xml_paths::ISSUES_XML, issues_xml);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.review_validated_outcome = Some(ReviewValidatedOutcome {
        pass: 0,
        issues_found: true,
        clean_no_issues: false,
        issues: vec!["src/main.rs:42 - Variable unused".to_string()].into_boxed_slice(),
        no_issues_found: None,
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

    assert!(
        content.contains("Variable unused"),
        "Should show issue text"
    );
    assert!(
        content.contains("test-driven-development"),
        "Should show skill name"
    );
    assert!(content.contains("context7"), "Should show mcp name");
    assert!(
        content.contains("Start with failing test"),
        "Should show skill reason"
    );
}

#[test]
fn test_extract_review_issue_snippets_includes_snippets_for_locations() {
    let issues_xml = "<ralph-issues><ralph-issue>[high] src/lib.rs:2 - adjust logic</ralph-issue></ralph-issues>";
    let workspace = MemoryWorkspace::new_test()
        .with_file(xml_paths::ISSUES_XML, issues_xml)
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
        .with_file(xml_paths::ISSUES_XML, issues_xml);
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
fn test_extract_review_issue_snippets_surfaces_non_not_found_issues_xml_read_errors() {
    let inner =
        MemoryWorkspace::new_test().with_file("src/lib.rs", "fn main() {\n    let x = 1;\n}\n");
    let workspace = ReadFailingWorkspace::new(
        inner.clone(),
        PathBuf::from(xml_paths::ISSUES_XML),
        io::ErrorKind::PermissionDenied,
    );

    let mut fixture = TestFixture::with_workspace(inner);
    let ctx = fixture.ctx_with_workspace(&workspace);

    let mut handler = MainEffectHandler::new(PipelineState::initial(0, 1));
    handler.state.review_validated_outcome = Some(ReviewValidatedOutcome {
        pass: 0,
        issues_found: true,
        clean_no_issues: false,
        issues: vec!["[high] src/lib.rs:2 - adjust logic".to_string()].into_boxed_slice(),
        no_issues_found: None,
    });

    let err = handler.extract_review_issue_snippets(&ctx, 0).expect_err(
        "extract_review_issue_snippets should surface non-NotFound issues.xml read failures",
    );

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                path,
                kind: WorkspaceIoErrorKind::PermissionDenied
            } if path == xml_paths::ISSUES_XML
        ),
        "expected WorkspaceReadFailed for issues.xml read, got: {error_event:?}"
    );
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
