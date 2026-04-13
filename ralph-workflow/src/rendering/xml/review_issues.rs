//! Review issues renderer.
//!
//! Renders review issues JSON content with semantic formatting:
//! - Box-drawing header with pass number
//! - Issue count or approval celebration
//! - Each issue as a numbered item

use crate::reducer::ui_event::XmlOutputContext;

/// Render review issues JSON content with semantic formatting.
pub(super) fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let header = if let Some(ctx) = output_context {
        if let Some(pass) = ctx.pass {
            format!("\n╔═══ Review Pass {pass} ═══╗\n\n")
        } else {
            "\n╔═══ Review Results ═══╗\n\n".to_string()
        }
    } else {
        "\n╔═══ Review Results ═══╗\n\n".to_string()
    };

    if let Ok(json) = serde_json::from_str::<serde_json::Value>(content) {
        let type_str = json.get("type").and_then(|t| t.as_str()).unwrap_or("");
        if type_str == "no_issues_found" {
            let explanation = json
                .get("explanation")
                .and_then(|e| e.as_str())
                .unwrap_or("No issues found");
            return format!("{header}🎉 ✅ Code Approved!\n\n   {explanation}\n");
        }
        if let Some(issues) = json.get("issues").and_then(|i| i.as_array()) {
            if issues.is_empty() {
                return format!("{header}🎉 ✅ No issues found! Code looks good.\n");
            }
            let issues_text: String = issues
                .iter()
                .enumerate()
                .map(|(i, issue)| {
                    let text = issue
                        .as_str()
                        .or_else(|| issue.get("text").and_then(|t| t.as_str()))
                        .unwrap_or("");
                    format!("{}. {}\n", i + 1, text)
                })
                .collect();
            return format!(
                "{header}Found {} issue(s):\n\n{}",
                issues.len(),
                issues_text
            );
        }
    }

    format!("{header}{content}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_review_issues_no_issues_found() {
        let ctx = Some(XmlOutputContext {
            iteration: None,
            pass: Some(1),
            snippets: Vec::new(),
        });
        let output = render(
            r#"{"type":"no_issues_found","explanation":"All code looks good"}"#,
            ctx.as_ref(),
        );

        assert!(output.contains("Review Pass 1"), "Should show pass number");
        assert!(output.contains("✅"), "Should show approval indicator");
        assert!(
            output.contains("All code looks good"),
            "Should include explanation"
        );
    }

    #[test]
    fn test_render_review_issues_with_issues() {
        let output = render(
            r#"{"issues":["Fix the null check","Add error handling"]}"#,
            None,
        );

        assert!(output.contains("Review Results"), "Should show header");
        assert!(output.contains("2"), "Should show issue count");
        assert!(output.contains("Fix the null check"), "Should list first issue");
        assert!(
            output.contains("Add error handling"),
            "Should list second issue"
        );
    }

    #[test]
    fn test_render_review_issues_with_pass_header() {
        let ctx = Some(XmlOutputContext {
            iteration: None,
            pass: Some(1),
            snippets: Vec::new(),
        });
        let output = render(r#"{"issues":["Some issue"]}"#, ctx.as_ref());

        assert!(output.contains("Review Pass 1"), "Should show pass number");
        assert!(output.contains("Some issue"), "Should include content");
    }

    #[test]
    fn test_render_review_issues_no_context() {
        let output = render(r#"{"type":"no_issues_found","explanation":"All good"}"#, None);

        assert!(output.contains("Review Results"), "Should show review results header");
        assert!(output.contains("✅"), "Should show approval");
    }

    #[test]
    fn test_render_review_issues_fallback_for_non_json() {
        let output = render("raw content", None);

        assert!(output.contains("Review Results"), "Should show review results header");
        assert!(output.contains("raw content"), "Should include content");
    }
}
