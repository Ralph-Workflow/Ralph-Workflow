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
/// This function creates the map of all embedded templates using functional construction.
fn build_embedded_templates() -> BTreeMap<&'static str, EmbeddedTemplate> {
    [
        // Commit Templates
        (
            "commit_message_xml",
            EmbeddedTemplate {
                name: "commit_message_xml",
                content: include_str!("templates/commit_message_xml.txt"),
                description: "Generate Conventional Commits messages from git diffs (XML format)",
                deprecated: false,
            },
        ),
        (
            "commit_simplified",
            EmbeddedTemplate {
                name: "commit_simplified",
                content: include_str!("templates/commit_simplified.txt"),
                description: "Simplified commit prompt with direct instructions",
                deprecated: false,
            },
        ),
        // Analysis Templates
        (
            "analysis_system_prompt",
            EmbeddedTemplate {
                name: "analysis_system_prompt",
                content: include_str!("templates/analysis_system_prompt.txt"),
                description: "Independent analysis agent system prompt (verifies PLAN vs DIFF and writes development_result.xml)",
                deprecated: false,
            },
        ),
        (
            "fix_analysis_system_prompt",
            EmbeddedTemplate {
                name: "fix_analysis_system_prompt",
                content: include_str!("templates/fix_analysis_system_prompt.txt"),
                description: "Independent analysis agent system prompt for verifying fix output against review issues",
                deprecated: false,
            },
        ),
        // Developer Templates
        (
            "developer_iteration_xml",
            EmbeddedTemplate {
                name: "developer_iteration_xml",
                content: include_str!("templates/developer_iteration_xml.txt"),
                description: "Developer agent implementation mode prompt (no structured output; analysis verifies progress)",
                deprecated: false,
            },
        ),
        (
            "planning_xml",
            EmbeddedTemplate {
                name: "planning_xml",
                content: include_str!("templates/planning_xml.txt"),
                description: "Planning phase prompt with XML output format and XSD validation",
                deprecated: false,
            },
        ),
        (
            "developer_iteration_continuation_xml",
            EmbeddedTemplate {
                name: "developer_iteration_continuation_xml",
                content: include_str!("templates/developer_iteration_continuation_xml.txt"),
                description: "Continuation prompt when previous attempt returned partial/failed",
                deprecated: false,
            },
        ),
        // Review XML Templates
        (
            "review_xml",
            EmbeddedTemplate {
                name: "review_xml",
                content: include_str!("templates/review_xml.txt"),
                description: "Review mode prompt with XML output format and XSD validation",
                deprecated: false,
            },
        ),
        // Fix Mode Templates
        (
            "fix_mode_xml",
            EmbeddedTemplate {
                name: "fix_mode_xml",
                content: include_str!("templates/fix_mode_xml.txt"),
                description: "Fix mode prompt with XML output format and XSD validation",
                deprecated: false,
            },
        ),
        // Rebase Templates
        (
            "conflict_resolution",
            EmbeddedTemplate {
                name: "conflict_resolution",
                content: include_str!("templates/conflict_resolution.txt"),
                description: "Merge conflict resolution prompt",
                deprecated: false,
            },
        ),
        (
            "conflict_resolution_fallback",
            EmbeddedTemplate {
                name: "conflict_resolution_fallback",
                content: include_str!("templates/conflict_resolution_fallback.txt"),
                description: "Fallback conflict resolution prompt",
                deprecated: false,
            },
        ),
        // Phase 4: Parallel Worker Templates
        (
            "parallel_planning_xml",
            EmbeddedTemplate {
                name: "parallel_planning_xml",
                content: include_str!("templates/parallel_planning_xml.txt"),
                description: "Parallel planning phase prompt with XML output for splitting work across workers",
                deprecated: false,
            },
        ),
        (
            "parallel_dev_worker_xml",
            EmbeddedTemplate {
                name: "parallel_dev_worker_xml",
                content: include_str!("templates/parallel_dev_worker_xml.txt"),
                description: "Parallel development worker prompt scoped to restricted edit area",
                deprecated: false,
            },
        ),
        (
            "parallel_verifier_xml",
            EmbeddedTemplate {
                name: "parallel_verifier_xml",
                content: include_str!("templates/parallel_verifier_xml.txt"),
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
        let result = get_embedded_template("developer_iteration_xml");
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
        let metadata = get_template_metadata("commit_message_xml");
        assert!(metadata.is_some());
        let template = metadata.unwrap();
        assert_eq!(template.name, "commit_message_xml");
        assert!(!template.description.is_empty());
    }

    #[test]
    fn test_list_all_templates() {
        let templates = list_all_templates();
        assert!(!templates.is_empty());
        assert!(templates.len() >= 10); // At least 10 templates (reduced after removing unused reviewer templates)

        assert!(templates
            .windows(2)
            .all(|window| window[0].name <= window[1].name));
    }

    #[test]
    fn test_get_templates_map() {
        let map = get_templates_map();
        assert!(!map.is_empty());
        assert!(map.contains_key("developer_iteration_xml"));
        assert!(map.contains_key("commit_message_xml"));

        let (content, description) = map.get("developer_iteration_xml").unwrap();
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
    fn test_legacy_non_xml_templates_removed() {
        // Verify legacy non-XML templates have been removed
        assert!(get_embedded_template("developer_iteration").is_none());
        assert!(get_embedded_template("planning").is_none());
        assert!(get_embedded_template("fix_mode").is_none());
        // Note: Use *_xml variants instead
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
        // Note: The review phase uses review_xml template via prompt_review_xml_with_context()
    }

    #[test]
    fn test_review_xml_template_exists() {
        // Verify the actually-used review template exists
        assert!(get_embedded_template("review_xml").is_some());
        let content = get_embedded_template("review_xml").unwrap();
        assert!(
            content.contains("REVIEW MODE"),
            "review_xml should contain REVIEW MODE"
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
