// Section parsing helpers.

// Note: normalize_tag_name is imported in main_validator.rs and available in this module
// via the include! statement that combines all validation/*.rs files

/// Known sub-element tags for section parsing (rich content, summary, table).
/// Used for fuzzy tag name matching (typo tolerance).
const SECTION_SUB_ELEMENT_TAGS: &[&str] = &[
    // Rich content elements
    "paragraph",
    "code-block",
    "heading",
    "list",
    "table",
    "item",
    "caption",
    "column",
    "row",
    "cell",
    // Summary elements
    "context",
    "scope-items",
    "scope-item",
];

/// Strip block-level elements from content for inline parsing.
///
/// This allows list items to contain block-level elements like `<code-block>`,
/// `<paragraph>`, and nested `<list>` without breaking inline parsing.
/// The block elements are removed, leaving only inline content to be parsed.
// pub(super) for test access from tests submodule
pub(super) fn strip_block_elements_for_inline_parsing(content: &str) -> String {
    ["list", "code-block", "paragraph"]
        .into_iter()
        .fold(content.to_string(), |current, tag| {
            strip_tag_blocks(&current, tag)
        })
}

fn strip_tag_blocks(content: &str, tag: &str) -> String {
    let open_tag = format!("<{tag}");
    let close_tag = format!("</{tag}>");

    strip_next_tag_block(content, &open_tag, &close_tag).map_or_else(
        || content.to_string(),
        |stripped| strip_tag_blocks(&stripped, tag),
    )
}

fn strip_next_tag_block(content: &str, open_tag: &str, close_tag: &str) -> Option<String> {
    let start = content.find(open_tag)?;
    let end = find_tag_block_end(content, start, open_tag, close_tag)?;

    Some(format!("{}{}", &content[..start], &content[end..]))
}

fn find_tag_block_end(
    content: &str,
    start: usize,
    open_tag: &str,
    close_tag: &str,
) -> Option<usize> {
    find_tag_block_end_recursive(content, start + open_tag.len(), open_tag, close_tag, 1)
}

fn find_tag_block_end_recursive(
    content: &str,
    cursor: usize,
    open_tag: &str,
    close_tag: &str,
    depth: usize,
) -> Option<usize> {
    let next_open = content[cursor..].find(open_tag).map(|idx| cursor + idx);
    let next_close = content[cursor..].find(close_tag).map(|idx| cursor + idx);

    match (next_open, next_close) {
        (_, None) => None,
        (Some(open_index), Some(close_index)) if open_index < close_index => {
            find_tag_block_end_recursive(
                content,
                open_index + open_tag.len(),
                open_tag,
                close_tag,
                depth.saturating_add(1),
            )
        }
        (_, Some(close_index)) => {
            let remaining_depth = depth.saturating_sub(1);
            if remaining_depth == 0 {
                Some(close_index + close_tag.len())
            } else {
                find_tag_block_end_recursive(
                    content,
                    close_index + close_tag.len(),
                    open_tag,
                    close_tag,
                    remaining_depth,
                )
            }
        }
    }
}

/// Parse inline content elements (text, emphasis, code, link)
fn parse_inline_elements(content: &str) -> Vec<InlineElement> {
    InlineElementCursor::new(content).collect()
}

struct InlineElementCursor<'a> {
    reader: Reader<&'a [u8]>,
    buf: Vec<u8>,
    current_text: String,
    pending_start: Option<quick_xml::events::BytesStart<'static>>,
}

impl<'a> InlineElementCursor<'a> {
    fn new(content: &'a str) -> Self {
        let mut reader = Reader::from_str(content);
        reader.config_mut().trim_text(false);
        Self {
            reader,
            buf: Vec::new(),
            current_text: String::new(),
            pending_start: None,
        }
    }

    fn take_text(&mut self) -> Option<InlineElement> {
        let trimmed = self.current_text.trim();
        if trimmed.is_empty() {
            self.current_text.clear();
            return None;
        }
        let text = trimmed.to_string();
        self.current_text.clear();
        Some(InlineElement::Text(text))
    }

    fn handle_start(
        &mut self,
        start: quick_xml::events::BytesStart<'static>,
    ) -> Option<InlineElement> {
        match start.name().as_ref() {
            b"emphasis" => read_text_until_end(&mut self.reader, b"emphasis")
                .ok()
                .map(InlineElement::Emphasis),
            b"code" => read_text_until_end(&mut self.reader, b"code")
                .ok()
                .map(InlineElement::Code),
            b"link" => {
                let attrs = get_attributes(&start);
                let href = attrs.get("href").cloned().unwrap_or_default();
                read_text_until_end(&mut self.reader, b"link")
                    .ok()
                    .map(|text| InlineElement::Link { href, text })
            }
            _ => {
                let _ = skip_to_end(&mut self.reader, start.name().as_ref());
                None
            }
        }
    }
}

impl<'a> Iterator for InlineElementCursor<'a> {
    type Item = InlineElement;

    fn next(&mut self) -> Option<Self::Item> {
        if let Some(start) = self.pending_start.take() {
            if let Some(element) = self.handle_start(start) {
                return Some(element);
            }
        }

        loop {
            self.buf.clear();
            match self.reader.read_event_into(&mut self.buf) {
                Ok(Event::Start(e)) => {
                    let owned = e.to_owned();
                    if !self.current_text.trim().is_empty() {
                        self.pending_start = Some(owned);
                        return self.take_text();
                    }
                    if let Some(element) = self.handle_start(owned) {
                        return Some(element);
                    }
                }
                Ok(Event::Text(e)) => {
                    self.current_text
                        .push_str(&e.unescape().unwrap_or_default());
                }
                Ok(Event::End(_) | Event::Eof) | Err(_) => {
                    return self.take_text();
                }
                Ok(_) => {}
            }
        }
    }
}

/// Parse rich content from a <content> element
fn parse_rich_content(content: &str) -> Result<RichContent, XsdValidationError> {
    // Tolerant: normalize list type via synonym table before rejecting.
    // Accepts "bulleted", "bullet", "ul" as "unordered"; "numbered", "ol" as "ordered".
    const LIST_TYPE_VALID: &[&str] = &["ordered", "unordered"];
    let mut elements = Vec::new();
    let mut reader = Reader::from_str(content);
    reader.config_mut().trim_text(true);
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                match e.name().as_ref() {
                    b"paragraph" => {
                        let inner = read_inner_xml(&mut reader, b"paragraph")?;
                        elements.push(ContentElement::Paragraph(Paragraph {
                            content: parse_inline_elements(&inner),
                        }));
                    }
                    b"code-block" => {
                        let attrs = get_attributes(&e);
                        let code = read_text_until_end(&mut reader, b"code-block")?;
                        elements.push(ContentElement::CodeBlock(CodeBlock {
                            content: code,
                            language: attrs.get("language").cloned(),
                            filename: attrs.get("filename").cloned(),
                        }));
                    }
                    b"heading" => {
                        let attrs = get_attributes(&e);
                        let raw_level: u8 =
                            attrs.get("level").and_then(|s| s.parse().ok()).unwrap_or(3);
                        // Tolerant: clamp level to the valid range 2..=4 instead of rejecting.
                        // Level 1 → 2 (below minimum), level 5+ → 4 (above maximum), missing → 3.
                        let level = raw_level.clamp(2, 4);
                        let text = read_text_until_end(&mut reader, b"heading")?;
                        elements.push(ContentElement::Heading(Heading { level, text }));
                    }
                    b"list" => {
                        let attrs = get_attributes(&e);
                        let list_type_str =
                            attrs.get("type").map_or("", std::string::String::as_str);
                        // Tolerant: missing or unrecognized list type defaults to Unordered.
                        // Only true synonyms (bulleted, ul, numbered, ol) and canonical values
                        // are recognized; everything else (including empty/missing) defaults to
                        // Unordered rather than rejecting, since type is non-essential structure.
                        let list_type = match normalize_enum_value(
                            list_type_str,
                            LIST_TYPE_VALID,
                            LIST_TYPE_SYNONYMS,
                        )
                        .as_deref()
                        {
                            Some("ordered") => ListType::Ordered,
                            // Unknown, empty, or unordered value: default to Unordered
                            Some(_) | None => ListType::Unordered,
                        };
                        let list = parse_list(&mut reader, list_type)?;
                        elements.push(ContentElement::List(list));
                    }
                    b"table" => {
                        let table = parse_table(&mut reader)?;
                        elements.push(ContentElement::Table(table));
                    }
                    other => {
                        // Tolerant: try fuzzy tag matching before skipping.
                        // If the tag is a known sub-element with minor typo, route to correct handler.
                        let tag_name = String::from_utf8_lossy(other);
                        if let Some(canonical) =
                            normalize_tag_name(&tag_name, SECTION_SUB_ELEMENT_TAGS)
                        {
                            // Re-parse with the canonical tag name
                            match canonical {
                                "paragraph" => {
                                    let inner = read_inner_xml(&mut reader, other)?;
                                    elements.push(ContentElement::Paragraph(Paragraph {
                                        content: parse_inline_elements(&inner),
                                    }));
                                }
                                "code-block" => {
                                    let attrs = get_attributes(&e);
                                    let code = read_text_until_end_fuzzy(
                                        &mut reader,
                                        b"code-block",
                                        other,
                                    )?;
                                    elements.push(ContentElement::CodeBlock(CodeBlock {
                                        content: code,
                                        language: attrs.get("language").cloned(),
                                        filename: attrs.get("filename").cloned(),
                                    }));
                                }
                                "heading" => {
                                    let attrs = get_attributes(&e);
                                    let raw_level: u8 = attrs
                                        .get("level")
                                        .and_then(|s| s.parse().ok())
                                        .unwrap_or(3);
                                    let level = raw_level.clamp(2, 4);
                                    let text =
                                        read_text_until_end_fuzzy(&mut reader, b"heading", other)?;
                                    elements.push(ContentElement::Heading(Heading { level, text }));
                                }
                                "list" => {
                                    let attrs = get_attributes(&e);
                                    let list_type_str =
                                        attrs.get("type").map_or("", std::string::String::as_str);
                                    let list_type = match normalize_enum_value(
                                        list_type_str,
                                        &["ordered", "unordered"],
                                        LIST_TYPE_SYNONYMS,
                                    )
                                    .as_deref()
                                    {
                                        Some("ordered") => ListType::Ordered,
                                        Some(_) | None => ListType::Unordered,
                                    };
                                    let list = parse_list(&mut reader, list_type)?;
                                    elements.push(ContentElement::List(list));
                                }
                                "table" => {
                                    let table = parse_table(&mut reader)?;
                                    elements.push(ContentElement::Table(table));
                                }
                                _ => {
                                    // Skip other canonical matches (item, caption, column, row, cell, etc.)
                                    let _ = skip_to_end(&mut reader, other);
                                }
                            }
                        } else {
                            // Skip unknown elements
                            let _ = skip_to_end(&mut reader, other);
                        }
                    }
                }
            }
            Ok(Event::Eof) => break,
            Ok(Event::Text(_) | _) => {} // Tolerant: skip stray text and other events
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "content".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    if elements.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "content".to_string(),
            expected: "at least one content element".to_string(),
            found: "empty content".to_string(),
            suggestion: "Add <paragraph>, <code-block>, <table>, <list>, or <heading> elements"
                .to_string(),
            example: None,
        });
    }

    Ok(RichContent { elements })
}

/// Parse a list element
fn parse_list(reader: &mut Reader<&[u8]>, list_type: ListType) -> Result<List, XsdValidationError> {
    let mut items = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"item" => {
                let inner = read_inner_xml(reader, b"item")?;
                // Check for nested list
                let nested_list = if inner.contains("<list") {
                    // Parse the nested list separately
                    let mut inner_reader = Reader::from_str(&inner);
                    inner_reader.config_mut().trim_text(true);
                    let mut inner_buf = Vec::new();
                    let mut nested = None;

                    loop {
                        match inner_reader.read_event_into(&mut inner_buf) {
                            Ok(Event::Start(e2)) if e2.name().as_ref() == b"list" => {
                                let attrs = get_attributes(&e2);
                                // Tolerant: normalize nested list type via synonym table.
                                // Accepts "bulleted", "bullet", "ul" as "unordered";
                                // "numbered", "ol" as "ordered"; case-insensitive.
                                // Unknown values default to Unordered (previous behavior preserved).
                                let raw_nested_type =
                                    attrs.get("type").map_or("", std::string::String::as_str);
                                let nested_type = match normalize_enum_value(
                                    raw_nested_type,
                                    &["ordered", "unordered"],
                                    LIST_TYPE_SYNONYMS,
                                )
                                .as_deref()
                                {
                                    Some("ordered") => ListType::Ordered,
                                    // Unknown or unordered value: default to unordered
                                    Some(_) | None => ListType::Unordered,
                                };
                                nested =
                                    Some(Box::new(parse_list(&mut inner_reader, nested_type)?));
                            }
                            Ok(Event::Eof) | Err(_) => break,
                            Ok(_) => {}
                        }
                        inner_buf.clear();
                    }
                    nested
                } else {
                    None
                };

                // Extract text content, stripping out block-level elements that we allow
                // but don't need to parse into the data structure (code-block, paragraph, list)
                let text_content = strip_block_elements_for_inline_parsing(&inner);

                items.push(ListItem {
                    content: parse_inline_elements(&text_content),
                    nested_list,
                });
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"list" => break,
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "list".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    if items.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "list".to_string(),
            expected: "at least one <item> element".to_string(),
            found: "empty list".to_string(),
            suggestion: "Add <item>...</item> to the list".to_string(),
            example: None,
        });
    }

    Ok(List { list_type, items })
}

/// Parse a table element
fn parse_table(reader: &mut Reader<&[u8]>) -> Result<Table, XsdValidationError> {
    let mut caption = None;
    let mut columns = Vec::new();
    let mut rows = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"caption" => {
                    caption = Some(read_text_until_end(reader, b"caption")?);
                }
                b"columns" => {
                    columns = parse_columns(reader)?;
                }
                b"row" => {
                    rows.push(parse_row(reader)?);
                }
                other => {
                    // Tolerant: try fuzzy tag matching before skipping.
                    // If the tag is a known sub-element with minor typo, route to correct handler.
                    let tag_name = String::from_utf8_lossy(other);
                    if let Some(canonical) = normalize_tag_name(&tag_name, SECTION_SUB_ELEMENT_TAGS)
                    {
                        match canonical {
                            "caption" => {
                                caption =
                                    Some(read_text_until_end_fuzzy(reader, b"caption", other)?);
                            }
                            "columns" => {
                                columns = parse_columns(reader)?;
                            }
                            "row" => {
                                rows.push(parse_row(reader)?);
                            }
                            _ => {
                                let _ = skip_to_end(reader, other);
                            }
                        }
                    } else {
                        let _ = skip_to_end(reader, other);
                    }
                }
            },
            Ok(Event::End(e)) if e.name().as_ref() == b"table" => break,
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "table".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    if rows.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "table".to_string(),
            expected: "at least one <row> element".to_string(),
            found: "no rows".to_string(),
            suggestion: "Add <row><cell>...</cell></row> to the table".to_string(),
            example: None,
        });
    }

    Ok(Table {
        caption,
        columns,
        rows,
    })
}

/// Parse table columns
fn parse_columns(reader: &mut Reader<&[u8]>) -> Result<Vec<String>, XsdValidationError> {
    let mut columns = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"column" => {
                columns.push(read_text_until_end(reader, b"column")?);
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"columns" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    Ok(columns)
}

/// Parse a table row
fn parse_row(reader: &mut Reader<&[u8]>) -> Result<Row, XsdValidationError> {
    let mut cells = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"cell" => {
                let inner = read_inner_xml(reader, b"cell")?;
                cells.push(TableCell {
                    content: parse_inline_elements(&inner),
                });
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"row" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    if cells.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "table/row".to_string(),
            expected: "at least one <cell> in each row".to_string(),
            found: "empty row".to_string(),
            suggestion: "Add <cell> elements to the row".to_string(),
            example: None,
        });
    }

    Ok(Row { cells })
}

/// Parse the ralph-summary section
///
/// The `original_tag` parameter is used for fuzzy matching - when the opening tag was misspelled,
/// this allows the parser to accept either the canonical closing tag OR the original misspelled one.
fn parse_summary(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
) -> Result<PlanSummary, XsdValidationError> {
    let canonical_tag = b"ralph-summary";
    let mut context = None;
    let mut scope_items = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"context" => {
                    context = Some(read_text_until_end(reader, b"context")?);
                }
                b"scope-items" => {
                    // Normal path: scope-items wrapper is present
                    let mut wrapped = parse_scope_items(reader)?;
                    scope_items.append(&mut wrapped);
                }
                b"scope-item" => {
                    // Tolerant: bare scope-item without scope-items wrapper.
                    // Merge directly into scope_items vec.
                    let attrs = get_attributes(&e);
                    let description = read_text_until_end(reader, b"scope-item")?;
                    scope_items.push(ScopeItem {
                        description,
                        count: attrs.get("count").cloned(),
                        category: attrs.get("category").cloned(),
                    });
                }
                other => {
                    // Tolerant: try fuzzy tag matching before skipping.
                    // If the tag is a known sub-element with minor typo, route to correct handler.
                    let tag_name = String::from_utf8_lossy(other);
                    if let Some(canonical) = normalize_tag_name(&tag_name, SECTION_SUB_ELEMENT_TAGS)
                    {
                        match canonical {
                            "context" => {
                                context =
                                    Some(read_text_until_end_fuzzy(reader, b"context", other)?);
                            }
                            "scope-items" => {
                                let mut wrapped = parse_scope_items(reader)?;
                                scope_items.append(&mut wrapped);
                            }
                            "scope-item" => {
                                let attrs = get_attributes(&e);
                                let description =
                                    read_text_until_end_fuzzy(reader, b"scope-item", other)?;
                                scope_items.push(ScopeItem {
                                    description,
                                    count: attrs.get("count").cloned(),
                                    category: attrs.get("category").cloned(),
                                });
                            }
                            _ => {
                                let _ = skip_to_end(reader, other);
                            }
                        }
                    } else {
                        let _ = skip_to_end(reader, other);
                    }
                }
            },
            Ok(Event::End(e)) => {
                // Accept either canonical tag OR original (misspelled) tag
                if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag {
                    break;
                }
            }
            Ok(Event::Eof) => break,
            Ok(Event::Text(_) | _) => {} // Tolerant: skip stray text and other events
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-summary".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    let context = context.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-summary/context".to_string(),
        expected: "<context> element".to_string(),
        found: "no <context> found".to_string(),
        suggestion: "Add <context>Description of what is being done</context>".to_string(),
        example: None,
    })?;

    if context.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-summary/context".to_string(),
            expected: "non-empty context".to_string(),
            found: "empty context".to_string(),
            suggestion: "Provide a description of what is being done".to_string(),
            example: None,
        });
    }

    if scope_items.len() < 3 {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-summary/scope-items".to_string(),
            expected: "at least 3 scope-item elements".to_string(),
            found: format!("{} scope-item(s)", scope_items.len()),
            suggestion:
                "Add more <scope-item count=\"N\" category=\"X\">description</scope-item> elements"
                    .to_string(),
            example: None,
        });
    }

    Ok(PlanSummary {
        context,
        scope_items,
    })
}

/// Parse scope-items
fn parse_scope_items(reader: &mut Reader<&[u8]>) -> Result<Vec<ScopeItem>, XsdValidationError> {
    let mut items = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"scope-item" => {
                let attrs = get_attributes(&e);
                let description = read_text_until_end(reader, b"scope-item")?;
                items.push(ScopeItem {
                    description,
                    count: attrs.get("count").cloned(),
                    category: attrs.get("category").cloned(),
                });
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"scope-items" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }
    Ok(items)
}
