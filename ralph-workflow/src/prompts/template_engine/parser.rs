// Template parsing logic: tokenizing, comment stripping, and partial extraction.

impl Template {
    /// Strip `{# comment #}` style comments from the content.
    ///
    /// Comments can span multiple lines. Handles line-only comments that leave
    /// empty lines behind by collapsing them.
    fn strip_comments(content: &str) -> String {
        let bytes = content.as_bytes();

        let comments: Vec<(usize, usize)> = bytes
            .windows(2)
            .enumerate()
            .filter_map(|(pos, window)| {
                if window[0] == b'{' && window[1] == b'#' {
                    let start = pos;
                    let rest = &bytes[pos + 2..];
                    rest.windows(2)
                        .position(|w| w[0] == b'#' && w[1] == b'}')
                        .map(|p| (start, pos + 2 + p + 2))
                } else {
                    None
                }
            })
            .collect();

        fn collapse_newlines(s: &str) -> String {
            if s.contains("\n\n\n") {
                collapse_newlines(&s.replace("\n\n\n", "\n\n"))
            } else {
                s.to_string()
            }
        }

        let positions: Vec<usize> = std::iter::once(0)
            .chain(comments.iter().flat_map(|&(s, e)| [s, e]))
            .chain(std::iter::once(content.len()))
            .collect();

        let segments: Vec<&str> = positions.windows(2).map(|w| &content[w[0]..w[1]]).collect();

        collapse_newlines(&segments.join(""))
    }

    /// Extract all partial references from template content.
    ///
    /// Returns Vec of (`full_match`, `partial_name`) tuples in order of appearance.
    fn extract_partials(content: &str) -> Vec<(String, String)> {
        let bytes = content.as_bytes();

        bytes
            .windows(3)
            .enumerate()
            .filter_map(|(pos, window)| {
                if window[0] == b'{' && window[1] == b'{' && window[2] == b'>' {
                    let start = pos;
                    let rest = &bytes[pos + 3..];

                    // Skip whitespace
                    let ws_end = rest
                        .iter()
                        .position(|&b| b != b' ' && b != b'\t')
                        .unwrap_or(rest.len());
                    let name_start = pos + 3 + ws_end;
                    let rest = &rest[ws_end..];

                    // Find closing }}
                    let close_pos = rest.windows(2).position(|w| w[0] == b'}' && w[1] == b'}');

                    close_pos.and_then(|end_offset| {
                        let end = name_start + end_offset + 2;
                        let full_match = &content[start..end];
                        let name = &content[name_start..end - 2];

                        let partial_name = name.trim().to_string();
                        if partial_name.is_empty() {
                            None
                        } else {
                            Some((full_match.to_string(), partial_name))
                        }
                    })
                } else {
                    None
                }
            })
            .collect()
    }
}
