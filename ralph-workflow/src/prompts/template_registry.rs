//! Template Registry for Runtime Template Loading
//!
//! This module provides a centralized template registry that supports:
//! - User-defined template overrides (from `~/.config/ralph/templates/`)
//! - Embedded templates as fallback (compiled into binary)
//! - Runtime template loading with caching
//!
//! # Template Loading Priority
//!
//! 1. User template: `{user_templates_dir}/{name}.txt`
//! 2. Embedded template: Compiled-in fallback
//! 3. Error: Template not found

use std::path::PathBuf;

use super::io::{get_xdg_config_home, load_template, template_exists};

/// Error type for template loading and rendering operations.
#[derive(Debug, Clone, thiserror::Error)]
pub enum TemplateError {
    /// Template not found in user directory or embedded catalog
    #[error("Template '{name}' not found")]
    TemplateNotFound { name: String },

    /// Error reading user template file
    #[error("Failed to read template '{name}': {reason}")]
    ReadError { name: String, reason: String },

    /// Missing required variable in template
    #[error("Missing required variable: {0}")]
    MissingVariable(String),

    /// Circular reference in partial templates
    #[error("Circular reference in templates: {0}")]
    CircularReference(String),

    /// Partial template not found
    #[error("Partial template not found: {0}")]
    PartialNotFound(String),
}

/// Template registry for loading templates from multiple sources.
///
/// The registry maintains a user templates directory for template overrides.
/// Templates are loaded from user directory first, falling back to embedded templates.
#[derive(Debug, Clone)]
pub struct TemplateRegistry {
    /// User templates directory (higher priority than embedded templates).
    user_templates_dir: Option<PathBuf>,
}

impl TemplateRegistry {
    /// Create a new template registry.
    ///
    /// # Arguments
    ///
    /// * `user_templates_dir` - Optional path to user templates directory.
    ///   When set, templates in this directory override embedded templates.
    #[must_use]
    pub const fn new(user_templates_dir: Option<PathBuf>) -> Self {
        Self { user_templates_dir }
    }

    /// Get the default user templates directory path.
    ///
    /// Returns `~/.config/ralph/templates/` by default.
    /// Respects `XDG_CONFIG_HOME` environment variable.
    ///
    /// # Returns
    ///
    /// `None` if home directory cannot be determined.
    #[must_use]
    pub fn default_user_templates_dir() -> Option<PathBuf> {
        if let Some(xdg) = get_xdg_config_home() {
            let xdg_str = xdg.to_string_lossy().trim().to_string();
            if !xdg_str.is_empty() {
                return Some(PathBuf::from(xdg_str).join("ralph").join("templates"));
            }
        }

        dirs::home_dir().map(|d| d.join(".config").join("ralph").join("templates"))
    }

    /// Check if a user template exists for the given name.
    ///
    /// # Returns
    ///
    /// `true` if a user template file exists (not embedded)
    #[must_use]
    pub fn has_user_template(&self, name: &str) -> bool {
        self.user_templates_dir
            .as_ref()
            .is_some_and(|user_dir| user_dir.join(format!("{name}.txt")).exists())
    }

    /// Get the source of a template (user or embedded).
    ///
    /// # Returns
    ///
    /// * `"user"` - Template is from user directory
    /// * `"embedded"` - Template is embedded
    #[must_use]
    pub fn template_source(&self, name: &str) -> &'static str {
        if self.has_user_template(name) {
            "user"
        } else {
            "embedded"
        }
    }

    /// Load a template by name.
    ///
    /// Template loading priority:
    /// 1. User template: `{user_templates_dir}/{name}.txt`
    /// 2. Embedded template from catalog
    /// 3. Error if neither exists
    ///
    /// # Arguments
    ///
    /// * `name` - Template name (without `.txt` extension)
    ///
    /// # Returns
    ///
    /// * `Ok(String)` - Template content
    /// * `Err(TemplateError)` - Template not found or read error
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn get_template(&self, name: &str) -> Result<String, TemplateError> {
        use crate::prompts::template_catalog;

        // Try user template first
        if let Some(user_dir) = &self.user_templates_dir {
            let user_path = user_dir.join(format!("{name}.txt"));
            if template_exists(&user_path) {
                return load_template(&user_path).map_err(|e| TemplateError::ReadError {
                    name: name.to_string(),
                    reason: e.to_string(),
                });
            }
        }

        // Fall back to embedded template
        if let Some(content) = template_catalog::get_embedded_template(name) {
            return Ok(content);
        }

        // Template not found
        Err(TemplateError::TemplateNotFound {
            name: name.to_string(),
        })
    }

    /// Get all template names available in the embedded catalog.
    ///
    /// # Returns
    ///
    /// A vector of all embedded template names, sorted alphabetically.
    #[must_use]
    #[cfg(test)]
    pub fn all_template_names() -> Vec<String> {
        use crate::prompts::template_catalog;
        template_catalog::list_all_templates()
            .iter()
            .map(|t| t.name.to_string())
            .collect()
    }

    /// Check if a template exists (either user or embedded).
    ///
    /// # Returns
    ///
    /// `true` if the template exists in user directory or embedded catalog
    #[must_use]
    #[cfg(test)]
    pub fn template_exists(&self, name: &str) -> bool {
        self.has_user_template(name) || self.get_template(name).is_ok()
    }
}

impl Default for TemplateRegistry {
    fn default() -> Self {
        Self::new(Self::default_user_templates_dir())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_registry_creation() {
        let registry = TemplateRegistry::new(None);
        assert!(registry.user_templates_dir.is_none());

        let custom_dir = PathBuf::from("/custom/templates");
        let registry = TemplateRegistry::new(Some(custom_dir.clone()));
        assert_eq!(registry.user_templates_dir, Some(custom_dir));
    }

    #[test]
    fn test_default_user_templates_dir() {
        let dir = TemplateRegistry::default_user_templates_dir();
        assert!(dir.is_some());
        let path = dir.unwrap();
        assert!(path.to_string_lossy().contains("templates"));
    }

    #[test]
    fn test_has_user_template_no_dir() {
        let registry = TemplateRegistry::new(None);
        assert!(!registry.has_user_template("commit_message_xml"));
    }

    #[test]
    fn test_template_source_no_dir() {
        let registry = TemplateRegistry::new(None);
        let source = registry.template_source("commit_message_xml");
        assert_eq!(source, "embedded");
    }

    #[test]
    fn test_template_source_not_found() {
        let registry = TemplateRegistry::new(None);
        let source = registry.template_source("nonexistent_template");
        assert_eq!(source, "embedded");
    }

    #[test]
    fn test_default_registry() {
        let registry = TemplateRegistry::default();
        // Default registry should have a user templates dir if home dir exists
        if TemplateRegistry::default_user_templates_dir().is_some() {
            assert!(registry.user_templates_dir.is_some());
        }
    }

    #[test]
    fn test_get_template_embedded() {
        let registry = TemplateRegistry::new(None);
        let result = registry.get_template("developer_iteration_xml");
        assert!(result.is_ok());
        let content = result.unwrap();
        assert!(!content.is_empty());
        assert!(content.contains("IMPLEMENTATION MODE") || content.contains("Developer"));
    }

    #[test]
    fn test_get_template_not_found() {
        let registry = TemplateRegistry::new(None);
        let result = registry.get_template("nonexistent_template");
        assert!(result.is_err());
        assert!(matches!(
            result.unwrap_err(),
            TemplateError::TemplateNotFound { .. }
        ));
    }

    #[test]
    fn test_all_template_names() {
        let names = TemplateRegistry::all_template_names();
        assert!(!names.is_empty());
        assert!(names.len() >= 10); // At least 10 templates (reduced after removing unused reviewer templates)
        assert!(names.contains(&"developer_iteration_xml".to_string()));
        assert!(names.contains(&"commit_message_xml".to_string()));
    }

    #[test]
    fn test_template_exists_embedded() {
        let registry = TemplateRegistry::new(None);
        assert!(registry.template_exists("developer_iteration_xml"));
        assert!(registry.template_exists("commit_message_xml"));
    }

    #[test]
    fn test_template_not_exists() {
        let registry = TemplateRegistry::new(None);
        assert!(!registry.template_exists("nonexistent_template"));
    }

    #[test]
    fn test_get_commit_template() {
        let registry = TemplateRegistry::new(None);
        let result = registry.get_template("commit_message_xml");
        assert!(result.is_ok());
        let content = result.unwrap();
        assert!(!content.is_empty());
    }

    #[test]
    fn test_get_review_xml_template() {
        let registry = TemplateRegistry::new(None);
        // The review phase uses review_xml template
        let result = registry.get_template("review_xml");
        assert!(result.is_ok());
        let content = result.unwrap();
        assert!(!content.is_empty());
        assert!(content.contains("REVIEW MODE"));
    }

    #[test]
    fn test_get_fix_mode_template() {
        let registry = TemplateRegistry::new(None);
        let result = registry.get_template("fix_mode_xml");
        assert!(result.is_ok());
        let content = result.unwrap();
        assert!(!content.is_empty());
    }

    #[test]
    fn test_all_templates_have_content() {
        let registry = TemplateRegistry::new(None);
        TemplateRegistry::all_template_names()
            .into_iter()
            .for_each(|name| {
                let result = registry.get_template(&name);
                assert!(result.is_ok(), "Template '{name}' should load successfully");
                let content = result.unwrap();
                assert!(!content.is_empty(), "Template '{name}' should not be empty");
            });
    }
}
