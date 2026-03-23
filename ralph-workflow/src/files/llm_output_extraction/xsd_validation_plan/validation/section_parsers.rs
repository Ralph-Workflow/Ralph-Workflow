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

/// Valid list type values (used in parse_rich_content and parse_list).
const LIST_TYPE_VALID: &[&str] = &["ordered", "unordered"];

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
    collect_inline_events(&mut Reader::from_str(content), String::new(), Vec::new())
}

fn collect_inline_events(
    reader: &mut Reader<&[u8]>,
    text: String,
    acc: Vec<InlineElement>,
) -> Vec<InlineElement> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let owned = e.into_owned();
            let (new_acc, new_text) = flush_inline_text(acc, text);
            let dispatched = dispatch_inline_start(reader, owned);
            collect_inline_events(
                reader,
                new_text,
                new_acc
                    .into_iter()
                    .chain(dispatched)
                    .collect(),
            )
        }
        Ok(Event::Text(e)) => {
            let appended = text + &e.unescape().unwrap_or_default();
            collect_inline_events(reader, appended, acc)
        }
        Ok(Event::End(_) | Event::Eof) | Err(_) => flush_inline_text(acc, text).0,
        Ok(_) => collect_inline_events(reader, text, acc),
    }
}

/// Flush accumulated inline text into the acc vec, returning new acc and empty text.
fn flush_inline_text(acc: Vec<InlineElement>, text: String) -> (Vec<InlineElement>, String) {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        (acc, String::new())
    } else {
        let elem = InlineElement::Text(trimmed.to_string());
        (
            acc.into_iter().chain(std::iter::once(elem)).collect(),
            String::new(),
        )
    }
}

fn dispatch_inline_start(
    reader: &mut Reader<&[u8]>,
    start: quick_xml::events::BytesStart<'static>,
) -> Option<InlineElement> {
    match start.name().as_ref() {
        b"emphasis" => read_text_until_end(reader, b"emphasis")
            .ok()
            .map(InlineElement::Emphasis),
        b"code" => read_text_until_end(reader, b"code")
            .ok()
            .map(InlineElement::Code),
        b"link" => {
            let attrs = get_attributes(&start);
            let href = attrs.get("href").cloned().unwrap_or_default();
            read_text_until_end(reader, b"link")
                .ok()
                .map(|text| InlineElement::Link { href, text })
        }
        _ => {
            let _ = skip_to_end(reader, start.name().as_ref());
            None
        }
    }
}

/// Parse rich content from a <content> element
fn parse_rich_content(content: &str) -> Result<RichContent, XsdValidationError> {
    // Tolerant: normalize list type via synonym table before rejecting.
    // Accepts "bulleted", "bullet", "ul" as "unordered"; "numbered", "ol" as "ordered".
    let elements = collect_rich_elements(&mut Reader::from_str(content), Vec::new())?;

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

fn collect_rich_elements(
    reader: &mut Reader<&[u8]>,
    acc: Vec<ContentElement>,
) -> Result<Vec<ContentElement>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let maybe_elem = parse_rich_element(reader, e)?;
            collect_rich_elements(
                reader,
                acc.into_iter().chain(maybe_elem).collect(),
            )
        }
        Ok(Event::Eof) => Ok(acc),
        Ok(Event::Text(_) | _) => collect_rich_elements(reader, acc), // Tolerant: skip stray text
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "content".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

fn parse_list_type_from_attrs(attrs: &HashMap<String, String>) -> ListType {
    let list_type_str = attrs.get("type").map_or("", std::string::String::as_str);
    match normalize_enum_value(list_type_str, LIST_TYPE_VALID, LIST_TYPE_SYNONYMS).as_deref() {
        Some("ordered") => ListType::Ordered,
        // Unknown, empty, or unordered value: default to Unordered
        Some(_) | None => ListType::Unordered,
    }
}

fn parse_rich_element(
    reader: &mut Reader<&[u8]>,
    e: quick_xml::events::BytesStart<'_>,
) -> Result<Option<ContentElement>, XsdValidationError> {
    // Copy name bytes first so `e` is not borrowed during the match, allowing it to be moved
    // into `parse_rich_element_fuzzy` in the wildcard arm.
    let name = e.name().as_ref().to_vec();
    match name.as_slice() {
        b"paragraph" => {
            let inner = read_inner_xml(reader, b"paragraph")?;
            Ok(Some(ContentElement::Paragraph(Paragraph {
                content: parse_inline_elements(&inner),
            })))
        }
        b"code-block" => {
            let attrs = get_attributes(&e);
            let code = read_text_until_end(reader, b"code-block")?;
            Ok(Some(ContentElement::CodeBlock(CodeBlock {
                content: code,
                language: attrs.get("language").cloned(),
                filename: attrs.get("filename").cloned(),
            })))
        }
        b"heading" => {
            let attrs = get_attributes(&e);
            let raw_level: u8 =
                attrs.get("level").and_then(|s| s.parse().ok()).unwrap_or(3);
            // Tolerant: clamp level to the valid range 2..=4 instead of rejecting.
            let level = raw_level.clamp(2, 4);
            let text = read_text_until_end(reader, b"heading")?;
            Ok(Some(ContentElement::Heading(Heading { level, text })))
        }
        b"list" => {
            let attrs = get_attributes(&e);
            let list_type = parse_list_type_from_attrs(&attrs);
            let list = parse_list(reader, list_type)?;
            Ok(Some(ContentElement::List(list)))
        }
        b"table" => {
            let table = parse_table(reader)?;
            Ok(Some(ContentElement::Table(table)))
        }
        other => parse_rich_element_fuzzy(reader, e, other),
    }
}

fn parse_rich_element_fuzzy(
    reader: &mut Reader<&[u8]>,
    e: quick_xml::events::BytesStart<'_>,
    other: &[u8],
) -> Result<Option<ContentElement>, XsdValidationError> {
    // Tolerant: try fuzzy tag matching before skipping.
    let tag_name = String::from_utf8_lossy(other);
    if let Some(canonical) = normalize_tag_name(&tag_name, SECTION_SUB_ELEMENT_TAGS) {
        match canonical {
            "paragraph" => {
                let inner = read_inner_xml(reader, other)?;
                Ok(Some(ContentElement::Paragraph(Paragraph {
                    content: parse_inline_elements(&inner),
                })))
            }
            "code-block" => {
                let attrs = get_attributes(&e);
                let code = read_text_until_end_fuzzy(reader, b"code-block", other)?;
                Ok(Some(ContentElement::CodeBlock(CodeBlock {
                    content: code,
                    language: attrs.get("language").cloned(),
                    filename: attrs.get("filename").cloned(),
                })))
            }
            "heading" => {
                let attrs = get_attributes(&e);
                let raw_level: u8 = attrs
                    .get("level")
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(3);
                let level = raw_level.clamp(2, 4);
                let text = read_text_until_end_fuzzy(reader, b"heading", other)?;
                Ok(Some(ContentElement::Heading(Heading { level, text })))
            }
            "list" => {
                let attrs = get_attributes(&e);
                let list_type = parse_list_type_from_attrs(&attrs);
                let list = parse_list(reader, list_type)?;
                Ok(Some(ContentElement::List(list)))
            }
            "table" => {
                let table = parse_table(reader)?;
                Ok(Some(ContentElement::Table(table)))
            }
            _ => {
                // Skip other canonical matches (item, caption, column, row, cell, etc.)
                let _ = skip_to_end(reader, other);
                Ok(None)
            }
        }
    } else {
        // Skip unknown elements
        let _ = skip_to_end(reader, other);
        Ok(None)
    }
}

/// Parse a list element
fn parse_list(reader: &mut Reader<&[u8]>, list_type: ListType) -> Result<List, XsdValidationError> {
    let items = parse_list_items(reader, list_type, Vec::new())?;

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

#[expect(
    clippy::only_used_in_recursion,
    reason = "list_type is a recursive accumulator passed through to parse_list; it is not consumed in non-recursive branches"
)]
fn parse_list_items(
    reader: &mut Reader<&[u8]>,
    list_type: ListType,
    acc: Vec<ListItem>,
) -> Result<Vec<ListItem>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"item" => {
            let item = parse_list_item(reader)?;
            parse_list_items(
                reader,
                list_type,
                acc.into_iter().chain(std::iter::once(item)).collect(),
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"list" => Ok(acc),
        Ok(Event::Eof) => Ok(acc),
        Ok(_) => parse_list_items(reader, list_type, acc),
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "list".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

fn parse_list_item(reader: &mut Reader<&[u8]>) -> Result<ListItem, XsdValidationError> {
    let inner = read_inner_xml(reader, b"item")?;

    // Check for nested list
    let nested_list = if inner.contains("<list") {
        find_nested_list(&mut Reader::from_str(&inner))?
    } else {
        None
    };

    // Extract text content, stripping out block-level elements that we allow
    // but don't need to parse into the data structure (code-block, paragraph, list)
    let text_content = strip_block_elements_for_inline_parsing(&inner);

    Ok(ListItem {
        content: parse_inline_elements(&text_content),
        nested_list,
    })
}

/// Find a nested <list> element within inner XML content of a list item.
fn find_nested_list(reader: &mut Reader<&[u8]>) -> Result<Option<Box<List>>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"list" => {
            let attrs = get_attributes(&e);
            // Tolerant: normalize nested list type via synonym table.
            let raw_nested_type = attrs.get("type").map_or("", std::string::String::as_str);
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
            Ok(Some(Box::new(parse_list(reader, nested_type)?)))
        }
        Ok(Event::Eof) | Err(_) => Ok(None),
        Ok(_) => find_nested_list(reader),
    }
}

/// Parse a table element
fn parse_table(reader: &mut Reader<&[u8]>) -> Result<Table, XsdValidationError> {
    parse_table_events(reader, None, Vec::new(), Vec::new())
}

fn parse_table_events(
    reader: &mut Reader<&[u8]>,
    caption: Option<String>,
    columns: Vec<String>,
    rows: Vec<Row>,
) -> Result<Table, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let (new_caption, new_columns, new_rows) =
                parse_table_element(reader, e, caption, columns, rows)?;
            parse_table_events(reader, new_caption, new_columns, new_rows)
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"table" => finish_table(caption, columns, rows),
        Ok(Event::Eof) => finish_table(caption, columns, rows),
        Ok(_) => parse_table_events(reader, caption, columns, rows),
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "table".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

type TableState = (Option<String>, Vec<String>, Vec<Row>);

fn parse_table_element(
    reader: &mut Reader<&[u8]>,
    e: quick_xml::events::BytesStart<'_>,
    caption: Option<String>,
    columns: Vec<String>,
    rows: Vec<Row>,
) -> Result<TableState, XsdValidationError> {
    match e.name().as_ref() {
        b"caption" => {
            let text = read_text_until_end(reader, b"caption")?;
            Ok((Some(text), columns, rows))
        }
        b"columns" => {
            let new_columns = parse_columns(reader)?;
            Ok((caption, new_columns, rows))
        }
        b"row" => {
            let row = parse_row(reader)?;
            Ok((
                caption,
                columns,
                rows.into_iter().chain(std::iter::once(row)).collect(),
            ))
        }
        other => {
            // Tolerant: try fuzzy tag matching before skipping.
            let tag_name = String::from_utf8_lossy(other);
            if let Some(canonical) = normalize_tag_name(&tag_name, SECTION_SUB_ELEMENT_TAGS) {
                match canonical {
                    "caption" => {
                        let text = read_text_until_end_fuzzy(reader, b"caption", other)?;
                        Ok((Some(text), columns, rows))
                    }
                    "columns" => {
                        let new_columns = parse_columns(reader)?;
                        Ok((caption, new_columns, rows))
                    }
                    "row" => {
                        let row = parse_row(reader)?;
                        Ok((
                            caption,
                            columns,
                            rows.into_iter().chain(std::iter::once(row)).collect(),
                        ))
                    }
                    _ => {
                        let _ = skip_to_end(reader, other);
                        Ok((caption, columns, rows))
                    }
                }
            } else {
                let _ = skip_to_end(reader, other);
                Ok((caption, columns, rows))
            }
        }
    }
}

fn finish_table(
    caption: Option<String>,
    columns: Vec<String>,
    rows: Vec<Row>,
) -> Result<Table, XsdValidationError> {
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
    parse_columns_events(reader, Vec::new())
}

fn parse_columns_events(
    reader: &mut Reader<&[u8]>,
    acc: Vec<String>,
) -> Result<Vec<String>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"column" => {
            let text = read_text_until_end(reader, b"column")?;
            parse_columns_events(
                reader,
                acc.into_iter().chain(std::iter::once(text)).collect(),
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"columns" => Ok(acc),
        Ok(Event::Eof) | Err(_) => Ok(acc),
        Ok(_) => parse_columns_events(reader, acc),
    }
}

/// Parse a table row
fn parse_row(reader: &mut Reader<&[u8]>) -> Result<Row, XsdValidationError> {
    let cells = parse_row_cells(reader, Vec::new())?;

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

fn parse_row_cells(
    reader: &mut Reader<&[u8]>,
    acc: Vec<TableCell>,
) -> Result<Vec<TableCell>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"cell" => {
            let inner = read_inner_xml(reader, b"cell")?;
            let cell = TableCell {
                content: parse_inline_elements(&inner),
            };
            parse_row_cells(
                reader,
                acc.into_iter().chain(std::iter::once(cell)).collect(),
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"row" => Ok(acc),
        Ok(Event::Eof) | Err(_) => Ok(acc),
        Ok(_) => parse_row_cells(reader, acc),
    }
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
    parse_summary_events(reader, original_tag, canonical_tag, None, Vec::new())
}

fn parse_summary_events(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
    canonical_tag: &[u8],
    context: Option<String>,
    scope_items: Vec<ScopeItem>,
) -> Result<PlanSummary, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let (new_context, new_scope_items) =
                parse_summary_element(reader, e, context, scope_items)?;
            parse_summary_events(reader, original_tag, canonical_tag, new_context, new_scope_items)
        }
        Ok(Event::End(e))
            if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag =>
        {
            finish_summary(context, scope_items)
        }
        Ok(Event::Eof) => finish_summary(context, scope_items),
        Ok(Event::Text(_) | _) => {
            // Tolerant: skip stray text and other events
            parse_summary_events(reader, original_tag, canonical_tag, context, scope_items)
        }
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-summary".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

fn parse_summary_element(
    reader: &mut Reader<&[u8]>,
    e: quick_xml::events::BytesStart<'_>,
    context: Option<String>,
    scope_items: Vec<ScopeItem>,
) -> Result<(Option<String>, Vec<ScopeItem>), XsdValidationError> {
    match e.name().as_ref() {
        b"context" => {
            let text = read_text_until_end(reader, b"context")?;
            Ok((Some(text), scope_items))
        }
        b"scope-items" => {
            // Normal path: scope-items wrapper is present
            let wrapped = parse_scope_items(reader)?;
            Ok((context, scope_items.into_iter().chain(wrapped).collect()))
        }
        b"scope-item" => {
            // Tolerant: bare scope-item without scope-items wrapper.
            let attrs = get_attributes(&e);
            let description = read_text_until_end(reader, b"scope-item")?;
            let item = ScopeItem {
                description,
                count: attrs.get("count").cloned(),
                category: attrs.get("category").cloned(),
            };
            Ok((context, scope_items.into_iter().chain(std::iter::once(item)).collect()))
        }
        other => {
            // Tolerant: try fuzzy tag matching before skipping.
            let tag_name = String::from_utf8_lossy(other);
            if let Some(canonical) = normalize_tag_name(&tag_name, SECTION_SUB_ELEMENT_TAGS) {
                match canonical {
                    "context" => {
                        let text = read_text_until_end_fuzzy(reader, b"context", other)?;
                        Ok((Some(text), scope_items))
                    }
                    "scope-items" => {
                        let wrapped = parse_scope_items(reader)?;
                        Ok((context, scope_items.into_iter().chain(wrapped).collect()))
                    }
                    "scope-item" => {
                        let attrs = get_attributes(&e);
                        let description =
                            read_text_until_end_fuzzy(reader, b"scope-item", other)?;
                        let item = ScopeItem {
                            description,
                            count: attrs.get("count").cloned(),
                            category: attrs.get("category").cloned(),
                        };
                        Ok((context, scope_items.into_iter().chain(std::iter::once(item)).collect()))
                    }
                    _ => {
                        let _ = skip_to_end(reader, other);
                        Ok((context, scope_items))
                    }
                }
            } else {
                let _ = skip_to_end(reader, other);
                Ok((context, scope_items))
            }
        }
    }
}

fn finish_summary(
    context: Option<String>,
    scope_items: Vec<ScopeItem>,
) -> Result<PlanSummary, XsdValidationError> {
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
    parse_scope_items_events(reader, Vec::new())
}

fn parse_scope_items_events(
    reader: &mut Reader<&[u8]>,
    acc: Vec<ScopeItem>,
) -> Result<Vec<ScopeItem>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"scope-item" => {
            let attrs = get_attributes(&e);
            let description = read_text_until_end(reader, b"scope-item")?;
            let item = ScopeItem {
                description,
                count: attrs.get("count").cloned(),
                category: attrs.get("category").cloned(),
            };
            parse_scope_items_events(
                reader,
                acc.into_iter().chain(std::iter::once(item)).collect(),
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"scope-items" => Ok(acc),
        Ok(Event::Eof) | Err(_) => Ok(acc),
        Ok(_) => parse_scope_items_events(reader, acc),
    }
}
