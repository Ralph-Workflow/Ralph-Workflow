//! Embedded Template Catalog
//!
//! Central registry of all embedded templates with metadata.
//!
//! This module provides a single source of truth for all embedded templates,
//! consolidating scattered `include_str!` calls across the codebase.

use std::collections::BTreeMap;

/// Metadata about an embedded template.
#[derive(Debug, Clone)]
pub struct EmbeddedTemplate {
    /// Template name (used for lookup and user override files)
    pub name: &'static str,
    /// Template content
    pub content: &'static str,
    /// Human-readable description
    pub description: &'static str,
    /// Whether this template is deprecated
    pub deprecated: bool,
}

/// Get an embedded template by name.
///
/// # Returns
///
/// * `Some(String)` - Template content if found
/// * `None` - Template not found
#[must_use]
pub fn get_embedded_template(name: &str) -> Option<String> {
    let templates = embedded_templates();
    templates.get(name).map(|t| t.content.to_string())
}

/// Get metadata about an embedded template.
///
/// # Returns
///
/// * `Some(&EmbeddedTemplate)` - Template metadata if found
/// * `None` - Template not found
#[must_use]
pub fn get_template_metadata(name: &str) -> Option<EmbeddedTemplate> {
    let templates = embedded_templates();
    templates.get(name).cloned()
}

/// List all available embedded templates.
///
/// # Returns
///
/// A vector of all embedded templates with metadata, sorted by name.
#[must_use]
pub fn list_all_templates() -> Vec<EmbeddedTemplate> {
    // BTreeMap iterates values in key-sorted order
    let templates = embedded_templates();
    templates.values().cloned().collect()
}

/// Get all templates as a map.
///
/// Returns templates in the format used by CLI template management code.
#[must_use]
pub fn get_templates_map() -> BTreeMap<String, (String, String)> {
    let templates = list_all_templates();
    templates
        .into_iter()
        .map(|template| {
            (
                template.name.to_string(),
                (
                    template.content.to_string(),
                    template.description.to_string(),
                ),
            )
        })
        .collect()
}

/// Build the embedded templates map.
///
/// All canonical-named templates (without `_xml` suffix) are loaded from
/// exported constants in the `ralph-workflow-policy` crate, which owns the
/// prompt asset files. Using constants rather than a runtime lookup avoids
/// `.expect()` outside of boundary modules.
fn build_embedded_templates() -> BTreeMap<&'static str, EmbeddedTemplate> {
    use ralph_workflow_policy::{
        ANALYSIS_SYSTEM_PROMPT_TEMPLATE, COMMIT_MESSAGE_TEMPLATE, COMMIT_SIMPLIFIED_TEMPLATE,
        CONFLICT_RESOLUTION_FALLBACK_TEMPLATE, CONFLICT_RESOLUTION_TEMPLATE,
        DEVELOPER_ITERATION_CONTINUATION_TEMPLATE, DEVELOPER_ITERATION_TEMPLATE,
        FIX_ANALYSIS_SYSTEM_PROMPT_TEMPLATE, FIX_MODE_TEMPLATE, PARALLEL_DEV_WORKER_TEMPLATE,
        PARALLEL_PLANNING_TEMPLATE, PARALLEL_VERIFIER_TEMPLATE, PLANNING_TEMPLATE, REVIEW_TEMPLATE,
    };
    [
        // Commit Templates — canonical names (no _xml suffix)
        (
            "commit_message",
            EmbeddedTemplate {
                name: "commit_message",
                content: COMMIT_MESSAGE_TEMPLATE,
                description: "Generate Conventional Commits messages from git diffs",
                deprecated: false,
            },
        ),
        (
            "commit_simplified",
            EmbeddedTemplate {
                name: "commit_simplified",
                content: COMMIT_SIMPLIFIED_TEMPLATE,
                description: "Simplified commit prompt with direct instructions",
                deprecated: false,
            },
        ),
        // Analysis Templates — canonical names
        (
            "analysis_system_prompt",
            EmbeddedTemplate {
                name: "analysis_system_prompt",
                content: ANALYSIS_SYSTEM_PROMPT_TEMPLATE,
                description: "Independent analysis agent system prompt (verifies PLAN vs DIFF)",
                deprecated: false,
            },
        ),
        (
            "fix_analysis_system_prompt",
            EmbeddedTemplate {
                name: "fix_analysis_system_prompt",
                content: FIX_ANALYSIS_SYSTEM_PROMPT_TEMPLATE,
                description: "Independent analysis agent system prompt for verifying fix output",
                deprecated: false,
            },
        ),
        // Developer Templates — canonical names (no _xml suffix)
        (
            "developer_iteration",
            EmbeddedTemplate {
                name: "developer_iteration",
                content: DEVELOPER_ITERATION_TEMPLATE,
                description:
                    "Developer agent implementation mode prompt (analysis verifies progress)",
                deprecated: false,
            },
        ),
        (
            "planning",
            EmbeddedTemplate {
                name: "planning",
                content: PLANNING_TEMPLATE,
                description: "Planning phase prompt",
                deprecated: false,
            },
        ),
        (
            "developer_iteration_continuation",
            EmbeddedTemplate {
                name: "developer_iteration_continuation",
                content: DEVELOPER_ITERATION_CONTINUATION_TEMPLATE,
                description: "Continuation prompt when previous attempt returned partial/failed",
                deprecated: false,
            },
        ),
        // Review Templates — canonical name (no _xml suffix)
        (
            "review",
            EmbeddedTemplate {
                name: "review",
                content: REVIEW_TEMPLATE,
                description: "Review mode prompt",
                deprecated: false,
            },
        ),
        // Fix Mode Templates — canonical name (no _xml suffix)
        (
            "fix_mode",
            EmbeddedTemplate {
                name: "fix_mode",
                content: FIX_MODE_TEMPLATE,
                description: "Fix mode prompt",
                deprecated: false,
            },
        ),
        // Rebase Templates — no rename needed (never had _xml suffix)
        (
            "conflict_resolution",
            EmbeddedTemplate {
                name: "conflict_resolution",
                content: CONFLICT_RESOLUTION_TEMPLATE,
                description: "Merge conflict resolution prompt",
                deprecated: false,
            },
        ),
        (
            "conflict_resolution_fallback",
            EmbeddedTemplate {
                name: "conflict_resolution_fallback",
                content: CONFLICT_RESOLUTION_FALLBACK_TEMPLATE,
                description: "Fallback conflict resolution prompt",
                deprecated: false,
            },
        ),
        // Parallel Worker Templates — canonical names (no _xml suffix)
        (
            "parallel_planning",
            EmbeddedTemplate {
                name: "parallel_planning",
                content: PARALLEL_PLANNING_TEMPLATE,
                description: "Parallel planning phase prompt for splitting work across workers",
                deprecated: false,
            },
        ),
        (
            "parallel_dev_worker",
            EmbeddedTemplate {
                name: "parallel_dev_worker",
                content: PARALLEL_DEV_WORKER_TEMPLATE,
                description: "Parallel development worker prompt scoped to restricted edit area",
                deprecated: false,
            },
        ),
        (
            "parallel_verifier",
            EmbeddedTemplate {
                name: "parallel_verifier",
                content: PARALLEL_VERIFIER_TEMPLATE,
                description: "Verifier/reconciler prompt for reviewing parallel worker outputs",
                deprecated: false,
            },
        ),
    ]
    .into_iter()
    .collect()
}

/// Get the embedded templates map.
///
/// Builds templates each call to avoid interior mutability.
fn embedded_templates() -> BTreeMap<&'static str, EmbeddedTemplate> {
    build_embedded_templates()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_embedded_template_existing() {
        // Uses canonical name without _xml suffix
        let result = get_embedded_template("developer_iteration");
        assert!(result.is_some());
        let content = result.unwrap();
        assert!(!content.is_empty());
        assert!(content.contains("IMPLEMENTATION MODE") || content.contains("Developer"));
    }

    #[test]
    fn test_get_embedded_template_not_found() {
        let result = get_embedded_template("nonexistent_template");
        assert!(result.is_none());
    }

    #[test]
    fn test_get_template_metadata() {
        // Uses canonical name without _xml suffix
        let metadata = get_template_metadata("commit_message");
        assert!(metadata.is_some());
        let template = metadata.unwrap();
        assert_eq!(template.name, "commit_message");
        assert!(!template.description.is_empty());
    }

    #[test]
    fn test_list_all_templates() {
        let templates = list_all_templates();
        assert!(!templates.is_empty());
        assert!(templates.len() >= 10); // At least 10 templates

        assert!(templates
            .windows(2)
            .all(|window| window[0].name <= window[1].name));
    }

    #[test]
    fn test_get_templates_map() {
        let map = get_templates_map();
        assert!(!map.is_empty());
        // Canonical names (no _xml suffix)
        assert!(map.contains_key("developer_iteration"));
        assert!(map.contains_key("commit_message"));

        let (content, description) = map.get("developer_iteration").unwrap();
        assert!(!content.is_empty());
        assert!(!description.is_empty());
    }

    #[test]
    fn test_all_templates_have_content() {
        let templates = list_all_templates();
        assert!(templates
            .iter()
            .all(|template| !template.content.is_empty()));
    }

    #[test]
    fn test_all_templates_have_descriptions() {
        let templates = list_all_templates();
        assert!(templates
            .iter()
            .all(|template| !template.description.is_empty()));
    }

    #[test]
    fn test_fallback_templates_removed() {
        // Verify legacy fallback templates have been removed
        assert!(get_embedded_template("developer_iteration_fallback").is_none());
        assert!(get_embedded_template("planning_fallback").is_none());
        assert!(get_embedded_template("fix_mode_fallback").is_none());
        // Note: Fallbacks are now embedded in code as inline strings, not separate .txt files
    }

    #[test]
    fn test_xml_suffixed_template_names_removed() {
        // Verify legacy _xml-suffixed template names no longer exist in the catalog.
        // Templates have been migrated to canonical names (planning, developer_iteration, etc.)
        assert!(get_embedded_template("developer_iteration_xml").is_none());
        assert!(get_embedded_template("planning_xml").is_none());
        assert!(get_embedded_template("fix_mode_xml").is_none());
        assert!(get_embedded_template("review_xml").is_none());
        assert!(get_embedded_template("commit_message_xml").is_none());
        assert!(get_embedded_template("parallel_planning_xml").is_none());
        assert!(get_embedded_template("parallel_dev_worker_xml").is_none());
        assert!(get_embedded_template("parallel_verifier_xml").is_none());
        assert!(get_embedded_template("developer_iteration_continuation_xml").is_none());
    }

    #[test]
    fn test_canonical_template_names_exist() {
        // Canonical names (no _xml suffix) must exist after migration
        assert!(get_embedded_template("developer_iteration").is_some());
        assert!(get_embedded_template("planning").is_some());
        assert!(get_embedded_template("fix_mode").is_some());
        assert!(get_embedded_template("review").is_some());
        assert!(get_embedded_template("commit_message").is_some());
        assert!(get_embedded_template("developer_iteration_continuation").is_some());
    }

    #[test]
    fn test_unused_reviewer_templates_removed() {
        // Verify the unused reviewer templates have been removed
        // These templates were registered but never used in production code
        assert!(get_embedded_template("standard_review").is_none());
        assert!(get_embedded_template("comprehensive_review").is_none());
        assert!(get_embedded_template("security_review").is_none());
        assert!(get_embedded_template("universal_review").is_none());
        assert!(get_embedded_template("standard_review_minimal").is_none());
        assert!(get_embedded_template("standard_review_normal").is_none());
    }

    #[test]
    fn test_review_template_exists() {
        // Verify the review template exists under its canonical name
        assert!(get_embedded_template("review").is_some());
        let content = get_embedded_template("review").unwrap();
        assert!(
            content.contains("REVIEW MODE"),
            "review template should contain REVIEW MODE"
        );
    }

    #[test]
    fn test_all_templates_include_no_git_commit_partial() {
        let templates = list_all_templates();
        assert!(templates
            .iter()
            .all(|template| template.content.contains("{{> shared/_no_git_commit}}")));
    }
}
