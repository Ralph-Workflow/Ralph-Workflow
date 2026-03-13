// Section parsing helpers.
/// Strip block-level elements from content for inline parsing.
///
/// This allows list items to contain block-level elements like `<code-block>`,
/// `<paragraph>`, and nested `<list>` without breaking inline parsing.
/// The block elements are removed, leaving only inline content to be parsed.
// pub(super) for test access from tests submodule
pub(super) fn strip_block_elements_for_inline_parsing(content: &str) -> String {
    let mut result = content.to_string();
    // Remove <list>...</list> blocks (handled separately for nesting)
    while let Some(start) = result.find("<list") {
        if let Some(end) = result[start..].find("</list>") {
            result = format!("{}{}", &result[..start], &result[start + end + 7..]);
        } else {
            break;
        }
    }

    // Remove <code-block>...</code-block> blocks
    while let Some(start) = result.find("<code-block") {
        if let Some(end) = result[start..].find("</code-block>") {
            result = format!("{}{}", &result[..start], &result[start + end + 13..]);
        } else {
            break;
        }
    }

    // Remove <paragraph>...</paragraph> blocks
    while let Some(start) = result.find("<paragraph") {
        if let Some(end) = result[start..].find("</paragraph>") {
            result = format!("{}{}", &result[..start], &result[start + end + 12..]);
        } else {
            break;
        }
    }

    result
}

/// Parse inline content elements (text, emphasis, code, link)
fn parse_inline_elements(content: &str) -> Vec<InlineElement> {
    let mut elements = Vec::new();
    let mut reader = Reader::from_str(content);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();
    let mut current_text = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                // Flush any accumulated text
                if !current_text.trim().is_empty() {
                    elements.push(InlineElement::Text(current_text.trim().to_string()));
                }
                current_text.clear();

                match e.name().as_ref() {
                    b"emphasis" => {
                        if let Ok(text) = read_text_until_end(&mut reader, b"emphasis") {
                            elements.push(InlineElement::Emphasis(text));
                        }
                    }
                    b"code" => {
                        if let Ok(text) = read_text_until_end(&mut reader, b"code") {
                            elements.push(InlineElement::Code(text));
                        }
                    }
                    b"link" => {
                        let attrs = get_attributes(&e);
                        let href = attrs.get("href").cloned().unwrap_or_default();
                        if let Ok(text) = read_text_until_end(&mut reader, b"link") {
                            elements.push(InlineElement::Link { href, text });
                        }
                    }
                    _ => {
                        // Skip unknown inline elements
                        let _ = skip_to_end(&mut reader, e.name().as_ref());
                    }
                }
            }
            Ok(Event::Text(e)) => {
                current_text.push_str(&e.unescape().unwrap_or_default());
            }
            Ok(Event::End(_) | Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    // Flush any remaining text
    if !current_text.trim().is_empty() {
        elements.push(InlineElement::Text(current_text.trim().to_string()));
    }

    elements
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
                        let level: u8 =
                            attrs.get("level").and_then(|s| s.parse().ok()).unwrap_or(3);
                        let text = read_text_until_end(&mut reader, b"heading")?;
                        if !(2..=4).contains(&level) {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::InvalidContent,
                                element_path: "heading/@level".to_string(),
                                expected: "level between 2 and 4".to_string(),
                                found: level.to_string(),
                                suggestion: "Use level=\"2\", level=\"3\", or level=\"4\""
                                    .to_string(),
                                example: None,
                            });
                        }
                        elements.push(ContentElement::Heading(Heading { level, text }));
                    }
                    b"list" => {
                        let attrs = get_attributes(&e);
                        let list_type_str =
                            attrs.get("type").map_or("", std::string::String::as_str);
                        let list_type = match normalize_enum_value(
                            list_type_str,
                            LIST_TYPE_VALID,
                            LIST_TYPE_SYNONYMS,
                        )
                        .as_deref()
                        {
                            Some("ordered") => ListType::Ordered,
                            Some("unordered") => ListType::Unordered,
                            _ => {
                                return Err(XsdValidationError {
                                    error_type: XsdErrorType::InvalidContent,
                                    element_path: "list/@type".to_string(),
                                    expected: "ordered or unordered".to_string(),
                                    found: list_type_str.to_string(),
                                    suggestion: "Use type=\"ordered\" or type=\"unordered\""
                                        .to_string(),
                                    example: None,
                                });
                            }
                        };
                        let list = parse_list(&mut reader, list_type)?;
                        elements.push(ContentElement::List(list));
                    }
                    b"table" => {
                        let table = parse_table(&mut reader)?;
                        elements.push(ContentElement::Table(table));
                    }
                    _ => {
                        // Skip unknown elements
                        let _ = skip_to_end(&mut reader, e.name().as_ref());
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
                _ => {
                    let _ = skip_to_end(reader, e.name().as_ref());
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
                cells.push(Cell {
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
fn parse_summary(reader: &mut Reader<&[u8]>) -> Result<PlanSummary, XsdValidationError> {
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
                    scope_items = parse_scope_items(reader)?;
                }
                _ => {
                    let _ = skip_to_end(reader, e.name().as_ref());
                }
            },
            Ok(Event::End(e)) if e.name().as_ref() == b"ralph-summary" => break,
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
