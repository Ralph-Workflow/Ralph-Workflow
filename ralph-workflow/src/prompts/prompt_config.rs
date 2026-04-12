//! Prompt configuration types.
//!
//! Groups related parameters for prompt generation to reduce function argument count.

use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::checkpoint::restore::ResumeContext;

/// Configuration for prompt generation.
///
/// Groups related parameters to reduce function argument count.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
#[must_use]
pub struct PromptConfig {
    /// The current iteration number (for developer iteration prompts).
    pub iteration: Option<u32>,
    /// The total number of iterations (for developer iteration prompts).
    pub total_iterations: Option<u32>,
    /// PROMPT.md content for planning prompts.
    pub prompt_md_content: Option<String>,
    /// (PROMPT.md, PLAN.md) content tuple for developer iteration prompts.
    pub prompt_and_plan: Option<(String, String)>,
    /// (PROMPT.md, PLAN.md, ISSUES.md) content tuple for fix prompts.
    pub prompt_plan_and_issues: Option<(String, String, String)>,
    /// Whether this is a resumed session (from a checkpoint).
    pub is_resume: bool,
    /// Rich resume context if available.
    pub resume_context: Option<ResumeContext>,
    /// The drain identity for this session (used to derive default capabilities).
    pub drain: Option<SessionDrain>,
    /// Capabilities granted to this session (derived from drain if not set).
    pub capabilities: Option<CapabilitySet>,
    /// Policy flags for this session (derived from drain if not set).
    pub policy_flags: Option<PolicyFlagSet>,
}

impl PromptConfig {
    /// Create a new prompt configuration with default values.
    #[must_use = "configuration is required for prompt generation"]
    pub const fn new() -> Self {
        Self {
            iteration: None,
            total_iterations: None,
            prompt_md_content: None,
            prompt_and_plan: None,
            prompt_plan_and_issues: None,
            is_resume: false,
            resume_context: None,
            drain: None,
            capabilities: None,
            policy_flags: None,
        }
    }

    /// Set iteration numbers for developer iteration prompts.
    #[must_use = "returns the updated configuration for chaining"]
    pub const fn with_iterations(mut self, iteration: u32, total: u32) -> Self {
        self.iteration = Some(iteration);
        self.total_iterations = Some(total);
        self
    }

    /// Set PROMPT.md content for planning prompts.
    #[must_use = "returns the updated configuration for chaining"]
    pub fn with_prompt_md(mut self, content: String) -> Self {
        self.prompt_md_content = Some(content);
        self
    }

    /// Set (PROMPT.md, PLAN.md) content tuple for developer iteration prompts.
    #[must_use = "returns the updated configuration for chaining"]
    pub fn with_prompt_and_plan(mut self, prompt: String, plan: String) -> Self {
        self.prompt_and_plan = Some((prompt, plan));
        self
    }

    /// Set (PROMPT.md, PLAN.md, ISSUES.md) content tuple for fix prompts.
    pub fn with_prompt_plan_and_issues(
        mut self,
        prompt: String,
        plan: String,
        issues: String,
    ) -> Self {
        self.prompt_plan_and_issues = Some((prompt, plan, issues));
        self
    }

    /// Set whether this is a resumed session.
    #[must_use = "returns the updated configuration for chaining"]
    pub const fn with_resume(mut self, is_resume: bool) -> Self {
        self.is_resume = is_resume;
        self
    }

    /// Set rich resume context for resumed sessions.
    #[must_use = "returns the updated configuration for chaining"]
    pub fn with_resume_context(mut self, context: ResumeContext) -> Self {
        self.resume_context = Some(context);
        self.is_resume = true;
        self
    }

    /// Set the drain identity for capability derivation.
    ///
    /// When `capabilities` or `policy_flags` are not explicitly set,
    /// they will be derived from this drain using the default mappings.
    #[must_use = "returns the updated configuration for chaining"]
    pub fn with_drain(mut self, drain: SessionDrain) -> Self {
        self.drain = Some(drain);
        self
    }

    /// Set explicit capabilities for this session.
    ///
    /// If not set, capabilities will be derived from `drain` using defaults.
    #[must_use = "returns the updated configuration for chaining"]
    pub fn with_capabilities(mut self, capabilities: CapabilitySet) -> Self {
        self.capabilities = Some(capabilities);
        self
    }

    /// Set explicit policy flags for this session.
    ///
    /// If not set, policy flags will be derived from `drain` using defaults.
    #[must_use = "returns the updated configuration for chaining"]
    pub fn with_policy_flags(mut self, policy_flags: PolicyFlagSet) -> Self {
        self.policy_flags = Some(policy_flags);
        self
    }

    /// Get the effective capabilities for this session.
    ///
    /// Returns explicitly set capabilities, or defaults derived from drain.
    #[must_use]
    pub fn effective_capabilities(&self) -> CapabilitySet {
        self.capabilities.clone().unwrap_or_else(|| {
            CapabilitySet::defaults_for_drain(self.drain.unwrap_or(SessionDrain::Planning))
        })
    }

    /// Get the effective policy flags for this session.
    ///
    /// Returns explicitly set policy flags, or defaults derived from drain.
    #[must_use]
    pub fn effective_policy_flags(&self) -> PolicyFlagSet {
        self.policy_flags.clone().unwrap_or_else(|| {
            PolicyFlagSet::defaults_for_drain(self.drain.unwrap_or(SessionDrain::Planning))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::{Capability, PolicyFlag, SessionDrain};

    #[test]
    fn test_prompt_config_default_is_empty() {
        let config = PromptConfig::new();
        assert!(config.drain.is_none());
        assert!(config.capabilities.is_none());
        assert!(config.policy_flags.is_none());
    }

    #[test]
    fn test_prompt_config_with_drain_sets_drain() {
        let config = PromptConfig::new().with_drain(SessionDrain::Planning);
        assert_eq!(config.drain, Some(SessionDrain::Planning));
    }

    #[test]
    fn test_prompt_config_effective_capabilities_for_planning_uses_defaults() {
        let config = PromptConfig::new().with_drain(SessionDrain::Planning);
        let caps = config.effective_capabilities();

        // Planning should have read-only capabilities
        assert!(caps.contains(Capability::WorkspaceRead));
        assert!(caps.contains(Capability::GitStatusRead));
        assert!(caps.contains(Capability::GitDiffRead));
        assert!(caps.contains(Capability::ArtifactSubmit));
        // Should NOT have write capabilities
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
        assert!(!caps.contains(Capability::ProcessExecBounded));
    }

    #[test]
    fn test_prompt_config_effective_capabilities_for_development_uses_defaults() {
        let config = PromptConfig::new().with_drain(SessionDrain::Development);
        let caps = config.effective_capabilities();

        // Development should have write capabilities
        assert!(caps.contains(Capability::WorkspaceRead));
        assert!(caps.contains(Capability::WorkspaceWriteTracked));
        assert!(caps.contains(Capability::ProcessExecBounded));
        assert!(caps.contains(Capability::GitStatusRead));
        assert!(caps.contains(Capability::GitDiffRead));
    }

    #[test]
    fn test_prompt_config_effective_capabilities_for_commit_uses_defaults() {
        let config = PromptConfig::new().with_drain(SessionDrain::Commit);
        let caps = config.effective_capabilities();

        // Commit should have git write capability
        assert!(caps.contains(Capability::GitWrite));
        assert!(caps.contains(Capability::GitStatusRead));
        assert!(caps.contains(Capability::GitDiffRead));
        // Should NOT have workspace write
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
    }

    #[test]
    fn test_prompt_config_effective_policy_flags_for_planning_has_no_edit() {
        let config = PromptConfig::new().with_drain(SessionDrain::Planning);
        let flags = config.effective_policy_flags();

        assert!(flags.contains(PolicyFlag::NoEdit));
        assert!(!flags.contains(PolicyFlag::AllowShell));
        assert!(!flags.contains(PolicyFlag::AllowGitWrite));
    }

    #[test]
    fn test_prompt_config_effective_policy_flags_for_development_has_allow_shell() {
        let config = PromptConfig::new().with_drain(SessionDrain::Development);
        let flags = config.effective_policy_flags();

        assert!(!flags.contains(PolicyFlag::NoEdit));
        assert!(flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn test_prompt_config_effective_policy_flags_for_commit_has_allow_git_write() {
        let config = PromptConfig::new().with_drain(SessionDrain::Commit);
        let flags = config.effective_policy_flags();

        assert!(!flags.contains(PolicyFlag::NoEdit));
        assert!(flags.contains(PolicyFlag::AllowGitWrite));
    }

    #[test]
    fn test_prompt_config_effective_policy_flags_for_review_has_no_edit() {
        let config = PromptConfig::new().with_drain(SessionDrain::Review);
        let flags = config.effective_policy_flags();

        assert!(flags.contains(PolicyFlag::NoEdit));
        assert!(!flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn test_prompt_config_explicit_capabilities_override_defaults() {
        let mut explicit = CapabilitySet::new();
        explicit.insert(Capability::WorkspaceRead);

        let config = PromptConfig::new()
            .with_drain(SessionDrain::Development)
            .with_capabilities(explicit);

        let caps = config.effective_capabilities();
        // Should only have the explicit capability
        assert!(caps.contains(Capability::WorkspaceRead));
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
        assert!(!caps.contains(Capability::ProcessExecBounded));
    }

    #[test]
    fn test_prompt_config_explicit_policy_flags_override_defaults() {
        let config = PromptConfig::new()
            .with_drain(SessionDrain::Development)
            .with_policy_flags(PolicyFlagSet::new()); // Empty flags

        let flags = config.effective_policy_flags();
        // Should have no flags despite Development normally having AllowShell
        assert!(!flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn test_prompt_config_planning_does_not_have_workspace_write_tracked() {
        let config = PromptConfig::new().with_drain(SessionDrain::Planning);
        let caps = config.effective_capabilities();
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
    }

    #[test]
    fn test_prompt_config_review_does_not_have_workspace_write_tracked() {
        let config = PromptConfig::new().with_drain(SessionDrain::Review);
        let caps = config.effective_capabilities();
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
    }

    #[test]
    fn test_prompt_config_analysis_does_not_have_workspace_write_tracked() {
        let config = PromptConfig::new().with_drain(SessionDrain::Analysis);
        let caps = config.effective_capabilities();
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
    }

    #[test]
    fn test_prompt_config_fix_has_workspace_write_tracked() {
        let config = PromptConfig::new().with_drain(SessionDrain::Fix);
        let caps = config.effective_capabilities();
        assert!(caps.contains(Capability::WorkspaceWriteTracked));
    }
}
