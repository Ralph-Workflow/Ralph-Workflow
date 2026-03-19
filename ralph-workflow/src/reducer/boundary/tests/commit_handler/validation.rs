use super::super::common::TestFixture;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{CommitEvent, PipelineEvent};
use crate::reducer::state::PipelineState;
use crate::reducer::ui_event::{UIEvent, XmlOutputType};
use crate::workspace::MemoryWorkspace;

#[test]
fn validate_commit_xml_emits_ui_xml_output_even_when_xml_file_missing() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));

    let result = handler.validate_commit_xml(&ctx);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(CommitEvent::CommitXmlValidationFailed { attempt: 1, .. })
        ),
        "expected CommitXmlValidationFailed event when xml is missing, got: {:?}",
        result.event
    );

    assert!(
        result.ui_events.iter().any(|e| matches!(
            e,
            UIEvent::XmlOutput {
                xml_type: XmlOutputType::CommitMessage,
                ..
            }
        )),
        "expected UIEvent::XmlOutput(CommitMessage) even when xml missing"
    );
}

#[test]
fn validate_commit_xml_extracts_files_from_ralph_files_element() {
    let xml = "<ralph-commit>\
        <ralph-subject>feat(auth): add OAuth2 login</ralph-subject>\
        <ralph-files>\
          <ralph-file>src/auth/oauth.rs</ralph-file>\
          <ralph-file>tests/auth_test.rs</ralph-file>\
        </ralph-files>\
        </ralph-commit>";

    let workspace = MemoryWorkspace::new_test().with_file(xml_paths::COMMIT_MESSAGE_XML, xml);
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    let result = handler.validate_commit_xml(&ctx);

    match &result.event {
        PipelineEvent::Commit(CommitEvent::CommitXmlValidated { files, .. }) => {
            assert_eq!(files.len(), 2, "expected 2 files, got: {files:?}");
            assert!(
                files.contains(&"src/auth/oauth.rs".to_string()),
                "expected src/auth/oauth.rs in files: {files:?}"
            );
            assert!(
                files.contains(&"tests/auth_test.rs".to_string()),
                "expected tests/auth_test.rs in files: {files:?}"
            );
        }
        other => panic!("expected CommitXmlValidated, got: {other:?}"),
    }
}
