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
pub(crate) fn create_reader(content: &str) -> Reader<&[u8]> {
    configure_trimmed_reader(Reader::from_str(content))
}

fn configure_trimmed_reader(mut reader: Reader<&[u8]>) -> Reader<&[u8]> {
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
pub(crate) fn read_text_until_end(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
) -> Result<String, XsdValidationError> {
    read_text_until_end_with_acc(reader, end_tag, String::new())
}

fn read_text_until_end_with_acc(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
    text: String,
) -> Result<String, XsdValidationError> {
    read_text_until_end_next(reader, end_tag, text, Vec::new())
}

fn read_text_until_end_next(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
    text: String,
    mut buf: Vec<u8>,
) -> Result<String, XsdValidationError> {
    match reader.read_event_into(&mut buf) {
        Ok(Event::Text(e)) => {
            let next_text = format!("{text}{}", e.unescape().unwrap_or_default());
            read_text_until_end_with_acc(reader, end_tag, next_text)
        }
        Ok(Event::CData(e)) => {
            let next_text = format!("{text}{}", String::from_utf8_lossy(&e));
            read_text_until_end_with_acc(reader, end_tag, next_text)
        }
        Ok(Event::End(e)) if e.name().as_ref() == end_tag => Ok(text.trim().to_string()),
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: String::from_utf8_lossy(end_tag).to_string(),
            expected: format!("closing </{}>", String::from_utf8_lossy(end_tag)),
            found: "unexpected end of file".to_string(),
            suggestion: format!(
                "Ensure the <{}> element has a matching closing tag.",
                String::from_utf8_lossy(end_tag)
            ),
            example: None,
        }),
        Ok(_) => read_text_until_end_with_acc(reader, end_tag, text),
        Err(e) => Err(make_parse_error(end_tag, &e)),
    }
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
pub(crate) fn read_text_until_end_fuzzy(
    reader: &mut Reader<&[u8]>,
    canonical_end_tag: &[u8],
    original_start_tag: &[u8],
) -> Result<String, XsdValidationError> {
    read_text_until_end_fuzzy_with_acc(reader, canonical_end_tag, original_start_tag, String::new())
}

fn read_text_until_end_fuzzy_with_acc(
    reader: &mut Reader<&[u8]>,
    canonical_end_tag: &[u8],
    original_start_tag: &[u8],
    text: String,
) -> Result<String, XsdValidationError> {
    read_text_until_end_fuzzy_next(
        reader,
        canonical_end_tag,
        original_start_tag,
        text,
        Vec::new(),
    )
}

fn read_text_until_end_fuzzy_next(
    reader: &mut Reader<&[u8]>,
    canonical_end_tag: &[u8],
    original_start_tag: &[u8],
    text: String,
    mut buf: Vec<u8>,
) -> Result<String, XsdValidationError> {
    match reader.read_event_into(&mut buf) {
        Ok(Event::Text(e)) => {
            let next_text = format!("{text}{}", e.unescape().unwrap_or_default());
            read_text_until_end_fuzzy_with_acc(
                reader,
                canonical_end_tag,
                original_start_tag,
                next_text,
            )
        }
        Ok(Event::CData(e)) => {
            let next_text = format!("{text}{}", String::from_utf8_lossy(&e));
            read_text_until_end_fuzzy_with_acc(
                reader,
                canonical_end_tag,
                original_start_tag,
                next_text,
            )
        }
        Ok(Event::End(e))
            if e.name().as_ref() == canonical_end_tag
                || e.name().as_ref() == original_start_tag =>
        {
            Ok(text.trim().to_string())
        }
        Ok(Event::End(_)) => {
            read_text_until_end_fuzzy_with_acc(reader, canonical_end_tag, original_start_tag, text)
        }
        Ok(Event::Eof) => Err(XsdValidationError {
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
        }),
        Ok(_) => {
            read_text_until_end_fuzzy_with_acc(reader, canonical_end_tag, original_start_tag, text)
        }
        Err(e) => Err(make_parse_error(canonical_end_tag, &e)),
    }
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
pub(crate) fn skip_to_end(reader: &mut Reader<&[u8]>, end_tag: &[u8]) -> Result<(), XsdValidationError> {
    skip_to_end_with_depth(reader, end_tag, 1)
}

fn skip_to_end_with_depth(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
    depth: usize,
) -> Result<(), XsdValidationError> {
    skip_to_end_next(reader, end_tag, depth, Vec::new())
}

fn skip_to_end_next(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
    depth: usize,
    mut buf: Vec<u8>,
) -> Result<(), XsdValidationError> {
    match reader.read_event_into(&mut buf) {
        Ok(Event::Start(e)) if e.name().as_ref() == end_tag => {
            skip_to_end_with_depth(reader, end_tag, depth.saturating_add(1))
        }
        Ok(Event::End(e)) if e.name().as_ref() == end_tag => {
            if depth.saturating_sub(1) == 0 {
                Ok(())
            } else {
                skip_to_end_with_depth(reader, end_tag, depth.saturating_sub(1))
            }
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: String::from_utf8_lossy(end_tag).to_string(),
            expected: format!("closing </{}>", String::from_utf8_lossy(end_tag)),
            found: "unexpected end of file".to_string(),
            suggestion: "Check that all XML elements are properly closed.".to_string(),
            example: None,
        }),
        Ok(_) => skip_to_end_with_depth(reader, end_tag, depth),
        Err(e) => Err(make_parse_error(end_tag, &e)),
    }
}

fn merge_raw_content(raw_content: Option<String>, fragment: &str) -> Option<String> {
    let trimmed_fragment = fragment.trim();
    if trimmed_fragment.is_empty() {
        return raw_content;
    }

    raw_content
        .map(|existing| format!("{existing} {trimmed_fragment}"))
        .or_else(|| Some(trimmed_fragment.to_string()))
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
/// A `SkillsMcp` struct with parsed entries.
pub(crate) fn parse_skills_mcp(reader: &mut Reader<&[u8]>) -> SkillsMcp {
    let parsed_state = parse_skills_mcp_state(
        reader,
        SkillsMcpState {
            skills: Vec::new(),
            mcps: Vec::new(),
            raw_content: None,
        },
    );

    SkillsMcp {
        skills: parsed_state.skills,
        mcps: parsed_state.mcps,
        raw_content: parsed_state.raw_content,
    }
}

struct SkillsMcpState {
    skills: Vec<SkillEntry>,
    mcps: Vec<McpEntry>,
    raw_content: Option<String>,
}

fn parse_skills_mcp_state(reader: &mut Reader<&[u8]>, state: SkillsMcpState) -> SkillsMcpState {
    parse_skills_mcp_next(reader, state, Vec::new())
}

fn parse_skills_mcp_next(
    reader: &mut Reader<&[u8]>,
    state: SkillsMcpState,
    mut buf: Vec<u8>,
) -> SkillsMcpState {
    match reader.read_event_into(&mut buf) {
        Ok(Event::Start(e)) => {
            let tag = e.name();
            let tag_bytes = tag.as_ref();
            let reason = e
                .attributes()
                .filter_map(std::result::Result::ok)
                .find(|a| a.key.as_ref() == b"reason")
                .and_then(|a| a.unescape_value().ok())
                .map(Cow::into_owned)
                .filter(|s| !s.is_empty());

            let next_state = match tag_bytes {
                b"skill" => merge_skill(state, read_text_until_end(reader, b"skill"), reason),
                b"mcp" => merge_mcp(state, read_text_until_end(reader, b"mcp"), reason),
                other => {
                    let _ = skip_to_end(reader, other);
                    state
                }
            };

            parse_skills_mcp_state(reader, next_state)
        }
        Ok(Event::Empty(_)) => parse_skills_mcp_state(reader, state),
        Ok(Event::Text(e)) => {
            let text = e.unescape().unwrap_or_default().to_string();
            let SkillsMcpState {
                skills,
                mcps,
                raw_content,
            } = state;
            parse_skills_mcp_state(
                reader,
                SkillsMcpState {
                    skills,
                    mcps,
                    raw_content: merge_raw_content(raw_content, &text),
                },
            )
        }
        Ok(Event::CData(e)) => {
            let text = String::from_utf8_lossy(&e).to_string();
            let SkillsMcpState {
                skills,
                mcps,
                raw_content,
            } = state;
            parse_skills_mcp_state(
                reader,
                SkillsMcpState {
                    skills,
                    mcps,
                    raw_content: merge_raw_content(raw_content, &text),
                },
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"skills-mcp" => state,
        Ok(Event::Eof) => state,
        Ok(_) => parse_skills_mcp_state(reader, state),
        Err(_) => state,
    }
}

fn merge_skill(
    state: SkillsMcpState,
    parsed_name: Result<String, XsdValidationError>,
    reason: Option<String>,
) -> SkillsMcpState {
    let SkillsMcpState {
        skills,
        mcps,
        raw_content,
    } = state;
    let name = parsed_name.unwrap_or_default().trim().to_string();

    if name.is_empty() {
        SkillsMcpState {
            skills,
            mcps,
            raw_content,
        }
    } else {
        SkillsMcpState {
            skills: skills
                .into_iter()
                .chain(std::iter::once(SkillEntry { name, reason }))
                .collect(),
            mcps,
            raw_content,
        }
    }
}

fn merge_mcp(
    state: SkillsMcpState,
    parsed_name: Result<String, XsdValidationError>,
    reason: Option<String>,
) -> SkillsMcpState {
    let SkillsMcpState {
        skills,
        mcps,
        raw_content,
    } = state;
    let name = parsed_name.unwrap_or_default().trim().to_string();

    if name.is_empty() {
        SkillsMcpState {
            skills,
            mcps,
            raw_content,
        }
    } else {
        SkillsMcpState {
            skills,
            mcps: mcps
                .into_iter()
                .chain(std::iter::once(McpEntry { name, reason }))
                .collect(),
            raw_content,
        }
    }
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

    #[test]
    fn test_merge_raw_content_skips_blank_fragments() {
        let merged = merge_raw_content(None, "  ");
        assert_eq!(merged, None);
    }

    #[test]
    fn test_merge_raw_content_joins_fragments_with_spaces() {
        let merged = merge_raw_content(None, "  first  ");
        let merged = merge_raw_content(merged, "second");
        let merged = merge_raw_content(merged, "  third  ");

        assert_eq!(merged, Some("first second third".to_string()));
    }
}
