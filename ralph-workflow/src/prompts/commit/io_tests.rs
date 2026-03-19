use super::*;
use crate::prompts::template_context::TemplateContext;
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;

#[test]
fn test_prompt_generate_commit_message_with_diff_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let diff = "diff --git a/src/main.rs b/src/main.rs\n+fn new_func() {}";
    let result = prompt_generate_commit_message_with_diff_with_context(&context, diff, &workspace);
    assert!(!result.is_empty());
    assert!(result.contains("DIFF:") || result.contains("diff"));
    assert!(!result.contains("ERROR: Empty diff"));
}

#[test]
fn test_prompt_generate_commit_message_with_diff_with_context_empty() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let result = prompt_generate_commit_message_with_diff_with_context(&context, "", &workspace);
    assert!(result.contains("ERROR: Empty diff"));
}

#[test]
fn test_prompt_commit_xsd_retry_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let result = prompt_commit_xsd_retry_with_context(&context, "xsd error", &workspace);
    assert!(result.contains("WARNING: Required XSD retry files are missing:"));
}
