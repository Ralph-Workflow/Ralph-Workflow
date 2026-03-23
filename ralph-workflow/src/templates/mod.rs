//! Prompt template management module.
//!
//! This module provides a collection of PROMPT.md templates for different
//! task types (feature specifications, bug fixes, refactoring, etc.).
//!
//! Templates are embedded at compile time using `include_str!` and can be
//! accessed via the `get_template_content()` function.

use std::fmt;

/// Available prompt template types.
///
/// Each variant represents a different template for a specific use case.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PromptTemplate {
    /// Comprehensive product specification template
    FeatureSpec,
    /// Concise bug fix template
    BugFix,
    /// Code refactoring template
    Refactor,
    /// Test writing template
    Test,
    /// Documentation update template
    Docs,
    /// Quick/small change template
    Quick,
    /// Performance optimization template
    PerformanceOptimization,
    /// Security audit template
    SecurityAudit,
    /// API integration template
    ApiIntegration,
    /// Database migration template
    DatabaseMigration,
    /// Dependency update template
    DependencyUpdate,
    /// CLI tool development template
    CliTool,
    /// Web API development template
    WebApi,
    /// Data pipeline template
    DataPipeline,
    /// UI component template
    UiComponent,
    /// Code review template
    CodeReview,
    /// Debug triage template
    DebugTriage,
    /// Release preparation template
    Release,
    /// Technical debt refactoring template
    TechDebt,
    /// Onboarding template
    Onboarding,
}

impl PromptTemplate {
    /// Returns the name/key for this template (used for CLI arguments).
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::FeatureSpec => "feature-spec",
            Self::BugFix => "bug-fix",
            Self::Refactor => "refactor",
            Self::Test => "test",
            Self::Docs => "docs",
            Self::Quick => "quick",
            Self::PerformanceOptimization => "performance-optimization",
            Self::SecurityAudit => "security-audit",
            Self::ApiIntegration => "api-integration",
            Self::DatabaseMigration => "database-migration",
            Self::DependencyUpdate => "dependency-update",
            Self::CliTool => "cli-tool",
            Self::WebApi => "web-api",
            Self::DataPipeline => "data-pipeline",
            Self::UiComponent => "ui-component",
            Self::CodeReview => "code-review",
            Self::DebugTriage => "debug-triage",
            Self::Release => "release",
            Self::TechDebt => "tech-debt",
            Self::Onboarding => "onboarding",
        }
    }

    /// Returns a short description of this template.
    #[must_use]
    pub const fn description(self) -> &'static str {
        match self {
            Self::FeatureSpec => "Comprehensive product specification with questions to consider and code quality standards",
            Self::BugFix => "Bug fix template with investigation guidance and testing requirements",
            Self::Refactor => "Code refactoring template with behavior preservation emphasis",
            Self::Test => "Test writing template with edge case considerations",
            Self::Docs => "Documentation update template with completeness checklist",
            Self::Quick => "Quick/small change template (minimal)",
            Self::PerformanceOptimization => "Performance optimization template with benchmarking and profiling guidance",
            Self::SecurityAudit => "Security audit template covering OWASP Top 10 and vulnerability remediation",
            Self::ApiIntegration => "API integration template with error handling, retry logic, and resilience patterns",
            Self::DatabaseMigration => "Database migration template with zero-downtime strategies and rollback plans",
            Self::DependencyUpdate => "Dependency update template with migration guides and breaking change handling",
            Self::CliTool => "CLI tool development template with argument parsing, completion, and error handling",
            Self::WebApi => "Web API development template with REST design, error handling, and security considerations",
            Self::DataPipeline => "Data pipeline template with ETL processing, reliability, and monitoring guidance",
            Self::UiComponent => "UI component template with accessibility, responsive design, and user experience",
            Self::CodeReview => "Code review template for structured pull request feedback",
            Self::DebugTriage => "Debug triage template for systematic issue investigation and diagnosis",
            Self::Release => "Release preparation template with versioning, changelog, and deployment checklist",
            Self::TechDebt => "Technical debt refactoring template with prioritization and planning guidance",
            Self::Onboarding => "Onboarding template for learning new codebases efficiently",
        }
    }

    /// Returns the embedded template content.
    #[must_use]
    pub const fn content(self) -> &'static str {
        match self {
            Self::FeatureSpec => {
                include_str!("../../templates/prompts/feature-spec.md")
            }
            Self::BugFix => {
                include_str!("../../templates/prompts/bug-fix.md")
            }
            Self::Refactor => {
                include_str!("../../templates/prompts/refactor.md")
            }
            Self::Test => {
                include_str!("../../templates/prompts/test.md")
            }
            Self::Docs => {
                include_str!("../../templates/prompts/docs.md")
            }
            Self::Quick => {
                include_str!("../../templates/prompts/quick.md")
            }
            Self::PerformanceOptimization => {
                include_str!("../../templates/prompts/performance-optimization.md")
            }
            Self::SecurityAudit => {
                include_str!("../../templates/prompts/security-audit.md")
            }
            Self::ApiIntegration => {
                include_str!("../../templates/prompts/api-integration.md")
            }
            Self::DatabaseMigration => {
                include_str!("../../templates/prompts/database-migration.md")
            }
            Self::DependencyUpdate => {
                include_str!("../../templates/prompts/dependency-update.md")
            }
            Self::CliTool => {
                include_str!("../../templates/prompts/cli-tool.md")
            }
            Self::WebApi => {
                include_str!("../../templates/prompts/web-api.md")
            }
            Self::DataPipeline => {
                include_str!("../../templates/prompts/data-pipeline.md")
            }
            Self::UiComponent => {
                include_str!("../../templates/prompts/ui-component.md")
            }
            Self::CodeReview => {
                include_str!("../../templates/prompts/code-review.md")
            }
            Self::DebugTriage => {
                include_str!("../../templates/prompts/debug-triage.md")
            }
            Self::Release => {
                include_str!("../../templates/prompts/release.md")
            }
            Self::TechDebt => {
                include_str!("../../templates/prompts/tech-debt.md")
            }
            Self::Onboarding => {
                include_str!("../../templates/prompts/onboarding.md")
            }
        }
    }
}

impl fmt::Display for PromptTemplate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.name())
    }
}

/// All available prompt templates.
pub const ALL_TEMPLATES: [PromptTemplate; 20] = [
    PromptTemplate::FeatureSpec,
    PromptTemplate::BugFix,
    PromptTemplate::Refactor,
    PromptTemplate::Test,
    PromptTemplate::Docs,
    PromptTemplate::Quick,
    PromptTemplate::PerformanceOptimization,
    PromptTemplate::SecurityAudit,
    PromptTemplate::ApiIntegration,
    PromptTemplate::DatabaseMigration,
    PromptTemplate::DependencyUpdate,
    PromptTemplate::CliTool,
    PromptTemplate::WebApi,
    PromptTemplate::DataPipeline,
    PromptTemplate::UiComponent,
    PromptTemplate::CodeReview,
    PromptTemplate::DebugTriage,
    PromptTemplate::Release,
    PromptTemplate::TechDebt,
    PromptTemplate::Onboarding,
];

/// Get a template by name.
///
/// # Arguments
///
/// * `name` - The template name (e.g., "feature-spec", "bug-fix")
///
/// # Returns
///
/// * `Some(PromptTemplate)` - The template if found
/// * `None` - If no template matches the name
#[must_use]
pub fn get_template(name: &str) -> Option<PromptTemplate> {
    match name {
        "feature-spec" => Some(PromptTemplate::FeatureSpec),
        "bug-fix" => Some(PromptTemplate::BugFix),
        "refactor" => Some(PromptTemplate::Refactor),
        "test" => Some(PromptTemplate::Test),
        "docs" => Some(PromptTemplate::Docs),
        "quick" => Some(PromptTemplate::Quick),
        "performance-optimization" => Some(PromptTemplate::PerformanceOptimization),
        "security-audit" => Some(PromptTemplate::SecurityAudit),
        "api-integration" => Some(PromptTemplate::ApiIntegration),
        "database-migration" => Some(PromptTemplate::DatabaseMigration),
        "dependency-update" => Some(PromptTemplate::DependencyUpdate),
        "cli-tool" => Some(PromptTemplate::CliTool),
        "web-api" => Some(PromptTemplate::WebApi),
        "data-pipeline" => Some(PromptTemplate::DataPipeline),
        "ui-component" => Some(PromptTemplate::UiComponent),
        "code-review" => Some(PromptTemplate::CodeReview),
        "debug-triage" => Some(PromptTemplate::DebugTriage),
        "release" => Some(PromptTemplate::Release),
        "tech-debt" => Some(PromptTemplate::TechDebt),
        "onboarding" => Some(PromptTemplate::Onboarding),
        _ => None,
    }
}

/// List all available templates with their descriptions.
///
/// Returns a vector of (name, description) tuples.
#[must_use]
pub fn list_templates() -> Vec<(&'static str, &'static str)> {
    vec![
        (
            PromptTemplate::FeatureSpec.name(),
            PromptTemplate::FeatureSpec.description(),
        ),
        (
            PromptTemplate::BugFix.name(),
            PromptTemplate::BugFix.description(),
        ),
        (
            PromptTemplate::Refactor.name(),
            PromptTemplate::Refactor.description(),
        ),
        (
            PromptTemplate::Test.name(),
            PromptTemplate::Test.description(),
        ),
        (
            PromptTemplate::Docs.name(),
            PromptTemplate::Docs.description(),
        ),
        (
            PromptTemplate::Quick.name(),
            PromptTemplate::Quick.description(),
        ),
        (
            PromptTemplate::PerformanceOptimization.name(),
            PromptTemplate::PerformanceOptimization.description(),
        ),
        (
            PromptTemplate::SecurityAudit.name(),
            PromptTemplate::SecurityAudit.description(),
        ),
        (
            PromptTemplate::ApiIntegration.name(),
            PromptTemplate::ApiIntegration.description(),
        ),
        (
            PromptTemplate::DatabaseMigration.name(),
            PromptTemplate::DatabaseMigration.description(),
        ),
        (
            PromptTemplate::DependencyUpdate.name(),
            PromptTemplate::DependencyUpdate.description(),
        ),
        (
            PromptTemplate::CliTool.name(),
            PromptTemplate::CliTool.description(),
        ),
        (
            PromptTemplate::WebApi.name(),
            PromptTemplate::WebApi.description(),
        ),
        (
            PromptTemplate::DataPipeline.name(),
            PromptTemplate::DataPipeline.description(),
        ),
        (
            PromptTemplate::UiComponent.name(),
            PromptTemplate::UiComponent.description(),
        ),
        (
            PromptTemplate::CodeReview.name(),
            PromptTemplate::CodeReview.description(),
        ),
        (
            PromptTemplate::DebugTriage.name(),
            PromptTemplate::DebugTriage.description(),
        ),
        (
            PromptTemplate::Release.name(),
            PromptTemplate::Release.description(),
        ),
        (
            PromptTemplate::TechDebt.name(),
            PromptTemplate::TechDebt.description(),
        ),
        (
            PromptTemplate::Onboarding.name(),
            PromptTemplate::Onboarding.description(),
        ),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_template_names() {
        assert_eq!(PromptTemplate::FeatureSpec.name(), "feature-spec");
        assert_eq!(PromptTemplate::BugFix.name(), "bug-fix");
        assert_eq!(PromptTemplate::Refactor.name(), "refactor");
        assert_eq!(PromptTemplate::Test.name(), "test");
        assert_eq!(PromptTemplate::Docs.name(), "docs");
        assert_eq!(PromptTemplate::Quick.name(), "quick");
        assert_eq!(
            PromptTemplate::PerformanceOptimization.name(),
            "performance-optimization"
        );
        assert_eq!(PromptTemplate::SecurityAudit.name(), "security-audit");
        assert_eq!(PromptTemplate::ApiIntegration.name(), "api-integration");
        assert_eq!(
            PromptTemplate::DatabaseMigration.name(),
            "database-migration"
        );
        assert_eq!(PromptTemplate::DependencyUpdate.name(), "dependency-update");
        assert_eq!(PromptTemplate::CliTool.name(), "cli-tool");
        assert_eq!(PromptTemplate::WebApi.name(), "web-api");
        assert_eq!(PromptTemplate::DataPipeline.name(), "data-pipeline");
        assert_eq!(PromptTemplate::UiComponent.name(), "ui-component");
        assert_eq!(PromptTemplate::CodeReview.name(), "code-review");
        assert_eq!(PromptTemplate::DebugTriage.name(), "debug-triage");
        assert_eq!(PromptTemplate::Release.name(), "release");
        assert_eq!(PromptTemplate::TechDebt.name(), "tech-debt");
        assert_eq!(PromptTemplate::Onboarding.name(), "onboarding");
    }

    #[test]
    fn test_template_descriptions() {
        assert!(!PromptTemplate::FeatureSpec.description().is_empty());
        assert!(!PromptTemplate::BugFix.description().is_empty());
        assert!(!PromptTemplate::Refactor.description().is_empty());
        assert!(!PromptTemplate::Test.description().is_empty());
        assert!(!PromptTemplate::Docs.description().is_empty());
        assert!(!PromptTemplate::Quick.description().is_empty());
        assert!(!PromptTemplate::PerformanceOptimization
            .description()
            .is_empty());
        assert!(!PromptTemplate::SecurityAudit.description().is_empty());
        assert!(!PromptTemplate::ApiIntegration.description().is_empty());
        assert!(!PromptTemplate::DatabaseMigration.description().is_empty());
        assert!(!PromptTemplate::DependencyUpdate.description().is_empty());
        assert!(!PromptTemplate::CliTool.description().is_empty());
        assert!(!PromptTemplate::WebApi.description().is_empty());
        assert!(!PromptTemplate::DataPipeline.description().is_empty());
        assert!(!PromptTemplate::UiComponent.description().is_empty());
        assert!(!PromptTemplate::CodeReview.description().is_empty());
        assert!(!PromptTemplate::DebugTriage.description().is_empty());
        assert!(!PromptTemplate::Release.description().is_empty());
        assert!(!PromptTemplate::TechDebt.description().is_empty());
        assert!(!PromptTemplate::Onboarding.description().is_empty());
    }

    #[test]
    fn test_get_template() {
        assert_eq!(
            get_template("feature-spec"),
            Some(PromptTemplate::FeatureSpec)
        );
        assert_eq!(get_template("bug-fix"), Some(PromptTemplate::BugFix));
        assert_eq!(
            get_template("performance-optimization"),
            Some(PromptTemplate::PerformanceOptimization)
        );
        assert_eq!(
            get_template("security-audit"),
            Some(PromptTemplate::SecurityAudit)
        );
        assert_eq!(
            get_template("api-integration"),
            Some(PromptTemplate::ApiIntegration)
        );
        assert_eq!(
            get_template("database-migration"),
            Some(PromptTemplate::DatabaseMigration)
        );
        assert_eq!(
            get_template("dependency-update"),
            Some(PromptTemplate::DependencyUpdate)
        );
        assert_eq!(get_template("cli-tool"), Some(PromptTemplate::CliTool));
        assert_eq!(get_template("web-api"), Some(PromptTemplate::WebApi));
        assert_eq!(
            get_template("data-pipeline"),
            Some(PromptTemplate::DataPipeline)
        );
        assert_eq!(
            get_template("ui-component"),
            Some(PromptTemplate::UiComponent)
        );
        assert_eq!(
            get_template("code-review"),
            Some(PromptTemplate::CodeReview)
        );
        assert_eq!(
            get_template("debug-triage"),
            Some(PromptTemplate::DebugTriage)
        );
        assert_eq!(get_template("release"), Some(PromptTemplate::Release));
        assert_eq!(get_template("tech-debt"), Some(PromptTemplate::TechDebt));
        assert_eq!(get_template("onboarding"), Some(PromptTemplate::Onboarding));
        assert_eq!(get_template("nonexistent"), None);
    }

    #[test]
    fn test_list_templates() {
        let templates = list_templates();
        assert_eq!(templates.len(), 20);
        assert!(templates.iter().any(|(name, _)| name == &"feature-spec"));
        assert!(templates.iter().any(|(name, _)| name == &"bug-fix"));
        assert!(templates
            .iter()
            .any(|(name, _)| name == &"performance-optimization"));
        assert!(templates.iter().any(|(name, _)| name == &"security-audit"));
        assert!(templates.iter().any(|(name, _)| name == &"api-integration"));
        assert!(templates
            .iter()
            .any(|(name, _)| name == &"database-migration"));
        assert!(templates
            .iter()
            .any(|(name, _)| name == &"dependency-update"));
        assert!(templates.iter().any(|(name, _)| name == &"cli-tool"));
        assert!(templates.iter().any(|(name, _)| name == &"web-api"));
        assert!(templates.iter().any(|(name, _)| name == &"data-pipeline"));
        assert!(templates.iter().any(|(name, _)| name == &"ui-component"));
        assert!(templates.iter().any(|(name, _)| name == &"code-review"));
        assert!(templates.iter().any(|(name, _)| name == &"debug-triage"));
        assert!(templates.iter().any(|(name, _)| name == &"release"));
        assert!(templates.iter().any(|(name, _)| name == &"tech-debt"));
        assert!(templates.iter().any(|(name, _)| name == &"onboarding"));
    }

    #[test]
    fn test_template_content_has_goal() {
        ALL_TEMPLATES.iter().for_each(|template| {
            let content = template.content();
            assert!(
                content.contains("## Goal"),
                "Template {} missing Goal section",
                template.name()
            );
        });
    }

    #[test]
    fn test_template_content_has_acceptance() {
        ALL_TEMPLATES.iter().for_each(|template| {
            let content = template.content();
            assert!(
                content.contains("## Acceptance") || content.contains("## Acceptance Checks"),
                "Template {} missing Acceptance section",
                template.name()
            );
        });
    }
}
