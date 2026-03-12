//! Integration test for XSD retry missing file detection.
//!
//! Verifies that XSD retry prompts detect missing schema files and `last_output.xml`
//! and emit actionable diagnostics including workspace root path.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::prompts::{
    prompt_commit_xsd_retry_with_context, prompt_developer_iteration_xsd_retry_with_context_files,
    prompt_fix_xsd_retry_with_context_files, prompt_planning_xsd_retry_with_context_files,
    prompt_review_xsd_retry_with_context_files, TemplateContext,
};
use ralph_workflow::workspace::{DirEntry, MemoryWorkspace, Workspace};
use std::io;
use std::path::{Path, PathBuf};

use crate::test_timeout::with_default_timeout;

struct FailingXsdRetryWorkspace {
    root: PathBuf,
}

impl FailingXsdRetryWorkspace {
    fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }
}

impl Workspace for FailingXsdRetryWorkspace {
    fn root(&self) -> &Path {
        &self.root
    }

    fn read(&self, _relative: &Path) -> io::Result<String> {
        Err(io::Error::other("read not supported in test workspace"))
    }

    fn read_bytes(&self, _relative: &Path) -> io::Result<Vec<u8>> {
        Err(io::Error::other(
            "read_bytes not supported in test workspace",
        ))
    }

    fn write(&self, _relative: &Path, _content: &str) -> io::Result<()> {
        Err(io::Error::other("write blocked for fallback prompt test"))
    }

    fn write_bytes(&self, _relative: &Path, _content: &[u8]) -> io::Result<()> {
        Err(io::Error::other(
            "write_bytes blocked for fallback prompt test",
        ))
    }

    fn append_bytes(&self, _relative: &Path, _content: &[u8]) -> io::Result<()> {
        Err(io::Error::other(
            "append_bytes blocked for fallback prompt test",
        ))
    }

    fn exists(&self, _relative: &Path) -> bool {
        false
    }

    fn is_file(&self, _relative: &Path) -> bool {
        false
    }

    fn is_dir(&self, _relative: &Path) -> bool {
        false
    }

    fn remove(&self, _relative: &Path) -> io::Result<()> {
        Err(io::Error::other("remove not supported in test workspace"))
    }

    fn remove_if_exists(&self, _relative: &Path) -> io::Result<()> {
        Ok(())
    }

    fn remove_dir_all(&self, _relative: &Path) -> io::Result<()> {
        Err(io::Error::other(
            "remove_dir_all not supported in test workspace",
        ))
    }

    fn remove_dir_all_if_exists(&self, _relative: &Path) -> io::Result<()> {
        Ok(())
    }

    fn create_dir_all(&self, _relative: &Path) -> io::Result<()> {
        Err(io::Error::other(
            "create_dir_all blocked for fallback prompt test",
        ))
    }

    fn read_dir(&self, _relative: &Path) -> io::Result<Vec<DirEntry>> {
        Err(io::Error::other("read_dir not supported in test workspace"))
    }

    fn rename(&self, _from: &Path, _to: &Path) -> io::Result<()> {
        Err(io::Error::other("rename not supported in test workspace"))
    }

    fn write_atomic(&self, _relative: &Path, _content: &str) -> io::Result<()> {
        Err(io::Error::other(
            "write_atomic not supported in test workspace",
        ))
    }

    fn set_readonly(&self, _relative: &Path) -> io::Result<()> {
        Ok(())
    }

    fn set_writable(&self, _relative: &Path) -> io::Result<()> {
        Ok(())
    }
}

/// Test that planning XSD retry detects missing schema file.
#[test]
fn test_planning_xsd_retry_detects_missing_schema() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt = prompt_planning_xsd_retry_with_context_files(
            &template_context,
            "Test error",
            &workspace,
        );

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that review XSD retry detects missing files.
#[test]
fn test_review_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt =
            prompt_review_xsd_retry_with_context_files(&template_context, "Test error", &workspace);

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that development XSD retry detects missing files.
#[test]
fn test_development_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt = prompt_developer_iteration_xsd_retry_with_context_files(
            &template_context,
            "Test error",
            &workspace,
            true,
        );

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()")
                && prompt.contains("development_continuation_result.xsd"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that continuation fallback keeps the continuation-only XML contract.
#[test]
fn test_development_xsd_retry_fallback_uses_continuation_contract() {
    with_default_timeout(|| {
        let workspace = FailingXsdRetryWorkspace::new("/tmp/failing_xsd_retry_workspace");
        let template_context = TemplateContext::default();

        let prompt = prompt_developer_iteration_xsd_retry_with_context_files(
            &template_context,
            "Test error",
            &workspace,
            true,
        );

        assert!(
            prompt.contains("development_continuation_result.xsd"),
            "Continuation fallback should mention the continuation schema path"
        );
        assert!(
            prompt.contains("<ralph-status>partial|failed</ralph-status>"),
            "Continuation fallback should restrict status to partial|failed"
        );
        assert!(
            prompt.contains("<ralph-next-steps>1."),
            "Continuation fallback should require ordered next steps"
        );
        assert!(
            !prompt.contains("<ralph-status>completed|partial|failed</ralph-status>"),
            "Continuation fallback should not fall back to the generic development XML contract"
        );
        assert!(
            !prompt.contains("<ralph-files-changed>"),
            "Continuation fallback should omit file bookkeeping"
        );
    });
}

/// Test that fix XSD retry detects missing files.
#[test]
fn test_fix_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt =
            prompt_fix_xsd_retry_with_context_files(&template_context, "Test error", &workspace);

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}

/// Test that commit XSD retry detects missing files.
#[test]
fn test_commit_xsd_retry_detects_missing_files() {
    with_default_timeout(|| {
        let workspace_root = PathBuf::from("/tmp/test_workspace");
        let workspace = MemoryWorkspace::new(workspace_root);
        let template_context = TemplateContext::default();

        // Generate XSD retry prompt with missing schema
        let prompt =
            prompt_commit_xsd_retry_with_context(&template_context, "Test error", &workspace);

        // Verify: prompt indicates missing file AND includes workspace root
        assert!(
            prompt.contains("WARNING: Required XSD retry files are missing")
                && prompt.contains("workspace.root()"),
            "Should detect missing schema AND include workspace root diagnostics. Got prompt: \n{prompt}"
        );
    });
}
