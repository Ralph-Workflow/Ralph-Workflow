//! Shared helpers for XML renderers.
//!
//! This module contains utilities used by multiple XML renderer modules.

/// Extract text content from an XML tag.
///
/// Simple extraction for well-formed tags. Returns None if tag not found.
pub(super) fn extract_tag_content(content: &str, tag_name: &str) -> Option<String> {
    let start_tag = format!("<{tag_name}>");
    let end_tag = format!("</{tag_name}>");

    let start_pos = content.find(&start_tag)?;
    let content_start = start_pos + start_tag.len();
    let end_pos = content[content_start..].find(&end_tag)?;

    Some(content[content_start..content_start + end_pos].to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_tag_content_found() {
        let xml = "<ralph-subject>Hello World</ralph-subject>";
        let result = extract_tag_content(xml, "ralph-subject");
        assert_eq!(result, Some("Hello World".to_string()));
    }

    #[test]
    fn test_extract_tag_content_not_found() {
        let xml = "<other>content</other>";
        let result = extract_tag_content(xml, "ralph-subject");
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_tag_content_nested() {
        let xml = "<outer><ralph-subject>Nested</ralph-subject></outer>";
        let result = extract_tag_content(xml, "ralph-subject");
        assert_eq!(result, Some("Nested".to_string()));
    }
}
