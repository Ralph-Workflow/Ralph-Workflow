// XML parsing helpers (get_attributes, read_text_until_end, skip_to_end, read_inner_xml)

/// Extract attributes from a quick-xml `BytesStart`
fn get_attributes(e: &quick_xml::events::BytesStart<'_>) -> HashMap<String, String> {
    e.attributes()
        .flatten()
        .filter_map(|attr| {
            std::str::from_utf8(attr.key.as_ref())
                .ok()
                .zip(std::str::from_utf8(&attr.value).ok())
                .map(|(key, value)| (key.to_string(), value.to_string()))
        })
        .collect()
}

fn malformed_xml_eof_error(
    element_path: String,
    expected: String,
    suggestion: String,
) -> XsdValidationError {
    XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path,
        expected,
        found: "end of file".to_string(),
        suggestion,
        example: None,
    }
}

fn malformed_xml_parse_error(
    element_path: String,
    parse_error: quick_xml::Error,
) -> XsdValidationError {
    XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path,
        expected: "valid XML".to_string(),
        found: format!("parse error: {parse_error}"),
        suggestion: "Check XML syntax".to_string(),
        example: None,
    }
}

fn read_owned_event(
    reader: &mut Reader<&[u8]>,
) -> Result<quick_xml::events::Event<'static>, quick_xml::Error> {
    reader
        .read_event_into(&mut Vec::new())
        .map(quick_xml::events::Event::into_owned)
}

fn read_text_until_end_matching<F>(
    reader: &mut Reader<&[u8]>,
    is_end_tag: F,
    element_path: String,
    expected: String,
    suggestion: String,
    text: String,
) -> Result<String, XsdValidationError>
where
    F: Fn(&[u8]) -> bool + Copy,
{
    match read_owned_event(reader) {
        Ok(Event::Text(event)) => read_text_until_end_matching(
            reader,
            is_end_tag,
            element_path,
            expected,
            suggestion,
            format!("{text}{}", event.unescape().unwrap_or_default()),
        ),
        Ok(Event::CData(event)) => read_text_until_end_matching(
            reader,
            is_end_tag,
            element_path,
            expected,
            suggestion,
            format!("{text}{}", String::from_utf8_lossy(&event)),
        ),
        Ok(Event::End(event)) if is_end_tag(event.name().as_ref()) => Ok(text),
        Ok(Event::Eof) => Err(malformed_xml_eof_error(element_path, expected, suggestion)),
        Ok(_) => read_text_until_end_matching(
            reader,
            is_end_tag,
            element_path,
            expected,
            suggestion,
            text,
        ),
        Err(parse_error) => Err(malformed_xml_parse_error(element_path, parse_error)),
    }
}

/// Read text content until the end tag
fn read_text_until_end(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
) -> Result<String, XsdValidationError> {
    read_text_until_end_matching(
        reader,
        |tag_name| tag_name == end_tag,
        String::from_utf8_lossy(end_tag).to_string(),
        format!("closing </{}>", String::from_utf8_lossy(end_tag)),
        "Check XML is well-formed".to_string(),
        String::new(),
    )
    .map(|text| text.trim().to_string())
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
    read_text_until_end_matching(
        reader,
        |tag_name| tag_name == canonical_end_tag || tag_name == original_start_tag,
        String::from_utf8_lossy(canonical_end_tag).to_string(),
        format!(
            "closing </{}> or </{}>",
            String::from_utf8_lossy(canonical_end_tag),
            String::from_utf8_lossy(original_start_tag)
        ),
        format!(
            "Ensure the element has a matching closing tag (</{}> or </{}>).",
            String::from_utf8_lossy(canonical_end_tag),
            String::from_utf8_lossy(original_start_tag)
        ),
        String::new(),
    )
    .map(|text| text.trim().to_string())
}

fn skip_to_end_with_depth(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
    depth: usize,
) -> Result<(), XsdValidationError> {
    match read_owned_event(reader) {
        Ok(Event::Start(event)) if event.name().as_ref() == end_tag => {
            skip_to_end_with_depth(reader, end_tag, depth.saturating_add(1))
        }
        Ok(Event::End(event)) if event.name().as_ref() == end_tag => {
            if depth == 1 {
                Ok(())
            } else {
                skip_to_end_with_depth(reader, end_tag, depth.saturating_sub(1))
            }
        }
        Ok(Event::Eof) => Err(malformed_xml_eof_error(
            String::from_utf8_lossy(end_tag).to_string(),
            format!("closing </{}>", String::from_utf8_lossy(end_tag)),
            "Check XML is well-formed".to_string(),
        )),
        Ok(_) => skip_to_end_with_depth(reader, end_tag, depth),
        Err(parse_error) => Err(malformed_xml_parse_error(
            String::from_utf8_lossy(end_tag).to_string(),
            parse_error,
        )),
    }
}

/// Skip until the end of the current element (handles nested elements)
fn skip_to_end(reader: &mut Reader<&[u8]>, end_tag: &[u8]) -> Result<(), XsdValidationError> {
    skip_to_end_with_depth(reader, end_tag, 1)
}

fn attributes_fragment(attributes: quick_xml::events::attributes::Attributes<'_>) -> String {
    attributes
        .flatten()
        .map(|attr| {
            format!(
                " {}=\"{}\"",
                String::from_utf8_lossy(attr.key.as_ref()),
                String::from_utf8_lossy(&attr.value)
            )
        })
        .collect()
}

fn start_tag_fragment(event: &quick_xml::events::BytesStart<'_>) -> String {
    format!(
        "<{}{}>",
        String::from_utf8_lossy(event.name().as_ref()),
        attributes_fragment(event.attributes())
    )
}

fn empty_tag_fragment(event: &quick_xml::events::BytesStart<'_>) -> String {
    format!(
        "<{}{}/>",
        String::from_utf8_lossy(event.name().as_ref()),
        attributes_fragment(event.attributes())
    )
}

fn end_tag_fragment(event: &quick_xml::events::BytesEnd<'_>) -> String {
    format!("</{}>", String::from_utf8_lossy(event.name().as_ref()))
}

fn cdata_fragment(event: &quick_xml::events::BytesCData<'_>) -> String {
    format!("<![CDATA[{}]]>", String::from_utf8_lossy(event.as_ref()))
}

fn read_inner_xml_with_state(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
    depth: usize,
    content: String,
) -> Result<String, XsdValidationError> {
    match read_owned_event(reader) {
        Ok(Event::Start(event)) => read_inner_xml_with_state(
            reader,
            end_tag,
            if event.name().as_ref() == end_tag {
                depth.saturating_add(1)
            } else {
                depth
            },
            format!("{content}{}", start_tag_fragment(&event)),
        ),
        Ok(Event::End(event)) if event.name().as_ref() == end_tag && depth == 1 => Ok(content),
        Ok(Event::End(event)) if event.name().as_ref() == end_tag => read_inner_xml_with_state(
            reader,
            end_tag,
            depth.saturating_sub(1),
            format!("{content}{}", end_tag_fragment(&event)),
        ),
        Ok(Event::End(event)) => read_inner_xml_with_state(
            reader,
            end_tag,
            depth,
            format!("{content}{}", end_tag_fragment(&event)),
        ),
        Ok(Event::Empty(event)) => read_inner_xml_with_state(
            reader,
            end_tag,
            depth,
            format!("{content}{}", empty_tag_fragment(&event)),
        ),
        Ok(Event::Text(event)) => read_inner_xml_with_state(
            reader,
            end_tag,
            depth,
            format!("{content}{}", String::from_utf8_lossy(event.as_ref())),
        ),
        Ok(Event::CData(event)) => read_inner_xml_with_state(
            reader,
            end_tag,
            depth,
            format!("{content}{}", cdata_fragment(&event)),
        ),
        Ok(Event::Eof) => Err(malformed_xml_eof_error(
            String::from_utf8_lossy(end_tag).to_string(),
            format!("closing </{}>", String::from_utf8_lossy(end_tag)),
            "Check XML is well-formed".to_string(),
        )),
        Ok(_) => read_inner_xml_with_state(reader, end_tag, depth, content),
        Err(parse_error) => Err(malformed_xml_parse_error(
            String::from_utf8_lossy(end_tag).to_string(),
            parse_error,
        )),
    }
}

/// Read all content (including nested XML) as a string until end tag
fn read_inner_xml(
    reader: &mut Reader<&[u8]>,
    end_tag: &[u8],
) -> Result<String, XsdValidationError> {
    read_inner_xml_with_state(reader, end_tag, 1, String::new())
}

#[cfg(test)]
mod xml_helpers_tests {
    use super::*;

    fn next_start(reader: &mut Reader<&[u8]>) {
        match reader.read_event() {
            Ok(Event::Start(_)) => {}
            Ok(other) => panic!("expected start event, got {other:?}"),
            Err(error) => panic!("expected start event, got error: {error}"),
        }
    }

    fn result_or_panic<T, E: std::fmt::Display>(result: Result<T, E>) -> T {
        match result {
            Ok(value) => value,
            Err(error) => panic!("unexpected error: {error}"),
        }
    }

    #[test]
    fn read_text_until_end_unescapes_text_and_keeps_cdata() {
        let mut reader = Reader::from_str("<root>  one &amp; two <![CDATA[<raw>]]> </root>");
        next_start(&mut reader);

        let text = result_or_panic(read_text_until_end(&mut reader, b"root"));

        assert_eq!(text, "one & two <raw>");
    }

    #[test]
    fn read_inner_xml_preserves_escaped_entities_and_cdata() {
        let mut reader = Reader::from_str(
            "<outer><inner a=\"1\">x &amp; y<![CDATA[z<w>]]></inner><empty b=\"2\"/></outer>",
        );
        next_start(&mut reader);

        let inner = result_or_panic(read_inner_xml(&mut reader, b"outer"));

        assert_eq!(
            inner,
            "<inner a=\"1\">x &amp; y<![CDATA[z<w>]]></inner><empty b=\"2\"/>"
        );
    }

    #[test]
    fn get_attributes_collects_all_attributes() {
        let mut event = quick_xml::events::BytesStart::new("item");
        event.push_attribute(("priority", "high"));
        event.push_attribute(("status", "open"));

        let attrs = get_attributes(&event);

        assert_eq!(attrs.get("priority").map(String::as_str), Some("high"));
        assert_eq!(attrs.get("status").map(String::as_str), Some("open"));
    }
}
