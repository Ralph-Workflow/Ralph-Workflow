use super::*;
use crate::prompts::template_context::TemplateContext;
use crate::workspace::MemoryWorkspace;
use std::path::PathBuf;

#[test]
fn test_prompt_review_xml_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new(PathBuf::from("/tmp/test"));
    let result = prompt_review_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test changes",
        &workspace,
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
    let result = prompt_fix_xml_with_context(
        &context,
        "test prompt",
        "test plan",
        "test issues",
        &[],
        &workspace,
    );
    assert!(result.contains("test issues"));
    assert!(result.contains("FIX MODE"));
}

#[test]
fn test_prompt_review_xsd_retry_with_context() {
    let context = TemplateContext::default();
    let workspace = MemoryWorkspace::new_test();
    let result = prompt_review_xsd_retry_with_context(
        &context,
        "test prompt",
        "test plan",
        "test changes",
        "XSD error",
        "last output",
        &workspace,
    );
    assert!(result.contains("XSD error"));
    assert!(result.contains(".agent/tmp/issues.xml"));
    assert!(result.contains(".agent/tmp/issues.xsd"));
}
