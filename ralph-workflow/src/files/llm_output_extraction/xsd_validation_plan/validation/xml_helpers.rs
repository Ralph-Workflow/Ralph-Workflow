// XML parsing helpers (get_attributes, read_text_until_end, skip_to_end, read_inner_xml)

/// Extract attributes from a quick-xml `BytesStart`
fn get_attributes(e: &quick_xml::events::BytesStart<'_>) -> HashMap<String, String> {
    let mut attrs = HashMap::new();
    for attr in e.attributes().flatten() {
        if let (Ok(key), Ok(value)) = (
            std::str::from_utf8(attr.key.as_ref()),
            std::str::from_utf8(&attr.value),
        ) {
            attrs.insert(key.to_string(), value.to_string());
        }
    }
    attrs
}

/// Read text content until the end tag
fn read_text_until_end(
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
                    found: "end of file".to_string(),
                    suggestion: "Check XML is well-formed".to_string(),
                    example: None,
                });
            }
            Ok(_) => {} // Skip other events
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(end_tag).to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    Ok(text.trim().to_string())
}

/// Read text content until the closing tag, accepting either canonical OR original tag name.
///
/// This function is used when fuzzy tag matching resolves a misspelled tag to its canonical form.
/// For example, if `<title>` is matched to `title`, this function will accept
/// either `</title>` OR the original misspelled closing tag.
fn read_text_until_end_fuzzy(
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
                if e.name().as_ref() == canonical_end_tag || e.name().as_ref() == original_start_tag {
                    break;
                }
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
            Ok(_) => {} // Skip other events
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(canonical_end_tag).to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    Ok(text.trim().to_string())
}

/// Skip until the end of the current element (handles nested elements)
fn skip_to_end(reader: &mut Reader<&[u8]>, end_tag: &[u8]) -> Result<(), XsdValidationError> {
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
                    found: "end of file".to_string(),
                    suggestion: "Check XML is well-formed".to_string(),
                    example: None,
                });
            }
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(end_tag).to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    Ok(())
}

/// Read all content (including nested XML) as a string until end tag
fn read_inner_xml(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
) -> Result<String, XsdValidationError> {
    let mut buf = Vec::new();
    let mut content = String::new();
    let mut depth: usize = 1;

    loop {
        let event = reader.read_event_into(&mut buf);
        match &event {
            Ok(Event::Start(e)) => {
                if e.name().as_ref() == end_tag {
                    depth = depth.saturating_add(1);
                }
                // Reconstruct the tag
                content.push('<');
                content.push_str(&String::from_utf8_lossy(e.name().as_ref()));
                for attr in e.attributes().flatten() {
                    content.push(' ');
                    content.push_str(&String::from_utf8_lossy(attr.key.as_ref()));
                    content.push_str("=\"");
                    content.push_str(&String::from_utf8_lossy(&attr.value));
                    content.push('"');
                }
                content.push('>');
            }
            Ok(Event::End(e)) => {
                if e.name().as_ref() == end_tag {
                    depth = depth.saturating_sub(1);
                    if depth == 0 {
                        break;
                    }
                }
                content.push_str("</");
                content.push_str(&String::from_utf8_lossy(e.name().as_ref()));
                content.push('>');
            }
            Ok(Event::Empty(e)) => {
                content.push('<');
                content.push_str(&String::from_utf8_lossy(e.name().as_ref()));
                for attr in e.attributes().flatten() {
                    content.push(' ');
                    content.push_str(&String::from_utf8_lossy(attr.key.as_ref()));
                    content.push_str("=\"");
                    content.push_str(&String::from_utf8_lossy(&attr.value));
                    content.push('"');
                }
                content.push_str("/>");
            }
            Ok(Event::Text(e)) => {
                // Keep escaped entities as-is for re-parsing (don't unescape)
                content.push_str(&String::from_utf8_lossy(e.as_ref()));
            }
            Ok(Event::CData(e)) => {
                // Preserve CDATA sections so they can be re-parsed correctly
                content.push_str("<![CDATA[");
                content.push_str(&String::from_utf8_lossy(e.as_ref()));
                content.push_str("]]>");
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(end_tag).to_string(),
                    expected: format!("closing </{}>", String::from_utf8_lossy(end_tag)),
                    found: "end of file".to_string(),
                    suggestion: "Check XML is well-formed".to_string(),
                    example: None,
                });
            }
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: String::from_utf8_lossy(end_tag).to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    Ok(content)
}
