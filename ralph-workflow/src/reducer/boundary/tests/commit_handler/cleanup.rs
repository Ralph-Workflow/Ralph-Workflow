//! Tests for commit phase XML cleanup.
//!
//! These tests verify that the unified `cleanup_required_files` method
//! correctly removes commit XML files from the workspace.

use super::super::common::TestFixture;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::reducer::event::PipelinePhase;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{AgentChainState, CommitState, PipelineState};
use crate::workspace::Workspace;
use std::path::Path;

#[test]
fn test_cleanup_required_files_removes_stale_commit_xml() {
    let workspace = crate::workspace::MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_prompt.txt", "commit prompt")
        .with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit-message>old</ralph-commit-message>",
        );
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();

    let handler = MainEffectHandler::new(PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 2,
        },
        agent_chain: AgentChainState::initial(),
        ..PipelineState::initial(1, 1)
    });

    let files: Box<[String]> = vec![xml_paths::COMMIT_MESSAGE_XML.to_string()].into_boxed_slice();
    handler.cleanup_required_files(&ctx, &files);

    assert!(
        !fixture
            .workspace
            .exists(Path::new(xml_paths::COMMIT_MESSAGE_XML)),
        "stale commit XML should be cleared before invoking commit agent"
    );
}
