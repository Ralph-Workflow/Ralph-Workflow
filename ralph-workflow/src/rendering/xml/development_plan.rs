//! Development plan XML renderer.
//!
//! Renders development plan XML with semantic formatting:
//! - Box-drawing header
//! - Context description
//! - Scope items with counts and categories
//! - Implementation steps with priorities, file targets, rationale, and dependencies
//! - Risks and mitigations with severity
//! - Verification strategy

use std::fmt::Write;

use crate::files::llm_output_extraction::validate_plan_xml;
use crate::files::llm_output_extraction::xsd_validation_plan::{
    FileAction, Priority, Severity, SkillsMcp,
};

/// Render development plan XML with semantic formatting.
pub fn render(content: &str) -> String {
    let mut output = String::new();

    output.push_str("\n╔════════════════════════════════════╗\n");
    output.push_str("║      Implementation Plan           ║\n");
    output.push_str("╚════════════════════════════════════╝\n\n");

    if let Ok(elements) = validate_plan_xml(content) {
        // Context section
        output.push_str("📋 Context:\n");
        writeln!(output, "   {}\n", elements.summary.context).unwrap();

        // Scope section with categories
        output.push_str("📊 Scope:\n");
        for item in &elements.summary.scope_items {
            if let Some(ref count) = item.count {
                write!(output, "   • {} {}", count, item.description).unwrap();
            } else {
                write!(output, "   • {}", item.description).unwrap();
            }
            if let Some(ref category) = item.category {
                write!(output, " ({category})").unwrap();
            }
            output.push('\n');
        }

        // Skills & MCP recommendations (if present)
        render_skills_mcp(&mut output, elements.skills_mcp.as_ref());

        // Steps section with priorities and dependencies
        output.push_str("\n───────────────────────────────────\n");
        output.push_str("📝 Implementation Steps:\n\n");
        for step in &elements.steps {
            let priority_badge = step.priority.map_or(String::new(), |p| {
                format!(
                    " [{}]",
                    match p {
                        Priority::Critical => "🔴 critical",
                        Priority::High => "🟠 high",
                        Priority::Medium => "🟡 medium",
                        Priority::Low => "🟢 low",
                    }
                )
            });
            writeln!(
                output,
                "   {}. {}{}",
                step.number, step.title, priority_badge
            )
            .unwrap();

            for file in &step.target_files {
                let action_icon = match file.action {
                    FileAction::Create => "➕",
                    FileAction::Modify => "📝",
                    FileAction::Delete => "🗑️",
                };
                writeln!(output, "      {} {}", action_icon, file.path).unwrap();
            }

            if let Some(ref rationale) = step.rationale {
                writeln!(output, "      💡 {rationale}").unwrap();
            }

            if !step.depends_on.is_empty() {
                let deps: Vec<String> = step
                    .depends_on
                    .iter()
                    .map(|d| format!("Step {d}"))
                    .collect();
                writeln!(output, "      🔗 Depends on: {}", deps.join(", ")).unwrap();
            }
            output.push('\n');
        }

        // Risks section with severity
        if !elements.risks_mitigations.is_empty() {
            output.push_str("───────────────────────────────────\n");
            output.push_str("⚠️  Risks & Mitigations:\n\n");
            for risk in &elements.risks_mitigations {
                let severity_icon = risk.severity.map_or("", |s| match s {
                    Severity::Critical => "🔴",
                    Severity::High => "🟠",
                    Severity::Medium => "🟡",
                    Severity::Low => "🟢",
                });
                writeln!(output, "   {} Risk: {}", severity_icon, risk.risk).unwrap();
                writeln!(output, "     → Mitigation: {}\n", risk.mitigation).unwrap();
            }
        }

        // Verification section
        if !elements.verification_strategy.is_empty() {
            output.push_str("───────────────────────────────────\n");
            output.push_str("✓ Verification Strategy:\n\n");
            for (i, v) in elements.verification_strategy.iter().enumerate() {
                writeln!(output, "   {}. {}", i + 1, v.method).unwrap();
                writeln!(output, "      Expected: {}", v.expected_outcome).unwrap();
            }
        }
    } else {
        output.push_str("⚠️  Unable to parse plan XML\n\n");
        output.push_str(content);
    }

    output
}

/// Render skills-mcp recommendations to the output string.
fn render_skills_mcp(output: &mut String, skills_mcp: Option<&SkillsMcp>) {
    if let Some(sm) = skills_mcp {
        let has_structured = !sm.skills.is_empty() || !sm.mcps.is_empty();
        if has_structured || sm.raw_content.is_some() {
            output.push_str("\n🛠️  Skills & MCP Recommendations:\n");
            for skill in &sm.skills {
                if let Some(ref reason) = skill.reason {
                    writeln!(output, "   - skill: {} \u{2014} {}", skill.name, reason).unwrap();
                } else {
                    writeln!(output, "   - skill: {}", skill.name).unwrap();
                }
            }
            for mcp in &sm.mcps {
                if let Some(ref reason) = mcp.reason {
                    writeln!(output, "   - mcp: {} \u{2014} {}", mcp.name, reason).unwrap();
                } else {
                    writeln!(output, "   - mcp: {}", mcp.name).unwrap();
                }
            }
            if let Some(ref raw) = sm.raw_content {
                let trimmed: &str = raw.trim();
                if !trimmed.is_empty() && !has_structured {
                    writeln!(output, "   {trimmed}").unwrap();
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_plan_basic_structure() {
        // Use a minimal valid plan structure
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Adding a new feature to the codebase</context>
<scope-items>
<scope-item count="3">files to modify</scope-item>
<scope-item count="1">new file to create</scope-item>
<scope-item>documentation updates</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>Add new module</title>
<target-files>
<file path="src/new.rs" action="create"/>
</target-files>
<content>
<paragraph>Create the new module with basic structure.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/new.rs" action="create"/>
</primary-files>
<reference-files>
<file path="src/lib.rs" purpose="module registration"/>
</reference-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>May conflict with existing code</risk>
<mitigation>Review for conflicts</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>Run tests</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let output = render(xml);

        assert!(
            output.contains("Implementation Plan"),
            "Should have plan header"
        );
        assert!(output.contains("Context:"), "Should show context section");
        assert!(
            output.contains("Adding a new feature"),
            "Should show context text"
        );
        assert!(output.contains("Scope:"), "Should show scope section");
        assert!(
            output.contains("3 files to modify"),
            "Should show scope items"
        );
        assert!(
            output.contains("Implementation Steps"),
            "Should show steps section"
        );
        assert!(
            output.contains("1. Add new module"),
            "Should show step title"
        );
        assert!(
            output.contains("Risks & Mitigations"),
            "Should show risks section"
        );
    }

    #[test]
    fn test_render_plan_malformed_fallback() {
        let bad_xml = "<ralph-plan><incomplete>";
        let output = render(bad_xml);

        assert!(output.contains("⚠️"), "Should show warning");
        assert!(
            output.contains("<ralph-plan>"),
            "Should include raw content"
        );
    }

    #[test]
    fn test_render_plan_shows_step_priorities() {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test context</context>
<scope-items>
<scope-item count="1">item 1</scope-item>
<scope-item count="2">item 2</scope-item>
<scope-item count="3">item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" priority="critical" type="file-change">
<title>Critical step</title>
<target-files><file path="src/main.rs" action="modify"/></target-files>
<content><paragraph>Do something critical</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="src/main.rs" action="modify"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="high"><risk>Test risk</risk><mitigation>Test mitigation</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Run tests</method><expected-outcome>All pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let output = render(xml);
        assert!(output.contains("critical"), "Should show priority badge");
        assert!(output.contains("🔴"), "Should show critical icon");
    }

    #[test]
    fn test_render_plan_shows_step_dependencies() {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test context</context>
<scope-items>
<scope-item count="1">item 1</scope-item>
<scope-item count="2">item 2</scope-item>
<scope-item count="3">item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>First step</title>
<target-files><file path="src/a.rs" action="create"/></target-files>
<content><paragraph>Create file A</paragraph></content>
</step>
<step number="2" type="file-change">
<title>Second step</title>
<target-files><file path="src/b.rs" action="create"/></target-files>
<depends-on step="1"/>
<content><paragraph>Create file B</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="src/a.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>None</risk><mitigation>N/A</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Run tests</method><expected-outcome>Pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let output = render(xml);
        assert!(output.contains("Depends on"), "Should show dependencies");
        assert!(output.contains("Step 1"), "Should list dependent step");
    }

    #[test]
    fn test_render_plan_shows_verification_strategy() {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test context</context>
<scope-items>
<scope-item count="1">item 1</scope-item>
<scope-item count="2">item 2</scope-item>
<scope-item count="3">item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>Test step</title>
<target-files><file path="src/main.rs" action="modify"/></target-files>
<content><paragraph>Modify</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="src/main.rs" action="modify"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>None</risk><mitigation>N/A</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>cargo test</method><expected-outcome>All tests pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let output = render(xml);
        assert!(
            output.contains("Verification Strategy"),
            "Should show verification section"
        );
        assert!(output.contains("cargo test"), "Should show method");
        assert!(output.contains("Expected"), "Should show expected outcome");
    }

    #[test]
    fn test_render_plan_file_action_icons() {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item count="1">create</scope-item>
<scope-item count="1">modify</scope-item>
<scope-item count="1">delete</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>Create file</title>
<target-files><file path="src/new.rs" action="create"/></target-files>
<content><paragraph>Create</paragraph></content>
</step>
<step number="2" type="file-change">
<title>Modify file</title>
<target-files><file path="src/existing.rs" action="modify"/></target-files>
<content><paragraph>Modify</paragraph></content>
</step>
<step number="3" type="file-change">
<title>Delete file</title>
<target-files><file path="src/old.rs" action="delete"/></target-files>
<content><paragraph>Delete</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="src/new.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>None</risk><mitigation>N/A</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Test</method><expected-outcome>Pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let output = render(xml);
        assert!(output.contains("➕"), "Should show create icon");
        assert!(output.contains("📝"), "Should show modify icon");
        assert!(output.contains("🗑️"), "Should show delete icon");
    }

    #[test]
    fn test_render_plan_with_skills_mcp() {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Adding a new feature</context>
<scope-items>
<scope-item count="1">file to modify</scope-item>
<scope-item count="1">file to create</scope-item>
<scope-item count="1">test to add</scope-item>
</scope-items>
</ralph-summary>
<skills-mcp>
<skill reason="Start with failing tests">test-driven-development</skill>
<skill reason="UI work requires visual design">frontend-design</skill>
<mcp reason="Use for Angular documentation">angular-mcp</mcp>
</skills-mcp>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>Implement feature</title>
<target-files><file path="src/main.rs" action="modify"/></target-files>
<content><paragraph>Do the work</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="src/main.rs" action="modify"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>None</risk><mitigation>N/A</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Test</method><expected-outcome>Pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let output = render(xml);
        assert!(
            output.contains("Skills & MCP Recommendations"),
            "Should show skills-mcp section"
        );
        assert!(
            output.contains("test-driven-development"),
            "Should show skill name"
        );
        assert!(
            output.contains("frontend-design"),
            "Should show second skill"
        );
        assert!(output.contains("angular-mcp"), "Should show mcp name");
        assert!(
            output.contains("Start with failing tests"),
            "Should show skill reason"
        );
        assert!(
            output.contains("Use for Angular documentation"),
            "Should show mcp reason"
        );
    }

    #[test]
    fn test_render_plan_skills_mcp_raw_content_only() {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item1</scope-item>
<scope-item>item2</scope-item>
<scope-item>item3</scope-item>
</scope-items>
</ralph-summary>
<skills-mcp>
some raw unstructured content
</skills-mcp>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>Step</title>
<target-files><file path="src/main.rs" action="modify"/></target-files>
<content><paragraph>Do</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="src/main.rs" action="modify"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>None</risk><mitigation>N/A</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Test</method><expected-outcome>Pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let output = render(xml);
        assert!(
            output.contains("Skills & MCP Recommendations"),
            "Should show skills-mcp section"
        );
        assert!(
            output.contains("some raw unstructured content"),
            "Should show raw content"
        );
    }
}
