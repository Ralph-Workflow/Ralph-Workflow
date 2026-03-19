// Tool formatting.

impl OpenCodeParser {
    fn format_tool_event_header(
        tool_name: &str,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        // Status-specific icon and color based on ToolState variants from message-v2.ts
        // Statuses: "pending", "running", "completed", "error"
        let (icon, color) = match status {
            "completed" => (CHECK, c.green()),
            "error" => (CROSS, c.red()),
            "running" => ('►', c.cyan()),
            _ => ('…', c.yellow()), // "pending" or unknown
        };

        format!(
            "{}[{}]{} {}Tool{}: {}{}{} {}{}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.magenta(),
            c.reset(),
            c.bold(),
            tool_name,
            c.reset(),
            color,
            icon,
            c.reset()
        )
    }

    fn format_tool_title_to_string(
        &self,
        title: Option<&str>,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if let Some(t) = title {
            let limit = self.verbosity.truncate_limit("text");
            let preview = truncate_text(t, limit);
            format!(
                "{}[{}]{} {}  └─ {}{}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.dim(),
                preview,
                c.reset()
            )
        } else {
            String::new()
        }
    }

    fn format_tool_input_to_string(
        &self,
        part: &OpenCodePart,
        tool_name: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if !self.verbosity.show_tool_input() {
            return String::new();
        }

        if let Some(ref state) = part.state {
            if let Some(ref input_val) = state.input {
                let input_str = Self::format_tool_specific_input(tool_name, input_val);
                let limit = self.verbosity.truncate_limit("tool_input");
                let preview = truncate_text(&input_str, limit);
                if !preview.is_empty() {
                    return format!(
                        "{}[{}]{} {}  └─ {}{}\n",
                        c.dim(),
                        prefix,
                        c.reset(),
                        c.dim(),
                        preview,
                        c.reset()
                    );
                }
            }
        }
        String::new()
    }

    fn format_tool_error_to_string(
        &self,
        part: &OpenCodePart,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if status != "error" {
            return String::new();
        }

        if let Some(ref state) = part.state {
            if let Some(error_msg) = state.error.as_deref() {
                let limit = self.verbosity.truncate_limit("tool_result");
                let preview = truncate_text(error_msg, limit);
                return format!(
                    "{}[{}]{} {}  └─ {}Error:{} {}{}{}\n",
                    c.dim(),
                    prefix,
                    c.reset(),
                    c.red(),
                    c.bold(),
                    c.reset(),
                    c.red(),
                    preview,
                    c.reset()
                );
            }
        }
        String::new()
    }

    fn format_tool_output_to_string(
        &self,
        part: &OpenCodePart,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        if !self.verbosity.show_tool_input() || status != "completed" {
            return String::new();
        }

        if let Some(ref state) = part.state {
            if let Some(ref output_val) = state.output {
                let output_str = match output_val {
                    serde_json::Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                if !output_str.is_empty() {
                    let limit = self.verbosity.truncate_limit("tool_result");
                    return Self::format_tool_output_to_string_static(
                        &output_str,
                        limit,
                        prefix,
                        c,
                    );
                }
            }
        }
        String::new()
    }

    fn format_tool_output_to_string_static(
        output: &str,
        limit: usize,
        prefix: &str,
        c: crate::logger::Colors,
    ) -> String {
        use crate::config::truncation::MAX_OUTPUT_LINES;

        let lines: Vec<&str> = output.lines().collect();
        let is_multiline = lines.len() > 1;

        if !is_multiline {
            let preview = truncate_text(output, limit);
            if preview.is_empty() {
                return String::new();
            }
            return format!(
                "{}[{}]{} {}  └─ Output:{} {}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.cyan(),
                c.reset(),
                preview
            );
        }

        let indent = format!("{}[{}]{}     ", c.dim(), prefix, c.reset());
        let remaining = lines.len();

        let prefix_sums: Vec<usize> = (0..lines.len())
            .map(|i| lines[..i].iter().map(|l| l.len() + 1).sum())
            .collect();

        let result = [
            format!(
                "{}[{}]{} {}  └─ Output:{}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.cyan(),
                c.reset()
            ),
            lines
                .iter()
                .enumerate()
                .map(|(idx, line)| {
                    if idx >= MAX_OUTPUT_LINES {
                        let more = remaining - idx;
                        format!("{}{}...({} more lines)\n", indent, c.dim(), more)
                    } else {
                        let chars_used_before = prefix_sums[idx];
                        if chars_used_before + line.len() > limit {
                            let more = remaining - idx;
                            format!("{}{}...({} more lines)\n", indent, c.dim(), more)
                        } else {
                            format!("{}{}{}{}\n", indent, c.dim(), line, c.reset())
                        }
                    }
                })
                .collect::<Vec<_>>()
                .join(""),
        ]
        .join("");

        result
    }

    /// Format a `tool_use` event
    ///
    /// Based on `OpenCode` source (`run.ts` lines 163-174, `message-v2.ts` lines 221-287):
    /// - Shows tool name with status-specific icon and color
    /// - Status handling: pending (…), running (►), completed (✓), error (✗)
    /// - Title/description when available (from `state.title`)
    /// - Tool-specific input formatting based on tool type
    /// - Tool output/results shown at Normal+ verbosity
    /// - Error messages shown in red when status is "error"
    pub(super) fn format_tool_use_event(&self, event: &OpenCodeEvent) -> String {
        let c = self.colors;
        let prefix = &self.display_name;

        event.part.as_ref().map_or_else(String::new, |part| {
            let tool_name = part.tool.as_deref().unwrap_or("unknown");
            let status = part
                .state
                .as_ref()
                .and_then(|s| s.status.as_deref())
                .unwrap_or("pending");
            let title = part.state.as_ref().and_then(|s| s.title.as_deref());

            format!(
                "{}{}{}{}{}",
                Self::format_tool_event_header(tool_name, status, prefix, c),
                self.format_tool_title_to_string(title, prefix, c),
                self.format_tool_input_to_string(part, tool_name, prefix, c),
                self.format_tool_error_to_string(part, status, prefix, c),
                self.format_tool_output_to_string(part, status, prefix, c),
            )
        })
    }

    /// Format tool input based on tool type
    ///
    /// From `OpenCode` source, each tool has specific input fields:
    /// - `read`: `filePath`, `offset?`, `limit?`
    /// - `bash`: `command`, `timeout?`
    /// - `write`: `filePath`, `content`
    /// - `edit`: `filePath`, ...
    /// - `glob`: `pattern`, `path?`
    /// - `grep`: `pattern`, `path?`, `include?`
    /// - `fetch`: `url`, `format?`, `timeout?`
    pub(super) fn format_tool_specific_input(tool_name: &str, input: &serde_json::Value) -> String {
        let Some(obj) = input.as_object() else {
            return format_tool_input(input);
        };

        match tool_name {
            "read" | "view" => {
                let file_path = obj.get("filePath").and_then(|v| v.as_str()).unwrap_or("");
                let offset_part = obj
                    .get("offset")
                    .and_then(serde_json::Value::as_u64)
                    .map_or(String::new(), |o| format!(" (offset: {o})"));
                let limit_part = obj
                    .get("limit")
                    .and_then(serde_json::Value::as_u64)
                    .map_or(String::new(), |l| format!(" (limit: {l})"));
                format!("{file_path}{offset_part}{limit_part}")
            }
            "bash" => {
                // Primary: command
                obj.get("command")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string()
            }
            "write" => {
                // Primary: filePath (don't show content in summary)
                let file_path = obj.get("filePath").and_then(|v| v.as_str()).unwrap_or("");
                let content_len = obj
                    .get("content")
                    .and_then(|v| v.as_str())
                    .map_or(0, str::len);
                if content_len > 0 {
                    format!("{file_path} ({content_len} bytes)")
                } else {
                    file_path.to_string()
                }
            }
            "edit" => {
                // Primary: filePath
                obj.get("filePath")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string()
            }
            "glob" => {
                // Primary: pattern, optional: path
                let pattern = obj.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
                let path = obj.get("path").and_then(|v| v.as_str());
                path.map_or_else(|| pattern.to_string(), |p| format!("{pattern} in {p}"))
            }
            "grep" => {
                let pattern = obj.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
                let path_part = obj
                    .get("path")
                    .and_then(|v| v.as_str())
                    .map_or(String::new(), |p| format!(" in {p}"));
                let include_part = obj
                    .get("include")
                    .and_then(|v| v.as_str())
                    .map_or(String::new(), |i| format!(" ({i})"));
                format!("/{pattern}/{path_part}{include_part}")
            }
            "fetch" | "webfetch" => {
                // Primary: url, optional: format
                let url = obj.get("url").and_then(|v| v.as_str()).unwrap_or("");
                let format = obj.get("format").and_then(|v| v.as_str());
                format.map_or_else(|| url.to_string(), |f| format!("{url} ({f})"))
            }
            "todowrite" | "todoread" => {
                // Show count of todos if available
                obj.get("todos").and_then(|v| v.as_array()).map_or_else(
                    || format_tool_input(input),
                    |todos| format!("{} items", todos.len()),
                )
            }
            _ => {
                // Fallback to generic formatting
                format_tool_input(input)
            }
        }
    }
}
