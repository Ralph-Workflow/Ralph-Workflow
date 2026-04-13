//! Snapshot tests for capability-driven prompt generation.
//!
//! These tests verify that prompt rendering produces the correct output for each
//! drain type, ensuring that:
//! 1. The capability summary section is included
//! 2. The correct restrictions are applied based on capabilities
//! 3. Legacy behavior is preserved (same output as before for each drain type)
//! 4. Session-derived capabilities produce identical output to drain-default capabilities

#[cfg(test)]
mod tests {
    use crate::agents::session::{
        AgentSession, Capability, CapabilitySet, PolicyFlagSet, SessionDrain,
    };
    use crate::prompts::capability_template_variables_from_session;
    use crate::prompts::partials::get_shared_partials;
    use crate::prompts::template_engine::Template;
    use crate::prompts::template_variables::capability_template_variables;
    use std::collections::HashMap;

    /// Test that Planning drain prompt includes capability summary and correct restrictions.
    #[test]
    fn test_planning_prompt_capability_driven() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::PLANNING_TEMPLATE;
        let template = Template::new(template_content);

        // Use Planning drain defaults
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Planning);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Planning);
        let cap_vars = capability_template_variables(&caps, &flags);

        // Base variables for planning prompt
        let base_vars: HashMap<&str, String> = HashMap::from([
            ("PROMPT", "test requirements".to_string()),
            ("PLAN_XML_PATH", "/test/plan.xml".to_string()),
            ("PLAN_XSD_PATH", "/test/plan.xsd".to_string()),
        ]);

        // Merge variables
        let variables: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(cap_vars)
            .collect();

        let variables_ref: HashMap<&str, String> = variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();

        let result = template
            .render_with_partials(&variables_ref, &partials)
            .expect("template rendering should succeed");

        // Verify capability summary is present
        assert!(
            result.contains("SESSION CAPABILITIES"),
            "Planning prompt should include capability summary"
        );
        assert!(
            result.contains("Capabilities:"),
            "Planning prompt should list granted capabilities"
        );

        // Verify restrictions based on capabilities
        // Planning has no GitWrite, so _no_git_commit should be included
        assert!(
            result.contains("Do NOT run ANY git command except read-only lookup commands"),
            "Planning prompt should include git restrictions (no GitWrite capability)"
        );

        // Verify read-only constraints are present
        assert!(
            result.contains("READ-ONLY"),
            "Planning prompt should be read-only"
        );
        assert!(
            result.contains("STRICTLY PROHIBITED"),
            "Planning prompt should have explicit prohibition"
        );
    }

    /// Test that Development drain prompt includes capability summary and correct restrictions.
    #[test]
    fn test_development_prompt_capability_driven() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::DEVELOPER_ITERATION_TEMPLATE;
        let template = Template::new(template_content);

        // Use Development drain defaults
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Development);
        let cap_vars = capability_template_variables(&caps, &flags);

        // Base variables for developer iteration prompt
        let base_vars: HashMap<&str, String> = HashMap::from([
            ("PROMPT", "test prompt".to_string()),
            ("PLAN", "test plan".to_string()),
        ]);

        // Merge variables
        let variables: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(cap_vars)
            .collect();

        let variables_ref: HashMap<&str, String> = variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();

        let result = template
            .render_with_partials(&variables_ref, &partials)
            .expect("template rendering should succeed");

        // Verify capability summary is present
        assert!(
            result.contains("SESSION CAPABILITIES"),
            "Development prompt should include capability summary"
        );

        // Verify restrictions based on capabilities
        // Development has no GitWrite, so _no_git_commit should be included
        assert!(
            result.contains("Do NOT run ANY git command except read-only lookup commands"),
            "Development prompt should include git restrictions (no GitWrite capability)"
        );

        // Verify implementation mode is present
        assert!(
            result.contains("IMPLEMENTATION MODE"),
            "Development prompt should be in implementation mode"
        );
    }

    /// Test that Review drain prompt includes capability summary and correct restrictions.
    #[test]
    fn test_review_prompt_capability_driven() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::REVIEW_TEMPLATE;
        let template = Template::new(template_content);

        // Use Review drain defaults
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Review);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
        let cap_vars = capability_template_variables(&caps, &flags);

        // Base variables for review prompt
        let base_vars: HashMap<&str, String> = HashMap::from([
            ("PLAN", "test plan".to_string()),
            ("CHANGES", "test changes".to_string()),
            ("ISSUES_XML_PATH", "/test/issues.xml".to_string()),
            ("ISSUES_XSD_PATH", "/test/issues.xsd".to_string()),
        ]);

        // Merge variables
        let variables: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(cap_vars)
            .collect();

        let variables_ref: HashMap<&str, String> = variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();

        let result = template
            .render_with_partials(&variables_ref, &partials)
            .expect("template rendering should succeed");

        // Verify capability summary is present
        assert!(
            result.contains("SESSION CAPABILITIES"),
            "Review prompt should include capability summary"
        );

        // Verify restrictions based on capabilities
        // Review has no GitWrite, so _no_git_commit should be included
        assert!(
            result.contains("Do NOT run ANY git command except read-only lookup commands"),
            "Review prompt should include git restrictions (no GitWrite capability)"
        );

        // Verify review mode constraints
        assert!(
            result.contains("DO NOT MODIFY ANY CODE"),
            "Review prompt should prohibit code modification"
        );
    }

    /// Test that Commit drain prompt excludes _no_git_commit when GitWrite is granted.
    #[test]
    fn test_commit_prompt_capability_driven() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::COMMIT_MESSAGE_TEMPLATE;
        let template = Template::new(template_content);

        // Use Commit drain defaults
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Commit);
        let cap_vars = capability_template_variables(&caps, &flags);

        // Base variables for commit prompt
        let base_vars: HashMap<&str, String> = HashMap::from([
            ("DIFF", "test diff".to_string()),
            ("COMMIT_MESSAGE_XML_PATH", "/test/commit.xml".to_string()),
            ("COMMIT_MESSAGE_XSD_PATH", "/test/commit.xsd".to_string()),
        ]);

        // Merge variables
        let variables: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(cap_vars)
            .collect();

        let variables_ref: HashMap<&str, String> = variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();

        let result = template
            .render_with_partials(&variables_ref, &partials)
            .expect("template rendering should succeed");

        // Verify capability summary is present
        assert!(
            result.contains("SESSION CAPABILITIES"),
            "Commit prompt should include capability summary"
        );

        // Verify git restrictions are NOT present when GitWrite is granted
        // The conditional is {% if HAS_GIT_WRITE %}{% else %}{{> shared/_no_git_commit}}{% endif %}
        // Since Commit has HAS_GIT_WRITE="true", _no_git_commit should NOT be included
        assert!(
            !result.contains("Do NOT run ANY git command except read-only lookup commands")
                || !result.contains("read-only lookup"),
            "Commit prompt should NOT include _no_git_commit restrictions when GitWrite is granted"
        );

        // Verify commit message generation instructions are present
        assert!(
            result.contains("Conventional Commits"),
            "Commit prompt should include commit message guidance"
        );
    }

    /// Test that Fix drain prompt includes capability summary and correct restrictions.
    #[test]
    fn test_fix_prompt_capability_driven() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::FIX_MODE_TEMPLATE;
        let template = Template::new(template_content);

        // Use Fix drain defaults
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Fix);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Fix);
        let cap_vars = capability_template_variables(&caps, &flags);

        // Base variables for fix prompt
        let base_vars: HashMap<&str, String> = HashMap::from([
            ("PROMPT", "test prompt".to_string()),
            ("PLAN", "test plan".to_string()),
            ("ISSUES", "test issues".to_string()),
            ("FILES_TO_MODIFY", "src/main.rs".to_string()),
            ("FIX_RESULT_XML_PATH", "/test/fix.xml".to_string()),
            ("FIX_RESULT_XSD_PATH", "/test/fix.xsd".to_string()),
        ]);

        // Merge variables
        let variables: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(cap_vars)
            .collect();

        let variables_ref: HashMap<&str, String> = variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();

        let result = template
            .render_with_partials(&variables_ref, &partials)
            .expect("template rendering should succeed");

        // Verify capability summary is present
        assert!(
            result.contains("SESSION CAPABILITIES"),
            "Fix prompt should include capability summary"
        );

        // Verify git restrictions are present (Fix has no GitWrite by default)
        assert!(
            result.contains("Do NOT run ANY git command except read-only lookup commands"),
            "Fix prompt should include git restrictions"
        );

        // Verify fix mode constraints
        assert!(
            result.contains("FIX MODE"),
            "Fix prompt should be in fix mode"
        );
    }

    /// Test that Analysis drain prompt includes capability summary and correct restrictions.
    #[test]
    fn test_analysis_prompt_capability_driven() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::ANALYSIS_SYSTEM_PROMPT_TEMPLATE;
        let template = Template::new(template_content);

        // Use Analysis drain defaults
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Analysis);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Analysis);
        let cap_vars = capability_template_variables(&caps, &flags);

        // Base variables for analysis prompt
        let base_vars: HashMap<&str, String> = HashMap::from([
            ("PLAN", "test plan".to_string()),
            ("DIFF", "test diff".to_string()),
            (
                "DEVELOPMENT_RESULT_XML_PATH",
                "/test/result.xml".to_string(),
            ),
            (
                "DEVELOPMENT_RESULT_XSD_PATH",
                "/test/result.xsd".to_string(),
            ),
            (
                "REQUIRED_OUTPUT_XML",
                "<ralph-analysis>...</ralph-analysis>".to_string(),
            ),
        ]);

        // Merge variables
        let variables: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(cap_vars)
            .collect();

        let variables_ref: HashMap<&str, String> = variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();

        let result = template
            .render_with_partials(&variables_ref, &partials)
            .expect("template rendering should succeed");

        // Verify capability summary is present
        assert!(
            result.contains("SESSION CAPABILITIES"),
            "Analysis prompt should include capability summary"
        );

        // Verify git restrictions are present (Analysis has no GitWrite by default)
        assert!(
            result.contains("Do NOT run ANY git command except read-only lookup commands"),
            "Analysis prompt should include git restrictions"
        );

        // Verify analysis mode constraints
        assert!(
            result.contains("VERIFICATION agent"),
            "Analysis prompt should be for verification"
        );
    }

    /// Test that prompt generation is deterministic (same inputs produce same output).
    #[test]
    fn test_prompt_generation_is_deterministic() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::DEVELOPER_ITERATION_TEMPLATE;
        let template = Template::new(template_content);

        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Development);
        let cap_vars = capability_template_variables(&caps, &flags);

        let base_vars: HashMap<&str, String> = HashMap::from([
            ("PROMPT", "test prompt".to_string()),
            ("PLAN", "test plan".to_string()),
        ]);

        let variables: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(cap_vars)
            .collect();

        let variables_ref: HashMap<&str, String> = variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();

        let result1 = template
            .render_with_partials(&variables_ref, &partials)
            .expect("first render should succeed");
        let result2 = template
            .render_with_partials(&variables_ref, &partials)
            .expect("second render should succeed");

        assert_eq!(
            result1, result2,
            "Prompt generation should be deterministic"
        );
    }

    /// Test that prompts differ based on GitWrite capability by comparing drains.
    #[test]
    fn test_capabilities_affect_template_output() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::DEVELOPER_ITERATION_TEMPLATE;
        let template = Template::new(template_content);

        // Development drain (no GitWrite)
        let dev_caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
        let dev_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Development);
        let dev_vars = capability_template_variables(&dev_caps, &dev_flags);

        // Commit drain (has GitWrite)
        let commit_caps = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let commit_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Commit);
        let commit_vars = capability_template_variables(&commit_caps, &commit_flags);

        let base_vars: HashMap<&str, String> = HashMap::from([
            ("PROMPT", "test prompt".to_string()),
            ("PLAN", "test plan".to_string()),
        ]);

        // Render with Development capabilities (no GitWrite)
        let variables_dev: HashMap<String, String> = base_vars
            .clone()
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(dev_vars)
            .collect();
        let variables_ref_dev: HashMap<&str, String> = variables_dev
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();
        let result_dev = template
            .render_with_partials(&variables_ref_dev, &partials)
            .expect("dev render should succeed");

        // Render with Commit capabilities (has GitWrite)
        let variables_commit: HashMap<String, String> = base_vars
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .chain(commit_vars)
            .collect();
        let variables_ref_commit: HashMap<&str, String> = variables_commit
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();
        let result_commit = template
            .render_with_partials(&variables_ref_commit, &partials)
            .expect("commit render should succeed");

        // Verify capabilities are different
        let dev_has_git_write = dev_caps.contains(Capability::GitWrite);
        let commit_has_git_write = commit_caps.contains(Capability::GitWrite);

        assert!(
            !dev_has_git_write && commit_has_git_write,
            "Development should NOT have GitWrite, Commit should have GitWrite"
        );

        // The prompts should differ because they have different HAS_GIT_WRITE values
        // Development has HAS_GIT_WRITE="" which includes _no_git_commit
        // Commit has HAS_GIT_WRITE="true" which skips _no_git_commit
        //
        // This test verifies that the capability system correctly affects template output
        assert_ne!(
            result_dev, result_commit,
            "Prompts with different capabilities should differ"
        );
    }

    /// Test that empty capability set produces appropriate prompt.
    #[test]
    fn test_empty_capabilities_produces_empty_summary() {
        let caps = CapabilitySet::new();
        let flags = PolicyFlagSet::new();
        let vars = capability_template_variables(&caps, &flags);

        let summary = vars.get("CAPABILITY_SUMMARY").expect("should have summary");
        assert!(
            summary.contains("(none)"),
            "Empty capabilities should show (none) in summary"
        );
    }

    /// Test that all drain types produce expected capability summaries.
    #[test]
    fn test_drain_capabilities_summaries() {
        // Planning: read-only
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Planning);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Planning);
        let vars = capability_template_variables(&caps, &flags);
        let summary = vars.get("CAPABILITY_SUMMARY").unwrap();
        assert!(
            summary.contains("workspace.read"),
            "Planning should have workspace.read"
        );
        assert!(
            !summary.contains("workspace.write_tracked"),
            "Planning should not have workspace.write_tracked (but may have workspace.write_ephemeral)"
        );

        // Development: read + write
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
        let vars = capability_template_variables(&caps, &flags);
        let summary = vars.get("CAPABILITY_SUMMARY").unwrap();
        assert!(
            summary.contains("workspace.write"),
            "Development should have workspace.write"
        );

        // Commit: git write
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let vars = capability_template_variables(&caps, &flags);
        let summary = vars.get("CAPABILITY_SUMMARY").unwrap();
        assert!(
            summary.contains("git.write"),
            "Commit should have git.write"
        );

        // Review and Analysis have same read-only capabilities
        let review_caps = CapabilitySet::defaults_for_drain(SessionDrain::Review);
        let review_vars = capability_template_variables(
            &review_caps,
            &PolicyFlagSet::defaults_for_drain(SessionDrain::Review),
        );
        let analysis_caps = CapabilitySet::defaults_for_drain(SessionDrain::Analysis);
        let analysis_vars = capability_template_variables(
            &analysis_caps,
            &PolicyFlagSet::defaults_for_drain(SessionDrain::Analysis),
        );
        assert_eq!(
            review_vars.get("CAPABILITY_SUMMARY").unwrap(),
            analysis_vars.get("CAPABILITY_SUMMARY").unwrap(),
            "Review and Analysis should have identical capability summaries"
        );
    }

    /// Test that HAS_GIT_WRITE is correctly set for each drain.
    #[test]
    fn test_has_git_write_flag_per_drain() {
        // Commit has GitWrite capability
        let drain = SessionDrain::Commit;
        let caps = CapabilitySet::defaults_for_drain(drain);
        let flags = PolicyFlagSet::defaults_for_drain(drain);
        let vars = capability_template_variables(&caps, &flags);
        assert_eq!(
            vars.get("HAS_GIT_WRITE").unwrap(),
            "true",
            "{:?} should have HAS_GIT_WRITE=true",
            drain
        );

        // Drains without GitWrite capability
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Development,
            SessionDrain::Review,
            SessionDrain::Fix,
            SessionDrain::Analysis,
        ] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("HAS_GIT_WRITE").unwrap(),
                "",
                "{:?} should have HAS_GIT_WRITE=\"\" (empty)",
                drain
            );
        }
    }

    /// Test that POLICY_NO_EDIT is correctly set for each drain.
    #[test]
    fn test_policy_no_edit_flag_per_drain() {
        // Drains with NoEdit policy
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Review,
            SessionDrain::Analysis,
        ] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("POLICY_NO_EDIT").unwrap(),
                "true",
                "{:?} should have POLICY_NO_EDIT=true",
                drain
            );
        }

        // Drains without NoEdit policy
        for drain in [
            SessionDrain::Development,
            SessionDrain::Fix,
            SessionDrain::Commit,
        ] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("POLICY_NO_EDIT").unwrap(),
                "",
                "{:?} should have POLICY_NO_EDIT=\"\" (empty)",
                drain
            );
        }
    }

    /// Test that session-derived capabilities produce identical template variables to drain-defaults.
    ///
    /// This is the core behavioral equivalence test for RFC-009: for every drain type,
    /// `capability_template_variables_from_session(session)` must produce identical output
    /// to `capability_template_variables(defaults_for_drain(drain))`.
    #[test]
    fn test_session_derived_equals_drain_default_for_all_drains() {
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Development,
            SessionDrain::Analysis,
            SessionDrain::Review,
            SessionDrain::Fix,
            SessionDrain::Commit,
        ] {
            // Create session with this drain
            let session = AgentSession::for_drain("equiv-test".to_string(), drain, 0);

            // Get variables via session wrapper
            let from_session = capability_template_variables_from_session(&session);

            // Get variables via direct drain-default call
            let from_drain_default =
                capability_template_variables(session.capabilities(), session.policy_flags());

            assert_eq!(
                from_session, from_drain_default,
                "Session wrapper should produce identical variables to direct drain-default call for {:?}",
                drain
            );
        }
    }

    /// Test that session-derived and drain-default prompt rendering produces identical output.
    ///
    /// This test renders the same template with capability variables derived from both
    /// the session path and the direct drain-default path, asserting byte-identical output.
    #[test]
    fn test_prompt_render_equivalence_session_vs_drain_default() {
        let partials = get_shared_partials();
        let template_content = ralph_workflow_policy::DEVELOPER_ITERATION_TEMPLATE;
        let template = Template::new(template_content);

        for drain in [
            SessionDrain::Planning,
            SessionDrain::Development,
            SessionDrain::Analysis,
            SessionDrain::Review,
            SessionDrain::Fix,
            SessionDrain::Commit,
        ] {
            // Create session with this drain
            let session = AgentSession::for_drain("equiv-test".to_string(), drain, 0);

            // Get variables via session wrapper
            let session_vars = capability_template_variables_from_session(&session);

            // Get variables via direct drain-default call
            let drain_vars =
                capability_template_variables(session.capabilities(), session.policy_flags());

            // Base variables for developer iteration prompt
            let base_vars: HashMap<&str, String> = HashMap::from([
                ("PROMPT", "test prompt".to_string()),
                ("PLAN", "test plan".to_string()),
            ]);

            // Build variables with session-derived capability vars
            let variables_session: HashMap<String, String> = base_vars
                .clone()
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .chain(session_vars)
                .collect();
            let variables_ref_session: HashMap<&str, String> = variables_session
                .iter()
                .map(|(k, v)| (k.as_str(), v.clone()))
                .collect();

            // Build variables with drain-default capability vars
            let variables_drain: HashMap<String, String> = base_vars
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .chain(drain_vars)
                .collect();
            let variables_ref_drain: HashMap<&str, String> = variables_drain
                .iter()
                .map(|(k, v)| (k.as_str(), v.clone()))
                .collect();

            // Render with both variable sets
            let result_session = template
                .render_with_partials(&variables_ref_session, &partials)
                .expect("session render should succeed");
            let result_drain = template
                .render_with_partials(&variables_ref_drain, &partials)
                .expect("drain render should succeed");

            assert_eq!(
                result_session, result_drain,
                "Session-derived and drain-default rendering should be identical for {:?}",
                drain
            );
        }
    }

    /// Test that AgentSession::for_drain produces capabilities identical to CapabilitySet::defaults_for_drain.
    ///
    /// This invariant is the foundation of RFC-009 behavioral equivalence guarantees.
    #[test]
    fn test_agent_session_capabilities_match_drain_defaults() {
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Development,
            SessionDrain::Analysis,
            SessionDrain::Review,
            SessionDrain::Fix,
            SessionDrain::Commit,
        ] {
            let session = AgentSession::for_drain("equiv-test".to_string(), drain, 0);
            let defaults = CapabilitySet::defaults_for_drain(drain);

            assert_eq!(
                session.capabilities, defaults,
                "AgentSession capabilities should match drain defaults for {:?}",
                drain
            );
        }
    }

    /// Test that AgentSession::for_drain produces policy flags identical to PolicyFlagSet::defaults_for_drain.
    ///
    /// This invariant is the foundation of RFC-009 behavioral equivalence guarantees.
    #[test]
    fn test_agent_session_policy_flags_match_drain_defaults() {
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Development,
            SessionDrain::Analysis,
            SessionDrain::Review,
            SessionDrain::Fix,
            SessionDrain::Commit,
        ] {
            let session = AgentSession::for_drain("equiv-test".to_string(), drain, 0);
            let defaults = PolicyFlagSet::defaults_for_drain(drain);

            assert_eq!(
                session.policy_flags, defaults,
                "AgentSession policy_flags should match drain defaults for {:?}",
                drain
            );
        }
    }
}
