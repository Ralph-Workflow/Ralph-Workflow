//! Builder for assembling prompt content with size-aware references.
//!
//! This builder checks content sizes and creates appropriate references
//! (inline vs file path) for each piece of content. This prevents CLI
//! argument limits from being exceeded while still providing agents with
//! access to all necessary information.

use std::path::Path;

use super::content_reference::{
    DiffContentReference, PlanContentReference, PromptContentReference, MAX_INLINE_CONTENT_SIZE,
};
use crate::workspace::Workspace;

/// Builder for constructing prompt content with size-aware references.
///
/// This builder encapsulates the logic for determining whether content
/// should be embedded inline or referenced by file path.
pub struct PromptContentBuilder<'a> {
    workspace: &'a dyn Workspace,
    prompt_ref: Option<PromptContentReference>,
    plan_ref: Option<PlanContentReference>,
    diff_ref: Option<DiffContentReference>,
}

impl<'a> PromptContentBuilder<'a> {
    /// Create a new builder with a workspace reference.
    pub fn new(workspace: &'a dyn Workspace) -> Self {
        Self {
            workspace,
            prompt_ref: None,
            plan_ref: None,
            diff_ref: None,
        }
    }

    /// Add PROMPT content with automatic size checking.
    ///
    /// If the content exceeds [`MAX_INLINE_CONTENT_SIZE`], the builder will
    /// create a reference to the backup file instead of embedding inline.
    #[must_use]
    pub fn with_prompt(self, content: String) -> Self {
        let backup_path = self.workspace.prompt_backup();
        Self {
            prompt_ref: Some(PromptContentReference::from_content(
                content,
                &backup_path,
                "Original user requirements from PROMPT.md",
            )),
            ..self
        }
    }

    /// Add PLAN content with automatic size checking.
    ///
    /// If the content exceeds [`MAX_INLINE_CONTENT_SIZE`], the builder will
    /// create instructions to read from .agent/PLAN.md with optional XML fallback.
    #[must_use]
    pub fn with_plan(self, content: String) -> Self {
        let plan_path = Path::new(".agent/PLAN.md");
        let xml_fallback = Path::new(".agent/tmp/plan.xml");
        Self {
            plan_ref: Some(PlanContentReference::from_plan(
                content,
                plan_path,
                Some(xml_fallback),
            )),
            ..self
        }
    }

    /// Add DIFF content with automatic size checking.
    ///
    /// If the content exceeds [`MAX_INLINE_CONTENT_SIZE`], the builder will
    /// create instructions to use `git diff` instead of embedding inline.
    #[must_use]
    pub fn with_diff(self, content: String, start_commit: &str) -> Self {
        // For oversize diffs, write the diff to .agent/tmp/diff.txt so agents can read it
        // without relying on git being available.
        let is_oversize = content.len() > MAX_INLINE_CONTENT_SIZE;
        if is_oversize {
            let tmp_dir = Path::new(".agent/tmp");
            let diff_rel = tmp_dir.join("diff.txt");
            if self.workspace.create_dir_all(tmp_dir).is_ok() {
                let _ = self.workspace.write(&diff_rel, &content);
            }
        }

        let diff_abs = self.workspace.absolute(Path::new(".agent/tmp/diff.txt"));
        Self {
            diff_ref: Some(DiffContentReference::from_diff(
                content,
                start_commit,
                &diff_abs,
            )),
            ..self
        }
    }

    /// Build the references.
    ///
    /// Note: Backup files should be created before calling `build()` if needed.
    /// This builder only determines how content should be referenced.
    #[must_use]
    pub fn build(self) -> PromptContentReferences {
        PromptContentReferences {
            prompt: self.prompt_ref,
            plan: self.plan_ref,
            diff: self.diff_ref,
        }
    }

    /// Check if any content exceeds the inline size limit.
    ///
    /// This is useful for logging or debugging to see when content
    /// will be referenced by file path instead of embedded inline.
    #[must_use]
    pub fn has_oversize_content(&self) -> bool {
        let prompt_oversize = self.prompt_ref.as_ref().is_some_and(|r| !r.is_inline());
        let plan_oversize = self.plan_ref.as_ref().is_some_and(|r| !r.is_inline());
        let diff_oversize = self.diff_ref.as_ref().is_some_and(|r| !r.is_inline());

        prompt_oversize || plan_oversize || diff_oversize
    }
}

/// Container for all content references.
///
/// This struct holds the resolved references for PROMPT, PLAN, and DIFF
/// content. Each reference may be inline or a file path reference.
pub struct PromptContentReferences {
    /// Reference to PROMPT.md content.
    pub prompt: Option<PromptContentReference>,
    /// Reference to PLAN.md content.
    pub plan: Option<PlanContentReference>,
    /// Reference to diff content.
    pub diff: Option<DiffContentReference>,
}

impl PromptContentReferences {
    /// Get the PROMPT content for template rendering.
    ///
    /// Returns the content directly if inline, or instructions to read from file.
    #[must_use]
    pub fn prompt_for_template(&self) -> String {
        self.prompt
            .as_ref()
            .map(super::content_reference::PromptContentReference::render_for_template)
            .unwrap_or_default()
    }

    /// Get the PLAN content for template rendering.
    ///
    /// Returns the content directly if inline, or instructions to read from file.
    #[must_use]
    pub fn plan_for_template(&self) -> String {
        self.plan
            .as_ref()
            .map(super::content_reference::PlanContentReference::render_for_template)
            .unwrap_or_default()
    }

    /// Get the DIFF content for template rendering.
    ///
    /// Returns the content directly if inline, or instructions to use git diff.
    #[must_use]
    pub fn diff_for_template(&self) -> String {
        self.diff
            .as_ref()
            .map(super::content_reference::DiffContentReference::render_for_template)
            .unwrap_or_default()
    }

    /// Check if the PROMPT reference is inline.
    #[must_use]
    pub fn prompt_is_inline(&self) -> bool {
        self.prompt
            .as_ref()
            .is_some_and(super::content_reference::PromptContentReference::is_inline)
    }

    /// Check if the PLAN reference is inline.
    #[must_use]
    pub fn plan_is_inline(&self) -> bool {
        self.plan
            .as_ref()
            .is_some_and(super::content_reference::PlanContentReference::is_inline)
    }

    /// Check if the DIFF reference is inline.
    #[must_use]
    pub fn diff_is_inline(&self) -> bool {
        self.diff
            .as_ref()
            .is_some_and(super::content_reference::DiffContentReference::is_inline)
    }
}

#[cfg(all(test, feature = "test-utils"))]
mod tests {
    use super::*;
    use crate::prompts::MAX_INLINE_CONTENT_SIZE;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_builder_small_content() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "test");

        let builder = PromptContentBuilder::new(&workspace)
            .with_prompt("Small prompt".to_string())
            .with_plan("Small plan".to_string());

        assert!(!builder.has_oversize_content());

        let refs = builder.build();
        assert_eq!(refs.prompt_for_template(), "Small prompt");
        assert_eq!(refs.plan_for_template(), "Small plan");
    }

    #[test]
    fn test_builder_large_prompt() {
        let workspace = MemoryWorkspace::new_test().with_file(".agent/PROMPT.md.backup", "backup");

        let large_content = "x".repeat(MAX_INLINE_CONTENT_SIZE + 1);
        let builder = PromptContentBuilder::new(&workspace).with_prompt(large_content);

        assert!(builder.has_oversize_content());

        let refs = builder.build();
        let rendered = refs.prompt_for_template();
        assert!(rendered.contains("PROMPT.md.backup"));
        assert!(!refs.prompt_is_inline());
    }

    #[test]
    fn test_builder_large_plan() {
        let workspace = MemoryWorkspace::new_test().with_file(".agent/PLAN.md", "plan");

        let large_content = "x".repeat(MAX_INLINE_CONTENT_SIZE + 1);
        let builder = PromptContentBuilder::new(&workspace).with_plan(large_content);

        assert!(builder.has_oversize_content());

        let refs = builder.build();
        let rendered = refs.plan_for_template();
        assert!(rendered.contains(".agent/PLAN.md"));
        assert!(rendered.contains("plan.xml"));
        assert!(!refs.plan_is_inline());
    }

    #[test]
    fn test_builder_large_diff() {
        let workspace = MemoryWorkspace::new_test();

        let large_content = "x".repeat(MAX_INLINE_CONTENT_SIZE + 1);
        let builder = PromptContentBuilder::new(&workspace).with_diff(large_content, "abc123def");

        assert!(builder.has_oversize_content());

        let refs = builder.build();
        let rendered = refs.diff_for_template();
        assert!(
            rendered.contains(".agent/tmp/diff.txt"),
            "Oversize diff should reference .agent/tmp/diff.txt: {}",
            &rendered[..rendered.len().min(200)]
        );
        // Diff should be written for file-based fallback
        assert!(workspace.was_written(".agent/tmp/diff.txt"));
        assert!(!refs.diff_is_inline());
    }

    #[test]
    fn test_builder_no_oversize_when_all_small() {
        let workspace = MemoryWorkspace::new_test().with_file("PROMPT.md", "test");

        let builder = PromptContentBuilder::new(&workspace)
            .with_prompt("Small prompt".to_string())
            .with_plan("Small plan".to_string())
            .with_diff("Small diff".to_string(), "abc123");

        assert!(!builder.has_oversize_content());

        let refs = builder.build();
        assert!(refs.prompt_is_inline());
        assert!(refs.plan_is_inline());
        assert!(refs.diff_is_inline());
    }

    #[test]
    fn test_builder_partial_oversize() {
        let workspace = MemoryWorkspace::new_test().with_file(".agent/PROMPT.md.backup", "backup");

        // Only prompt is oversized
        let large_prompt = "x".repeat(MAX_INLINE_CONTENT_SIZE + 1);
        let builder = PromptContentBuilder::new(&workspace)
            .with_prompt(large_prompt)
            .with_plan("Small plan".to_string())
            .with_diff("Small diff".to_string(), "abc123");

        assert!(builder.has_oversize_content());

        let refs = builder.build();
        assert!(!refs.prompt_is_inline());
        assert!(refs.plan_is_inline());
        assert!(refs.diff_is_inline());
    }

    #[test]
    fn test_builder_empty_content() {
        let workspace = MemoryWorkspace::new_test();

        let refs = PromptContentBuilder::new(&workspace).build();

        assert_eq!(refs.prompt_for_template(), "");
        assert_eq!(refs.plan_for_template(), "");
        assert_eq!(refs.diff_for_template(), "");
    }

    #[test]
    fn test_refs_inline_checks_with_none() {
        let refs = PromptContentReferences {
            prompt: None,
            plan: None,
            diff: None,
        };

        // None should not be considered inline
        assert!(!refs.prompt_is_inline());
        assert!(!refs.plan_is_inline());
        assert!(!refs.diff_is_inline());
    }
}
