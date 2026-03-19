//! Tests for unified cleanup of stale XML files.
//!
//! These tests verify that the unified `cleanup_required_files` method
//! correctly removes XML files from the workspace.

use super::common::TestFixture;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::reducer::event::PipelinePhase;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{AgentChainState, PipelineState};
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

#[test]
fn test_invoke_planning_agent_does_not_clear_stale_plan_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/planning_prompt.txt", "prompt")
        .with_file(xml_paths::PLAN_XML, "<ralph-plan>old</ralph-plan>");
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    handler
        .invoke_planning_agent(&mut ctx, 0)
        .expect("invoke_planning_agent should succeed");

    drop(ctx);
    assert!(fixture.workspace.exists(Path::new(xml_paths::PLAN_XML)));
}

#[test]
fn test_cleanup_required_files_clears_stale_plan_xml() {
    let workspace =
        MemoryWorkspace::new_test().with_file(xml_paths::PLAN_XML, "<ralph-plan>old</ralph-plan>");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    let files: Box<[String]> = vec![xml_paths::PLAN_XML.to_string()].into_boxed_slice();
    handler.cleanup_required_files(&ctx, &files);

    drop(ctx);
    assert!(!fixture.workspace.exists(Path::new(xml_paths::PLAN_XML)));
}

#[test]
fn test_invoke_development_agent_does_not_clear_stale_dev_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/development_prompt.txt", "prompt")
        .with_file(
            xml_paths::DEVELOPMENT_RESULT_XML,
            "<ralph-development>old</ralph-development>",
        );
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    handler
        .invoke_development_agent(&mut ctx, 0)
        .expect("invoke_development_agent should succeed");

    drop(ctx);
    assert!(fixture
        .workspace
        .exists(Path::new(xml_paths::DEVELOPMENT_RESULT_XML)));
}

#[test]
fn test_cleanup_required_files_clears_stale_dev_xml() {
    let workspace = MemoryWorkspace::new_test().with_file(
        xml_paths::DEVELOPMENT_RESULT_XML,
        "<ralph-development>old</ralph-development>",
    );
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    let files: Box<[String]> =
        vec![xml_paths::DEVELOPMENT_RESULT_XML.to_string()].into_boxed_slice();
    handler.cleanup_required_files(&ctx, &files);

    drop(ctx);
    assert!(!fixture
        .workspace
        .exists(Path::new(xml_paths::DEVELOPMENT_RESULT_XML)));
}

#[test]
fn test_invoke_review_agent_does_not_clear_stale_issues_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/review_prompt.txt", "prompt")
        .with_file(xml_paths::ISSUES_XML, "<ralph-issues>old</ralph-issues>");
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    handler
        .invoke_review_agent(&mut ctx, 0)
        .expect("invoke_review_agent should succeed");

    drop(ctx);
    assert!(fixture.workspace.exists(Path::new(xml_paths::ISSUES_XML)));
}

#[test]
fn test_cleanup_required_files_clears_stale_issues_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(xml_paths::ISSUES_XML, "<ralph-issues>old</ralph-issues>");
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    let files: Box<[String]> = vec![xml_paths::ISSUES_XML.to_string()].into_boxed_slice();
    handler.cleanup_required_files(&ctx, &files);

    drop(ctx);
    assert!(!fixture.workspace.exists(Path::new(xml_paths::ISSUES_XML)));
}

#[test]
fn test_invoke_fix_agent_does_not_clear_stale_fix_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/fix_prompt.txt", "prompt")
        .with_file(
            xml_paths::FIX_RESULT_XML,
            "<ralph-fix-result>old</ralph-fix-result>",
        );
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    handler
        .invoke_fix_agent(&mut ctx, 0)
        .expect("invoke_fix_agent should succeed");

    drop(ctx);
    assert!(fixture
        .workspace
        .exists(Path::new(xml_paths::FIX_RESULT_XML)));
}

#[test]
fn test_cleanup_required_files_clears_stale_fix_xml() {
    let workspace = MemoryWorkspace::new_test().with_file(
        xml_paths::FIX_RESULT_XML,
        "<ralph-fix-result>old</ralph-fix-result>",
    );
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    let files: Box<[String]> = vec![xml_paths::FIX_RESULT_XML.to_string()].into_boxed_slice();
    handler.cleanup_required_files(&ctx, &files);

    drop(ctx);
    assert!(!fixture
        .workspace
        .exists(Path::new(xml_paths::FIX_RESULT_XML)));
}

#[test]
fn test_invoke_commit_agent_does_not_clear_stale_commit_xml() {
    let workspace = MemoryWorkspace::new_test()
        .with_file(".agent/tmp/commit_prompt.txt", "prompt")
        .with_file(
            xml_paths::COMMIT_MESSAGE_XML,
            "<ralph-commit>old</ralph-commit>",
        );
    let mut fixture = TestFixture::with_workspace(workspace);
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";
    ctx.reviewer_agent = "codex";
    let mut handler = MainEffectHandler::new(PipelineState {
        agent_chain: AgentChainState::initial().with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..PipelineState::initial(1, 1)
    });

    handler
        .invoke_commit_agent(&mut ctx)
        .expect("invoke_commit_agent should succeed");

    drop(ctx);
    assert!(fixture
        .workspace
        .exists(Path::new(xml_paths::COMMIT_MESSAGE_XML)));
}

#[test]
fn test_cleanup_required_files_clears_stale_commit_xml() {
    let workspace = MemoryWorkspace::new_test().with_file(
        xml_paths::COMMIT_MESSAGE_XML,
        "<ralph-commit>old</ralph-commit>",
    );
    let mut fixture = TestFixture::with_workspace(workspace);
    let ctx = fixture.ctx();
    let handler = MainEffectHandler::new(PipelineState {
        phase: PipelinePhase::CommitMessage,
        ..PipelineState::initial(1, 1)
    });

    let files: Box<[String]> = vec![xml_paths::COMMIT_MESSAGE_XML.to_string()].into_boxed_slice();
    handler.cleanup_required_files(&ctx, &files);

    drop(ctx);
    assert!(!fixture
        .workspace
        .exists(Path::new(xml_paths::COMMIT_MESSAGE_XML)));
}
