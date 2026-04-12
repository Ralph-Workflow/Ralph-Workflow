//! Development result XML renderer.
//!
//! Renders development result XML with semantic formatting:
//! - Header with box-drawing characters
//! - Status with emoji indicator and label
//! - Summary description with proper indentation
//! - Files changed with action type indicators
//! - Next steps if present

use super::helpers::{
    parse_files_changed_list, parse_unified_diff_files, render_diff_sections, ChangeAction,
};
use crate::files::llm_output_extraction::validate_development_result_xml;
use crate::reducer::ui_event::XmlOutputContext;
use crate::rendering::xml::SkillsMcp;

/// Render development result XML with semantic formatting.
pub(super) fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let header = output_context
        .and_then(|ctx| ctx.iteration)
        .map(|iter| format!("\n╔═══ Development Iteration {iter} ═══╗\n"))
        .unwrap_or_default();

    if let Ok(elements) = validate_development_result_xml(content) {
        let (status_emoji, status_label) = match elements.status.as_str() {
            "completed" => ("✅", "Completed"),
            "partial" => ("🔄", "In Progress"),
            "failed" => ("❌", "Failed"),
            _ => ("❓", "Unknown"),
        };

        let summary_part = {
            let summary_lines: String = elements
                .summary
                .lines()
                .map(|line| format!("   {line}\n"))
                .collect();
            format!("📋 Summary:\n{}", summary_lines)
        };

        let skills_mcp_part = elements.skills_mcp.as_ref().map_or(String::new(), |sm| {
            let output = render_skills_mcp_to_string(sm);
            if output.is_empty() {
                String::new()
            } else {
                format!("\n🛠️  Skills & MCP Recommendations:\n{output}")
            }
        });

        let files_part = elements
            .files_changed
            .as_ref()
            .map(|files| render_files_changed_as_diff_like_view(files))
            .unwrap_or_default();

        let next_steps_part = elements.next_steps.as_ref().map_or(String::new(), |next| {
            let next_lines: String = next.lines().map(|line| format!("   {line}\n")).collect();
            format!("\n➡️  Next Steps:\n{next_lines}")
        });

        format!(
            "{header}{status_emoji} Status: {status_label}\n\n{summary_part}{skills_mcp_part}{files_part}{next_steps_part}"
        )
    } else {
        format!("{header}⚠️  Unable to parse development result XML\n\n{content}")
    }
}

fn render_skills_mcp_to_string(sm: &SkillsMcp) -> String {
    let has_structured = !sm.skills.is_empty() || !sm.mcps.is_empty();
    if has_structured || sm.raw_content.is_some() {
        let skill_lines: String = sm
            .skills
            .iter()
            .map(|skill| {
                skill
                    .reason
                    .as_ref()
                    .map(|r| format!("   - skill: {} \u{2014} {}\n", skill.name, r))
                    .unwrap_or_else(|| format!("   - skill: {}\n", skill.name))
            })
            .collect();

        let mcp_lines: String = sm
            .mcps
            .iter()
            .map(|mcp| {
                mcp.reason
                    .as_ref()
                    .map(|r| format!("   - mcp: {} \u{2014} {}\n", mcp.name, r))
                    .unwrap_or_else(|| format!("   - mcp: {}\n", mcp.name))
            })
            .collect();

        let raw_part = sm
            .raw_content
            .as_ref()
            .filter(|raw: &&String| {
                let trimmed: &str = raw.trim();
                !trimmed.is_empty() && !has_structured
            })
            .map(|raw: &String| format!("   {}\n", raw.trim()))
            .unwrap_or_default();

        format!("{}{}{}", skill_lines, mcp_lines, raw_part)
    } else {
        String::new()
    }
}

fn render_files_changed_as_diff_like_view(files_changed: &str) -> String {
    let trimmed = files_changed.trim();
    if trimmed.is_empty() {
        return String::new();
    }

    if trimmed.contains("diff --git ") {
        let sections = parse_unified_diff_files(trimmed);
        return render_diff_sections("📁 Files Changed", &sections);
    }

    let items = parse_files_changed_list(trimmed);
    if items.is_empty() {
        return String::new();
    }

    let file_list: Vec<&str> = items.iter().map(|(p, _)| p.as_str()).collect();

    let files_output: String = items
        .iter()
        .map(|(path, action)| {
            let action_str = match action {
                ChangeAction::Create => "created",
                ChangeAction::Modify => "modified",
                ChangeAction::Delete => "deleted",
            };
            format!(
                "\n   📄 {}\n      Action: {}\n      (no diff provided)\n",
                path, action_str
            )
        })
        .collect();

    format!(
        "\n📁 Files Changed:\n   Modified {} file(s): {}{}",
        file_list.len(),
        file_list.join(", "),
        files_output
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_development_result_completed() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented feature X</ralph-summary>
<ralph-files-changed>src/main.rs
src/lib.rs</ralph-files-changed>
</ralph-development-result>";

        let output = render(xml, None);

        assert!(output.contains("✅"), "Should have completed emoji");
        assert!(
            output.contains("Completed"),
            "Should show friendly status label"
        );
        assert!(
            output.contains("Implemented feature X"),
            "Should show summary"
        );
        assert!(output.contains("src/main.rs"), "Should list files");
    }

    #[test]
    fn test_render_development_result_renders_diff_like_view_per_file_when_diff_present() {
        let xml = r#"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Updated two files</ralph-summary>
<ralph-files-changed>diff --git a/src/main.rs b/src/main.rs
index 1111111..2222222 100644
--- a/src/main.rs
+++ b/src/main.rs
@@ -1,2 +1,2 @@
-fn main() { println!("old"); }
+fn main() { println!("new"); }
diff --git a/src/lib.rs b/src/lib.rs
new file mode 100644
--- /dev/null
+++ b/src/lib.rs
@@ -0,0 +1,1 @@
+pub fn hello() {}
</ralph-files-changed>
</ralph-development-result>"#;

        let output = render(xml, None);

        assert!(
            output.contains("Modified 2 file") || output.contains("2 file"),
            "Should include file count summary"
        );
        assert!(
            output.contains("src/main.rs") && output.contains("src/lib.rs"),
            "Should include per-file headers"
        );
        assert!(
            output.contains("--- a/src/main.rs") && output.contains("+++ b/src/main.rs"),
            "Should include diff markers"
        );
        assert!(
            output.contains("+pub fn hello") || output.contains("pub fn hello"),
            "Should include diff content"
        );
    }

    #[test]
    fn test_render_development_result_partial() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Started work on feature</ralph-summary>
<ralph-next-steps>Continue with implementation</ralph-next-steps>
</ralph-development-result>";

        let output = render(xml, None);

        assert!(output.contains("🔄"), "Should have partial emoji");
        assert!(
            output.contains("Continue with implementation"),
            "Should show next steps"
        );
    }

    #[test]
    fn test_render_development_result_with_iteration() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>";

        let ctx = Some(XmlOutputContext {
            iteration: Some(2),
            pass: None,
            snippets: Vec::new(),
        });
        let output = render(xml, ctx.as_ref());

        assert!(
            output.contains("Development Iteration 2"),
            "Should show iteration number"
        );
    }

    #[test]
    fn test_render_development_result_malformed_fallback() {
        let bad_xml = "not valid xml at all";
        let output = render(bad_xml, None);

        assert!(output.contains("⚠️"), "Should show warning");
        assert!(
            output.contains("not valid xml"),
            "Should include raw content"
        );
    }

    #[test]
    fn test_development_result_multiline_summary() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>First line of summary
Second line of summary
Third line of summary</ralph-summary>
</ralph-development-result>";

        let output = render(xml, None);
        assert!(
            output.contains("First line"),
            "Should show first line of summary"
        );
        assert!(
            output.contains("Second line"),
            "Should show second line of summary"
        );
        assert!(
            output.contains("Third line"),
            "Should show third line of summary"
        );
    }

    #[test]
    fn test_development_result_file_action_icons() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Changes made</ralph-summary>
<ralph-files-changed>src/new_file.rs (created)
src/existing.rs
src/old.rs (deleted)</ralph-files-changed>
</ralph-development-result>";

        let output = render(xml, None);
        assert!(
            output.contains("src/new_file.rs") && output.contains("Action: created"),
            "Should show created action for new file"
        );
        assert!(
            output.contains("src/old.rs") && output.contains("Action: deleted"),
            "Should show deleted action for removed file"
        );
        assert!(
            output.contains("src/existing.rs") && output.contains("Action: modified"),
            "Should show modified action for existing file"
        );
    }

    #[test]
    fn test_render_development_result_with_skills_mcp() {
        let xml = "<ralph-development-result>\
<ralph-status>partial</ralph-status>\
<ralph-summary>Verification found a reproducible reducer failure.</ralph-summary>\
<skills-mcp>\
<skill reason=\"A concrete failure should be investigated before editing code\">systematic-debugging</skill>\
<skill reason=\"The eventual fix should begin with a reproducing test\">test-driven-development</skill>\
<mcp reason=\"Use when external dependency behavior needs confirmation\">context7</mcp>\
</skills-mcp>\
<ralph-files-changed>src/main.rs</ralph-files-changed>\
</ralph-development-result>";

        let output = render(xml, None);

        assert!(
            output.contains("Skills & MCP Recommendations"),
            "Should show skills-mcp header"
        );
        assert!(
            output.contains("systematic-debugging"),
            "Should show first skill name"
        );
        assert!(
            output.contains("test-driven-development"),
            "Should show second skill name"
        );
        assert!(output.contains("context7"), "Should show mcp name");
        assert!(
            output.contains("A concrete failure should be investigated"),
            "Should show reason for first skill"
        );
    }

    #[test]
    fn test_render_development_result_skills_mcp_raw_content_only() {
        let xml = "<ralph-development-result>\
<ralph-status>partial</ralph-status>\
<ralph-summary>Some work done</ralph-summary>\
<skills-mcp>\
some raw unstructured skills content here\
</skills-mcp>\
</ralph-development-result>";

        let output = render(xml, None);

        assert!(
            output.contains("Skills & MCP Recommendations"),
            "Should show skills-mcp header"
        );
        assert!(
            output.contains("some raw unstructured skills content here"),
            "Should show raw content"
        );
    }
}
