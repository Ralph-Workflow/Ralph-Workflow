// Template parsing logic: tokenizing, comment stripping, and partial extraction.

#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
impl Template {
    /// Strip `{# comment #}` style comments from the content.
    ///
    /// Comments can span multiple lines. Handles line-only comments that leave
    /// empty lines behind by collapsing them.
    fn strip_comments(content: &str) -> String {
        let mut result = String::with_capacity(content.len());
        let bytes = content.as_bytes();

        let mut i = 0;
        while i < bytes.len() {
            // Check for {# comment start
            if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
                // Find the end of the comment (#})
                let comment_start = i;
                i += 2;
                while i + 1 < bytes.len() && !(bytes[i] == b'#' && bytes[i + 1] == b'}') {
                    i += 1;
                }
                if i + 1 < bytes.len() && bytes[i] == b'#' && bytes[i + 1] == b'}' {
                    i += 2;
                    // Skip trailing whitespace on the same line if comment was on its own line
                    // Check if we're at the end of a line (or there's only whitespace until newline)
                    let whitespace_start = i;
                    while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                        i += 1;
                    }
                    // If we hit a newline after whitespace, skip it too (comment was full line)
                    if i < bytes.len() && bytes[i] == b'\n' {
                        // Check if the line before the comment was also empty
                        let was_line_start = result.is_empty() || result.ends_with('\n');
                        if was_line_start {
                            // Comment was on its own line - skip the newline
                            i += 1;
                        } else {
                            // Comment was at end of a content line - restore whitespace position
                            i = whitespace_start;
                        }
                    } else if i < bytes.len() {
                        // Not a newline - restore whitespace position
                        i = whitespace_start;
                    }
                    continue;
                }
                // Unclosed comment - treat as literal text
                result.push_str(&content[comment_start..i]);
            } else {
                result.push(bytes[i] as char);
                i += 1;
            }
        }

        result
    }

    /// Extract all partial references from template content.
    ///
    /// Returns Vec of (`full_match`, `partial_name`) tuples in order of appearance.
    fn extract_partials(content: &str) -> Vec<(String, String)> {
        let mut partials = Vec::new();
        let bytes = content.as_bytes();

        let mut i = 0;
        while i < bytes.len().saturating_sub(2) {
            // Check for {{> pattern
            if bytes[i] == b'{' && bytes[i + 1] == b'{' && i + 2 < bytes.len() {
                let start = i;
                i += 2;

                // Skip whitespace after {{
                while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                    i += 1;
                }

                // Check for > character
                if i < bytes.len() && bytes[i] == b'>' {
                    i += 1;

                    // Skip whitespace after >
                    while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                        i += 1;
                    }

                    // Extract partial name until }}
                    let name_start = i;
                    while i < bytes.len()
                        && !(bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}')
                    {
                        i += 1;
                    }

                    if i < bytes.len()
                        && bytes[i] == b'}'
                        && i + 1 < bytes.len()
                        && bytes[i + 1] == b'}'
                    {
                        let end = i + 2;
                        let full_match = &content[start..end];
                        let name = &content[name_start..i];

                        let partial_name = name.trim().to_string();
                        if !partial_name.is_empty() {
                            partials.push((full_match.to_string(), partial_name));
                        }
                        i = end;
                        continue;
                    }
                }
            }
            i += 1;
        }

        partials
    }
}
