//! XML formatter for displaying XML content to users.
//!
//! This module provides pretty-printing of XML content for display purposes.
//! When AI agents return XML, we want to display it in a nice, readable format
//! rather than showing raw XML.
//!
//! # Note on Semantic Rendering
//!
//! For user-facing output, prefer using `UIEvent::XmlOutput` which routes
//! through the semantic renderers in `rendering::xml`. Those renderers
//! provide user-friendly output (status emojis, structured layout) rather
//! than raw pretty-printed XML.
//!
//! This formatter is kept for:
//! - Debugging/logging where raw XML structure is needed
//! - Fallback rendering when semantic parsing fails
//! - Tests that verify XML structure

/// Format XML content for nice display (pretty-printed XML with indentation).
///
/// This function prettifies XML by adding proper indentation and line breaks.
/// If the XML parsing fails or the content isn't XML, it returns the original content.
///
/// # Prefer Semantic Rendering
///
/// For user-facing output, consider using the semantic renderers via
/// `UIEvent::XmlOutput` instead. They provide user-friendly formatting
/// (emojis, structured layout) rather than raw XML.
///
/// # Arguments
///
/// * `xml_content` - The XML content to format
///
/// # Returns
///
/// A formatted string with proper indentation for display.
#[must_use]
pub fn format_xml_for_display(xml_content: &str) -> String {
    // Check if content looks like XML (has tags)
    if !xml_content.contains('<') {
        return xml_content.to_string();
    }

    // Try to parse and pretty-print the XML
    let pretty = pretty_print_xml(xml_content);
    if pretty.is_empty() {
        xml_content.to_string()
    } else {
        pretty
    }
}

/// Pretty-print XML with proper indentation.
///
/// This is a simple XML pretty-printer that adds indentation
/// based on tag nesting level.
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
fn pretty_print_xml(xml_content: &str) -> String {
    #[derive(Copy, Clone)]
    enum XmlMode {
        Outside,
        Tag { start: usize },
        Content,
    }

    let mut result = String::new();
    let mut indent: usize = 0;
    let chars: Vec<char> = xml_content.chars().collect();
    let mut i = 0;
    let mut mode = XmlMode::Outside;

    while i < chars.len() {
        let c = chars[i];

        match c {
            '<' => {
                let next_char = chars.get(i + 1).copied();
                let is_closing_tag = matches!(next_char, Some('/'));

                if is_closing_tag {
                    if matches!(mode, XmlMode::Content) && indent > 0 {
                        result.push('\n');
                    }
                    indent = indent.saturating_sub(1);
                } else if matches!(mode, XmlMode::Content) {
                    result.push('\n');
                }

                mode = XmlMode::Tag { start: i };
            }
            '>' => {
                if let XmlMode::Tag { start } = mode {
                    let char_after_lt = chars.get(start + 1).copied().unwrap_or('\0');
                    let is_self_closing = i > 0 && chars[i - 1] == '/';
                    let is_declaration =
                        matches!(chars.get(start + 1), Some('?')) && i > 0 && chars[i - 1] == '?';

                    let skips_prefix = char_after_lt == '/' || char_after_lt == '?';
                    let tag_name_start = if skips_prefix { start + 2 } else { start + 1 };
                    let tag_name_end = i;
                    let tag_name: String = if tag_name_start < tag_name_end {
                        chars[tag_name_start..tag_name_end]
                            .iter()
                            .take_while(|&ch| !ch.is_whitespace() && *ch != '/')
                            .collect()
                    } else {
                        String::new()
                    };

                    let should_indent =
                        !is_self_closing && !is_declaration && !char_after_lt.is_whitespace();
                    if should_indent {
                        if !result.ends_with('\n') && !result.is_empty() {
                            result.push('\n');
                        }
                        for _ in 0..indent {
                            result.push_str("  ");
                        }
                    }

                    result.extend(chars[start..=i].iter().copied());

                    let should_increase_indent = !is_self_closing
                        && !is_declaration
                        && char_after_lt != '/'
                        && !tag_name.is_empty();
                    mode = if should_increase_indent {
                        indent = indent.saturating_add(1);
                        XmlMode::Content
                    } else {
                        XmlMode::Outside
                    };
                } else {
                    result.push(c);
                }
            }
            '\n' | '\r' | '\t' => {}
            ' ' => {
                if matches!(mode, XmlMode::Tag { .. }) {
                    result.push(c);
                } else if matches!(mode, XmlMode::Content) {
                    if let Some(last_char) = result.chars().last() {
                        if last_char != ' ' && last_char != '\n' {
                            result.push(c);
                        }
                    } else {
                        result.push(c);
                    }
                }
            }
            _ => {
                if matches!(mode, XmlMode::Tag { .. } | XmlMode::Content) {
                    result.push(c);
                }
            }
        }

        i = i.saturating_add(1);
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_simple_xml() {
        let xml = r"<ralph-plan><ralph-summary>Summary</ralph-summary></ralph-plan>";
        let formatted = format_xml_for_display(xml);
        assert!(formatted.contains("<ralph-plan>"));
        assert!(formatted.contains("<ralph-summary>"));
        assert!(formatted.contains("Summary"));
    }

    #[test]
    fn test_format_nested_xml() {
        let xml = r"<ralph-issues><ralph-issue>Issue 1</ralph-issue><ralph-issue>Issue 2</ralph-issue></ralph-issues>";
        let formatted = format_xml_for_display(xml);
        assert!(formatted.contains("<ralph-issues>"));
        assert!(formatted.contains("<ralph-issue>"));
    }

    #[test]
    fn test_format_with_attributes() {
        let xml = r"<ralph-fix-result><ralph-status>all_issues_addressed</ralph-status></ralph-fix-result>";
        let formatted = format_xml_for_display(xml);
        assert!(formatted.contains("<ralph-fix-result>"));
        assert!(formatted.contains("<ralph-status>"));
    }

    #[test]
    fn test_format_empty_xml() {
        let xml = "";
        let formatted = format_xml_for_display(xml);
        assert_eq!(formatted, "");
    }

    #[test]
    fn test_format_invalid_xml_returns_original() {
        let xml = "This is not XML at all";
        let formatted = format_xml_for_display(xml);
        assert_eq!(formatted, "This is not XML at all");
    }

    #[test]
    fn test_format_xml_with_declaration() {
        let xml = r#"<?xml version="1.0"?><ralph-plan><ralph-summary>Summary</ralph-summary></ralph-plan>"#;
        let formatted = format_xml_for_display(xml);
        assert!(formatted.contains("<?xml"));
        assert!(formatted.contains("<ralph-plan>"));
    }
}
