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

    fn append_tool_title(
        &self,
        out: &mut String,
        title: Option<&str>,
        prefix: &str,
        c: crate::logger::Colors,
    ) {
        if let Some(t) = title {
            let limit = self.verbosity.truncate_limit("text");
            let preview = truncate_text(t, limit);
            let _ = writeln!(
                out,
                "{}[{}]{} {}  └─ {}{}",
                c.dim(),
                prefix,
                c.reset(),
                c.dim(),
                preview,
                c.reset()
            );
        }
    }

    fn append_tool_input(
        &self,
        out: &mut String,
        part: &OpenCodePart,
        tool_name: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) {
        if !self.verbosity.show_tool_input() {
            return;
        }

        if let Some(ref state) = part.state {
            if let Some(ref input_val) = state.input {
                let input_str = Self::format_tool_specific_input(tool_name, input_val);
                let limit = self.verbosity.truncate_limit("tool_input");
                let preview = truncate_text(&input_str, limit);
                if !preview.is_empty() {
                    let _ = writeln!(
                        out,
                        "{}[{}]{} {}  └─ {}{}",
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
    }

    fn append_tool_error(
        &self,
        out: &mut String,
        part: &OpenCodePart,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) {
        if status != "error" {
            return;
        }

        if let Some(ref state) = part.state {
            if let Some(error_msg) = state.error.as_deref() {
                let limit = self.verbosity.truncate_limit("tool_result");
                let preview = truncate_text(error_msg, limit);
                let _ = writeln!(
                    out,
                    "{}[{}]{} {}  └─ {}Error:{} {}{}{}",
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
    }

    fn append_tool_output(
        &self,
        out: &mut String,
        part: &OpenCodePart,
        status: &str,
        prefix: &str,
        c: crate::logger::Colors,
    ) {
        if !self.verbosity.show_tool_input() || status != "completed" {
            return;
        }

        if let Some(ref state) = part.state {
            if let Some(ref output_val) = state.output {
                let output_str = match output_val {
                    serde_json::Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                if !output_str.is_empty() {
                    let limit = self.verbosity.truncate_limit("tool_result");
                    Self::format_tool_output(out, &output_str, limit, prefix, c);
                }
            }
        }
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

            let mut out = Self::format_tool_event_header(tool_name, status, prefix, c);
            self.append_tool_title(&mut out, title, prefix, c);
            self.append_tool_input(&mut out, part, tool_name, prefix, c);
            self.append_tool_error(&mut out, part, status, prefix, c);
            self.append_tool_output(&mut out, part, status, prefix, c);
            out
        })
    }

    /// Format tool output with proper multi-line handling
    ///
    /// For single-line outputs, shows inline. For multi-line outputs (like file contents),
    /// shows only the first few lines as a preview.
    pub(super) fn format_tool_output(
        out: &mut String,
        output: &str,
        limit: usize,
        prefix: &str,
        c: crate::logger::Colors,
    ) {
        use crate::config::truncation::MAX_OUTPUT_LINES;

        let lines: Vec<&str> = output.lines().collect();
        let is_multiline = lines.len() > 1;

        if is_multiline {
            // Multi-line output: show header then first few lines
            let _ = writeln!(
                out,
                "{}[{}]{} {}  └─ Output:{}",
                c.dim(),
                prefix,
                c.reset(),
                c.cyan(),
                c.reset()
            );

            let mut chars_used = 0;
            let indent = format!("{}[{}]{}     ", c.dim(), prefix, c.reset());

            for (lines_shown, line) in lines.iter().enumerate() {
                // Stop if we've shown enough lines OR exceeded char limit
                if lines_shown >= MAX_OUTPUT_LINES || chars_used + line.len() > limit {
                    let remaining = lines.len() - lines_shown;
                    if remaining > 0 {
                        let _ = writeln!(out, "{}{}...({} more lines)", indent, c.dim(), remaining);
                    }
                    break;
                }
                let _ = writeln!(out, "{}{}{}{}", indent, c.dim(), line, c.reset());
                chars_used += line.len() + 1;
            }
        } else {
            // Single-line output: show inline
            let preview = truncate_text(output, limit);
            if !preview.is_empty() {
                let _ = writeln!(
                    out,
                    "{}[{}]{} {}  └─ Output:{} {}",
                    c.dim(),
                    prefix,
                    c.reset(),
                    c.cyan(),
                    c.reset(),
                    preview
                );
            }
        }
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
                // Primary: filePath, optional: offset, limit
                let file_path = obj.get("filePath").and_then(|v| v.as_str()).unwrap_or("");
                let mut result = file_path.to_string();
                if let Some(offset) = obj.get("offset").and_then(serde_json::Value::as_u64) {
                    let _ = write!(result, " (offset: {offset})");
                }
                if let Some(limit) = obj.get("limit").and_then(serde_json::Value::as_u64) {
                    let _ = write!(result, " (limit: {limit})");
                }
                result
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
                // Primary: pattern, optional: path, include
                let pattern = obj.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
                let mut result = format!("/{pattern}/");
                if let Some(path) = obj.get("path").and_then(|v| v.as_str()) {
                    let _ = write!(result, " in {path}");
                }
                if let Some(include) = obj.get("include").and_then(|v| v.as_str()) {
                    let _ = write!(result, " ({include})");
                }
                result
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
