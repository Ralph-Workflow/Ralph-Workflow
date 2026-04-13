use super::*;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::prompts::template_context::TemplateContext;
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;

#[test]
fn test_prompt_review_xml_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let result = prompt_review_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test changes",
        &workspace,
        SessionCapabilities::new(&capabilities, &policy_flags),
    );
    assert!(!result.contains("test prompt"));
    assert!(result.contains("PROMPT.md.backup"));
    assert!(result.contains("test plan"));
    assert!(result.contains("test changes"));
    assert!(result.contains("REVIEW MODE"));
}

#[test]
fn test_prompt_fix_xml_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Fix);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Fix);
    let result = prompt_fix_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test issues",
        &[],
        &workspace,
        SessionCapabilities::new(&capabilities, &policy_flags),
    );
    assert!(result.contains("test issues"));
    assert!(result.contains("FIX MODE"));
}
