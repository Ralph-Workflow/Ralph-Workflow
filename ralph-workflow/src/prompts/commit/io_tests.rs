use super::*;
use crate::agents::session::SessionDrain;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::SessionCapabilities;
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;

#[test]
fn test_prompt_generate_commit_message_with_diff_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let diff = "diff --git a/src/main.rs b/src/main.rs\n+fn new_func() {}";
    let result = prompt_generate_commit_message_with_diff_with_context(
        &context,
        diff,
        &workspace,
        session_caps,
    );
    assert!(!result.is_empty());
    assert!(result.contains("DIFF:") || result.contains("diff"));
    assert!(!result.contains("ERROR: Empty diff"));
}

#[test]
fn test_prompt_generate_commit_message_with_diff_with_context_empty() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let result = prompt_generate_commit_message_with_diff_with_context(
        &context,
        "",
        &workspace,
        session_caps,
    );
    assert!(result.contains("ERROR: Empty diff"));
}

#[test]
fn test_prompt_commit_xsd_retry_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let result = prompt_commit_xsd_retry_with_context(&context, "xsd error", &workspace);
    assert!(result.contains("WARNING: Required XSD retry files are missing:"));
}
