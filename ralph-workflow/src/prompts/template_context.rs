//! Template context for prompt generation.
//!
//! This module provides the `TemplateContext` struct which holds the
//! template registry and is passed through the application to enable
//! user template overrides.

use super::template_registry::TemplateRegistry;
use std::path::PathBuf;

/// Context for template-based prompt generation.
///
/// Provides access to the template registry for loading user-customizable
/// templates. This context is created from the application config and passed
/// to prompt generation functions.
///
/// # Example
///
/// ```ignore
/// let context = TemplateContext::from_user_templates_dir(Some(PathBuf::from("~/.config/ralph/templates")));
/// let prompt = prompt_developer_iteration_xml_with_context(
///     &context,
///     1, 5, ContextLevel::Normal, "prompt", "plan"
/// );
/// ```
#[derive(Debug, Clone)]
pub struct TemplateContext {
    /// Template registry for loading templates.
    pub(crate) registry: TemplateRegistry,
}

impl TemplateContext {
    /// Create a new template context with the given registry.
    #[must_use]
    pub const fn new(registry: TemplateRegistry) -> Self {
        Self { registry }
    }

    /// Create a template context from a config's user templates directory.
    ///
    /// This is the recommended way to create a `TemplateContext` as it
    /// respects the user's configured templates directory.
    #[must_use]
    pub const fn from_user_templates_dir(user_templates_dir: Option<PathBuf>) -> Self {
        Self::new(TemplateRegistry::new(user_templates_dir))
    }

    /// Get a reference to the template registry.
    #[must_use]
    pub const fn registry(&self) -> &TemplateRegistry {
        &self.registry
    }
}

impl Default for TemplateContext {
    fn default() -> Self {
        Self::new(TemplateRegistry::default())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_template_context_creation() {
        let context = TemplateContext::new(TemplateRegistry::new(None));
        // Should not have user templates since we passed None
        assert!(!context.registry().has_user_template("developer_iteration"));
    }

    #[test]
    fn test_template_context_default() {
        let context = TemplateContext::default();
        // Default should work and access templates
        assert!(context.registry().template_exists("developer_iteration"));
    }

    #[test]
    fn test_template_context_from_user_templates_dir() {
        let custom_dir = PathBuf::from("/custom/templates");
        let context = TemplateContext::from_user_templates_dir(Some(custom_dir));
        // Context should be created successfully
        assert!(!context.registry().has_user_template("developer_iteration"));
    }

    #[test]
    fn test_template_context_from_user_templates_dir_none() {
        let context = TemplateContext::from_user_templates_dir(None);
        // Should not have user templates
        assert!(!context.registry().has_user_template("developer_iteration"));
    }

    #[test]
    fn test_template_context_registry_access() {
        let context = TemplateContext::default();
        let _registry = context.registry();
        // Should be able to access registry methods
        assert!(!TemplateRegistry::all_template_names().is_empty());
    }

    #[test]
    fn test_template_context_clone() {
        let context = TemplateContext::default();
        let _cloned = context.clone();
        // Verify clone compiles and original still works
        assert!(context.registry().template_exists("developer_iteration"));
    }

    #[test]
    fn test_template_context_get_template() {
        let context = TemplateContext::default();
        // Should be able to get templates
        let result = context.registry().get_template("developer_iteration");
        assert!(result.is_ok());
    }

    #[test]
    fn test_template_context_template_source() {
        let context = TemplateContext::default();
        // Should report embedded source for templates that don't have user overrides
        assert_eq!(
            context.registry().template_source("developer_iteration"),
            "embedded"
        );
    }

    #[test]
    fn test_template_context_all_templates() {
        let _context = TemplateContext::default();
        // Should be able to list all templates
        let names = TemplateRegistry::all_template_names();
        assert!(names.len() > 10);
        assert!(names.contains(&"developer_iteration".to_string()));
    }

    #[test]
    fn test_template_context_all_templates_not_empty() {
        let _context = TemplateContext::default();
        let names = TemplateRegistry::all_template_names();
        assert!(!names.is_empty());
    }
}
