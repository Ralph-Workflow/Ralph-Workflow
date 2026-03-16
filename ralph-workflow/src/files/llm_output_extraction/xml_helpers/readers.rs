//! XML reading utilities for parsing and traversal.
//!
//! This module provides functions for reading XML content using `quick_xml`,
//! with proper handling of whitespace, CDATA sections, and entity escaping.
//!
//! ## Key Features
//!
//! - **Whitespace trimming**: Automatic trimming of text between XML elements
//! - **CDATA preservation**: Content inside `<![CDATA[...]]>` is preserved exactly
//! - **Entity unescaping**: Automatic conversion of `&lt;`, `&gt;`, `&amp;` etc.
//! - **Depth tracking**: Proper handling of nested elements with the same tag name
//!
//! ## Usage Pattern
//!
//! See the unit tests in this module for working examples of XML reading utilities.
//!
//! ## CDATA Handling
//!
//! Code blocks with special XML characters (`<`, `>`, `&`) should use CDATA sections:
//!
//! ```xml
//! <code-block language="rust"><![CDATA[
//! if a < b && c > d {
//!     println!("hello");
//! }
//! ]]></code-block>
//! ```
//!
//! The reader preserves CDATA content exactly as written, without entity escaping.

use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use crate::files::llm_output_extraction::xsd_validation_plan::{McpEntry, SkillEntry, SkillsMcp};
use quick_xml::events::Event;
use quick_xml::Reader;
use std::borrow::Cow;

/// Create a configured `quick_xml` reader with whitespace trimming enabled.
///
/// The reader is configured with `trim_text(true)` which automatically
/// handles whitespace between XML elements - solving the spacing issues
/// that caused validation failures with manual string parsing.
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn create_reader(content: &str) -> Reader<&[u8]> {
    let mut reader = Reader::from_str(content);
    reader.config_mut().trim_text(true);
    reader
}

/// Read text content until the closing tag, trimming whitespace.
///
/// This handles XML text nodes properly, including:
/// - Entity unescaping (e.g., `&lt;` -> `<`, `&amp;` -> `&`)
/// - CDATA sections (content preserved exactly)
/// - Nested elements (skipped)
///
/// # Arguments
///
/// * `reader` - The `quick_xml` reader positioned after the opening tag
/// * `end_tag` - The closing tag name to read until (e.g., `b"root"`)
///
/// # Returns
///
/// The trimmed text content, or an error if the closing tag is not found.
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn read_text_until_end(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
) -> Result<String, XsdValidationError> {
    let mut buf = Vec::new();
    let mut text = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Text(e)) => {
                text.push_str(&e.unescape().unwrap_or_default());
            }
            Ok(Event::CData(e)) => {
                // CDATA content is preserved exactly as-is
                text.push_str(&String::from_utf8_lossy(&e));
            }
            Ok(Event::End(e)) if e.name().as_ref() == end_tag => {
                break;
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(end_tag).to_string(),
                    expected: format!("closing </{}>", String::from_utf8_lossy(end_tag)),
                    found: "unexpected end of file".to_string(),
                    suggestion: format!(
                        "Ensure the <{}> element has a matching closing tag.",
                        String::from_utf8_lossy(end_tag)
                    ),
                    example: None,
                });
            }
            Ok(_) => {} // Skip comments, processing instructions, nested elements
            Err(e) => {
                return Err(make_parse_error(end_tag, &e));
            }
        }
        buf.clear();
    }

    Ok(text.trim().to_string())
}

/// Read text content until the closing tag, accepting either canonical OR original tag name.
///
/// This function is used when fuzzy tag matching resolves a misspelled tag to its canonical form.
/// For example, if `<ralph-sumary>` is matched to `ralph-summary`, this function will accept
/// either `</ralph-summary>` OR `</ralph-sumary>` as the closing tag.
///
/// # Arguments
///
/// * `reader` - The XML reader
/// * `canonical_end_tag` - The expected canonical closing tag (e.g., `b"ralph-summary"`)
/// * `original_start_tag` - The original tag that appeared in the XML (e.g., `b"ralph-sumary"`)
pub fn read_text_until_end_fuzzy(
    reader: &mut Reader<&[u8]>,
    canonical_end_tag: &[u8],
    original_start_tag: &[u8],
) -> Result<String, XsdValidationError> {
    let mut buf = Vec::new();
    let mut text = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Text(e)) => {
                text.push_str(&e.unescape().unwrap_or_default());
            }
            Ok(Event::CData(e)) => {
                // CDATA content is preserved exactly as-is
                text.push_str(&String::from_utf8_lossy(&e));
            }
            Ok(Event::End(e)) => {
                // Accept either canonical tag name OR original (misspelled) tag name
                if e.name().as_ref() == canonical_end_tag || e.name().as_ref() == original_start_tag
                {
                    break;
                }
                // For nested elements with same name, track depth
                // (not needed for simple flat structures, but kept for correctness)
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(canonical_end_tag).to_string(),
                    expected: format!(
                        "closing </{}> or </{}>",
                        String::from_utf8_lossy(canonical_end_tag),
                        String::from_utf8_lossy(original_start_tag)
                    ),
                    found: "unexpected end of file".to_string(),
                    suggestion: format!(
                        "Ensure the element has a matching closing tag (</{}> or </{}>).",
                        String::from_utf8_lossy(canonical_end_tag),
                        String::from_utf8_lossy(original_start_tag)
                    ),
                    example: None,
                });
            }
            Ok(_) => {} // Skip comments, processing instructions, nested elements
            Err(e) => {
                return Err(make_parse_error(canonical_end_tag, &e));
            }
        }
        buf.clear();
    }

    Ok(text.trim().to_string())
}

/// Skip all content until the closing tag of the current element.
///
/// This properly handles nested elements with the same tag name by tracking depth.
///
/// # Arguments
///
/// * `reader` - The `quick_xml` reader positioned after the opening tag
/// * `end_tag` - The closing tag name to skip to (e.g., `b"element"`)
///
/// # Returns
///
/// `Ok(())` if successful, or an error if the closing tag is not found.
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn skip_to_end(reader: &mut Reader<&[u8]>, end_tag: &[u8]) -> Result<(), XsdValidationError> {
    let mut buf = Vec::new();
    let mut depth: usize = 1;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == end_tag => {
                depth = depth.saturating_add(1);
            }
            Ok(Event::End(e)) if e.name().as_ref() == end_tag => {
                depth = depth.saturating_sub(1);
                if depth == 0 {
                    break;
                }
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(end_tag).to_string(),
                    expected: format!("closing </{}>", String::from_utf8_lossy(end_tag)),
                    found: "unexpected end of file".to_string(),
                    suggestion: "Check that all XML elements are properly closed.".to_string(),
                    example: None,
                });
            }
            Ok(_) => {}
            Err(e) => {
                return Err(make_parse_error(end_tag, &e));
            }
        }
        buf.clear();
    }

    Ok(())
}

/// Parse a `<skills-mcp>` element's content into a `SkillsMcp` struct.
///
/// This function reads the content of a `<skills-mcp>` element, which may contain
/// `<skill>` and `<mcp>` child elements with optional `reason` attributes.
///
/// The parser is tolerant of malformed content:
/// - Unknown child elements are skipped
/// - Stray text between child elements is captured in `raw_content`
/// - Self-closing `<skill/>` and `<mcp/>` elements are skipped (no name)
/// - If XML parsing encounters an error inside the element, available data is returned
///
/// # Arguments
///
/// * `reader` - The `quick_xml` reader positioned immediately after the opening `<skills-mcp>` tag
///
/// # Returns
///
<<<<<<< Updated upstream
<<<<<<< Updated upstream
<<<<<<< Updated upstream
/// A `SkillsMcp` struct with parsed entries.
pub fn parse_skills_mcp(reader: &mut Reader<&[u8]>) -> SkillsMcp {
=======
/// A `SkillsMcp` struct with parsed entries. Never returns `Err` for content issues
/// inside the element (only for XML syntax errors that prevent reading).
pub fn parse_skills_mcp(reader: &mut Reader<&[u8]>) -> Result<SkillsMcp, XsdValidationError> {
>>>>>>> Stashed changes
=======
/// A `SkillsMcp` struct with parsed entries. Never returns `Err` for content issues
/// inside the element (only for XML syntax errors that prevent reading).
pub fn parse_skills_mcp(reader: &mut Reader<&[u8]>) -> Result<SkillsMcp, XsdValidationError> {
>>>>>>> Stashed changes
=======
/// A `SkillsMcp` struct with parsed entries. Never returns `Err` for content issues
/// inside the element (only for XML syntax errors that prevent reading).
pub fn parse_skills_mcp(reader: &mut Reader<&[u8]>) -> Result<SkillsMcp, XsdValidationError> {
>>>>>>> Stashed changes
    let mut buf = Vec::new();
    let mut skills: Vec<SkillEntry> = Vec::new();
    let mut mcps: Vec<McpEntry> = Vec::new();
    let mut raw_text_parts: Vec<String> = Vec::new();

    loop {
        buf.clear();
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                let tag = e.name();
                let tag_bytes = tag.as_ref();

                // Extract optional reason attribute
                let reason = e
                    .attributes()
<<<<<<< Updated upstream
<<<<<<< Updated upstream
<<<<<<< Updated upstream
                    .filter_map(std::result::Result::ok)
                    .find(|a| a.key.as_ref() == b"reason")
                    .and_then(|a| a.unescape_value().ok())
                    .map(Cow::into_owned)
=======
=======
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
                    .filter_map(|a| a.ok())
                    .find(|a| a.key.as_ref() == b"reason")
                    .and_then(|a| a.unescape_value().ok())
                    .map(|v| v.into_owned())
<<<<<<< Updated upstream
<<<<<<< Updated upstream
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
                    .filter(|s| !s.is_empty());

                match tag_bytes {
                    b"skill" => {
                        let name = read_text_until_end(reader, b"skill")
                            .unwrap_or_default()
                            .trim()
                            .to_string();
                        if !name.is_empty() {
                            skills.push(SkillEntry { name, reason });
                        }
                    }
                    b"mcp" => {
                        let name = read_text_until_end(reader, b"mcp")
                            .unwrap_or_default()
                            .trim()
                            .to_string();
                        if !name.is_empty() {
                            mcps.push(McpEntry { name, reason });
                        }
                    }
                    other => {
                        // Skip unknown elements tolerantly
                        let _ = skip_to_end(reader, other);
                    }
                }
            }
            Ok(Event::Empty(e)) => {
                // Self-closing elements like <skill/> or <mcp/> have no name text - skip
<<<<<<< Updated upstream
<<<<<<< Updated upstream
<<<<<<< Updated upstream
                let _tag_bytes = e.name().as_ref();
                // No content → nothing to record
=======
=======
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
                let tag = e.name();
                let tag_bytes = tag.as_ref();
                match tag_bytes {
                    b"skill" | b"mcp" => {
                        // No content → nothing to record
                    }
                    _ => {
                        // Unknown self-closing element - ignore
                    }
                }
<<<<<<< Updated upstream
<<<<<<< Updated upstream
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
            }
            Ok(Event::Text(e)) => {
                // Capture any stray text as raw content
                let text = e.unescape().unwrap_or_default().to_string();
                let trimmed = text.trim().to_string();
                if !trimmed.is_empty() {
                    raw_text_parts.push(trimmed);
                }
            }
            Ok(Event::CData(e)) => {
                let text = String::from_utf8_lossy(&e).trim().to_string();
                if !text.is_empty() {
                    raw_text_parts.push(text);
                }
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"skills-mcp" => break,
            Ok(Event::Eof) => {
                // Unexpected EOF - return what we have
                break;
            }
            Ok(_) => {} // Skip comments, PI, etc.
            Err(_) => {
                // Parse error inside skills-mcp - return what we have so far
                break;
            }
        }
    }

    let raw_content = if raw_text_parts.is_empty() {
        None
    } else {
        Some(raw_text_parts.join(" "))
    };

<<<<<<< Updated upstream
<<<<<<< Updated upstream
<<<<<<< Updated upstream
    SkillsMcp {
        skills,
        mcps,
        raw_content,
    }
=======
=======
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
    Ok(SkillsMcp {
        skills,
        mcps,
        raw_content,
    })
<<<<<<< Updated upstream
<<<<<<< Updated upstream
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
}

/// Create a parse error with CDATA suggestion if the element is code-related.
fn make_parse_error(element: &[u8], error: &quick_xml::Error) -> XsdValidationError {
    let element_name = String::from_utf8_lossy(element);
    let error_str = error.to_string();

    // Check if this is a code element - suggest CDATA
    let is_code_element = element_name.contains("code");
    let suggestion = if is_code_element {
        format!(
            "The <{element_name}> element contains characters that break XML parsing. \
             Use CDATA to wrap code content:\n\
             <{element_name}><![CDATA[\n  your code with <, >, & here\n]]></{element_name}>"
        )
    } else {
        "Check that all XML tags are properly formed and closed.".to_string()
    };

    XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: element_name.to_string(),
        expected: "valid XML content".to_string(),
        found: format!("parse error: {error_str}"),
        suggestion,
        example: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_reader() {
        // Just test that reader is created and can parse XML
        let mut reader = create_reader("<root>test</root>");
        let mut buf = Vec::new();
        // Reader should parse successfully
        let event = reader.read_event_into(&mut buf);
        assert!(event.is_ok());
    }

    #[test]
    fn test_read_text_until_end_simple() {
        let xml = "<root>hello world</root>";
        let mut reader = create_reader(xml);
        let mut buf = Vec::new();

        // Skip the opening tag
        loop {
            match reader.read_event_into(&mut buf) {
                Ok(Event::Start(e)) if e.name().as_ref() == b"root" => break,
                Ok(Event::Eof) => panic!("Unexpected EOF"),
                _ => {}
            }
            buf.clear();
        }

        let result = read_text_until_end(&mut reader, b"root").unwrap();
        assert_eq!(result, "hello world");
    }

    #[test]
    fn test_read_text_until_end_with_cdata() {
        let xml = "<code><![CDATA[a < b && c > d]]></code>";
        let mut reader = create_reader(xml);
        let mut buf = Vec::new();

        // Skip the opening tag
        loop {
            match reader.read_event_into(&mut buf) {
                Ok(Event::Start(e)) if e.name().as_ref() == b"code" => break,
                Ok(Event::Eof) => panic!("Unexpected EOF"),
                _ => {}
            }
            buf.clear();
        }

        let result = read_text_until_end(&mut reader, b"code").unwrap();
        // CDATA content should be preserved with actual < and > characters
        assert_eq!(result, "a < b && c > d");
    }

    #[test]
    fn test_read_text_until_end_with_entities() {
        let xml = "<code>a &lt; b &amp;&amp; c &gt; d</code>";
        let mut reader = create_reader(xml);
        let mut buf = Vec::new();

        // Skip the opening tag
        loop {
            match reader.read_event_into(&mut buf) {
                Ok(Event::Start(e)) if e.name().as_ref() == b"code" => break,
                Ok(Event::Eof) => panic!("Unexpected EOF"),
                _ => {}
            }
            buf.clear();
        }

        let result = read_text_until_end(&mut reader, b"code").unwrap();
        // Entities should be unescaped
        assert_eq!(result, "a < b && c > d");
    }

    #[test]
    fn test_make_parse_error_suggests_cdata_for_code_element() {
        let error = quick_xml::Error::Syntax(quick_xml::errors::SyntaxError::UnclosedTag);
        let result = make_parse_error(b"code-block", &error);
        // Should suggest CDATA for code-block element
        assert!(result.suggestion.contains("CDATA"));
        assert!(result.suggestion.contains("code-block"));
    }
}
