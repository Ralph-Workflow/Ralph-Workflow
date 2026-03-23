fn strip_thought_pattern_with_blank_line(content: &str, pattern: &str) -> Option<String> {
    let rest = content.strip_prefix(pattern)?;

    // Find the first blank line after the pattern
    if let Some(blank_line_pos) = rest.find("\n\n") {
        let remaining = rest[blank_line_pos + 2..].trim();
        if !remaining.is_empty() {
            // Check if it looks like a clean commit message
            if looks_like_commit_message_start(remaining) {
                return Some(remaining.to_string());
            }
            // Otherwise, update result to continue with aggressive filtering below
            return Some(remaining.to_string());
        }
    } else if let Some(single_newline) = rest.find('\n') {
        // If no double newline, try to skip to after the first single newline
        let after_newline = &rest[single_newline + 1..];
        // Check if what follows looks like a commit message
        if looks_like_commit_message_start(after_newline.trim()) {
            return Some(after_newline.to_string());
        }
        // If not, check if the rest starts with numbered analysis
        if after_newline.trim().starts_with("1. ")
            || after_newline.trim().starts_with("1. **")
            || after_newline.trim().starts_with("- ")
        {
            return Some(after_newline.trim().to_string());
        }
    }

    // If the remaining content after the pattern is all analysis (no valid commit), return empty
    let rest_trimmed = rest.trim();
    if looks_like_analysis_text(rest_trimmed)
        && find_conventional_commit_start(rest_trimmed).is_none()
    {
        return Some(String::new());
    }

    None
}

fn strip_numbered_analysis_patterns(content: &str) -> Option<String> {
    let result_lower = content.to_lowercase();
    let numbered_start_patterns = [
        "1. ",
        "1)\n",
        "- first",
        "- the first",
        "* first",
        "* the first",
    ];

    for pattern in &numbered_start_patterns {
        if result_lower.starts_with(pattern) || content.starts_with(pattern) {
            // Try to find the commit message by looking for conventional commit format
            if let Some(commit_start) = find_conventional_commit_start(content) {
                return Some(content[commit_start..].to_string());
            }
            // Fallback: look for a blank line after the analysis
            if let Some(blank_pos) = content.find("\n\n") {
                let after_analysis = &content[blank_pos + 2..];
                // Check if the content after looks like a real commit message
                if after_analysis.trim().starts_with(char::is_alphanumeric) {
                    return Some(after_analysis.to_string());
                }
            }
            return Some(content.to_string());
        }
    }

    None
}

fn strip_markdown_bold_analysis(content: &str) -> Option<String> {
    if starts_with_markdown_bold_analysis(content) {
        if let Some(commit_start) = find_conventional_commit_start(content) {
            return Some(content[commit_start..].to_string());
        }
        // Fallback: look for double newline after the analysis
        if let Some(blank_pos) = content.find("\n\n") {
            let after_analysis = &content[blank_pos + 2..];
            if after_analysis.trim().starts_with(char::is_alphanumeric) {
                return Some(after_analysis.to_string());
            }
        }
        return Some(content.to_string());
    }
    None
}

fn extract_commit_from_analysis(content: &str) -> Option<String> {
    if let Some(commit_start) = find_conventional_commit_start(content) {
        // Verify that the content before the commit looks like analysis
        let before_commit = &content[..commit_start];
        // Check multiple conditions to identify analysis:
        // 1. Contains multiple lines (analysis is typically multi-line)
        // 2. Either looks like analysis text OR contains common analysis patterns
        let is_analysis = before_commit.contains('\n')
            && (looks_like_analysis_text(before_commit)
                || before_commit.to_lowercase().contains("changes")
                || before_commit.to_lowercase().contains("diff")
                || before_commit.contains("1.")
                || before_commit.contains("- "));

        if is_analysis {
            return Some(content[commit_start..].to_string());
        }
    }
    None
}

fn filter_analysis_with_embedded_types(content: &str) -> Option<String> {
    if !looks_like_analysis_text(content) {
        return None;
    }

    // Check if there's markdown-bold type mention embedded in analysis text
    // like "This is a **refactor**..." which indicates analysis, not a commit
    let result_lower = content.to_lowercase();
    if result_lower.contains("**feat**")
        || result_lower.contains("**fix**")
        || result_lower.contains("**refactor**")
        || result_lower.contains("**chore**")
        || result_lower.contains("**test**")
        || result_lower.contains("**docs**")
        || result_lower.contains("**perf**")
        || result_lower.contains("**style**")
    {
        // Look for the pattern "**type**:" (with colon) which indicates
        // it might be an actual commit message in markdown format
        if result_lower.contains("**feat**:")
            || result_lower.contains("**fix**:")
            || result_lower.contains("**refactor**:")
            || result_lower.contains("**chore**:")
            || result_lower.contains("**test**:")
            || result_lower.contains("**docs**:")
            || result_lower.contains("**perf**:")
            || result_lower.contains("**style**:")
        {
            // This might be a valid commit message in markdown, keep it
            return Some(content.to_string());
        }
        // Otherwise, it's analysis with embedded type mentions, filter it out
        return Some(String::new());
    }

    // If no conventional commit was found and it looks like analysis, return empty
    if find_conventional_commit_start(content).is_none() {
        return Some(String::new());
    }

    None
}

#[must_use] 
pub fn remove_thought_process_patterns(content: &str) -> String {
    let mut result = content.to_string();

    // Remove AI thought process prefixes
    let thought_patterns = [
        "Looking at this diff, I can see",
        "Looking at this diff",
        "I can see",
        "The main changes are",
        "The main changes I see are:",
        "The main changes I see are",
        "Several distinct categories of changes",
        "Key categories of changes",
        "Based on the diff",
        "Analyzing the changes",
        "This diff shows",
        "Looking at the changes",
        "I've analyzed",
        "After reviewing",
        "Based on the git diff",
        "Based on the git diff, here are the changes",
        "Based on the git diff, here's what changed",
        "Based on the git diff, the following changes",
        "Here are the changes",
        "Here's what changed",
        "Here is what changed",
        "The following changes",
        "The changes include",
        "Changes include",
        "After reviewing the diff",
        "After reviewing the changes",
        "After analyzing the diff",
        "After analyzing the changes",
        "I've analyzed the changes",
        "I've analyzed the diff",
        "Looking at the changes, I can see",
        "Key changes include",
        "Several changes include",
        "This diff shows the following",
        "The most substantive change is",
        "The most substantive changes are",
        "The most substantive user-facing change is",
    ];

    for pattern in &thought_patterns {
        if let Some(new_result) = strip_thought_pattern_with_blank_line(&result, pattern) {
            result = new_result;
            break;
        }
    }

    // Remove numbered analysis patterns
    if let Some(new_result) = strip_numbered_analysis_patterns(&result) {
        return new_result;
    }

    // Remove markdown bold analysis patterns
    if let Some(new_result) = strip_markdown_bold_analysis(&result) {
        return new_result;
    }

    // Additional aggressive filtering: detect analysis ending with conventional commit
    if let Some(new_result) = extract_commit_from_analysis(&result) {
        return new_result;
    }

    // Final check: filter analysis with embedded type mentions
    if let Some(new_result) = filter_analysis_with_embedded_types(&result) {
        return new_result;
    }

    result
}

fn starts_with_markdown_bold_analysis(text: &str) -> bool {
    let trimmed = text.trim();

    // Check for patterns like "1. **Category**" or "**Category**:"
    // These are markdown bold patterns used for analysis headers
    let lines: Vec<&str> = trimmed.lines().collect();

    if lines.is_empty() {
        return false;
    }

    // Check the first line for markdown bold patterns
    let first_line = lines[0].trim();

    // Pattern 1: "1. **Bold Text**" or "1. **Bold Text** (file.rs): description"
    if first_line.starts_with("1. **") || first_line.starts_with("1. **") {
        return true;
    }

    // Pattern 2: Line starts with ** (markdown bold opening)
    if first_line.starts_with("**") {
        // Check if it looks like a header/analysis, not a valid commit message
        // Valid commits don't start with **, but analysis headers do
        return true;
    }

    // Pattern 3: Check if first few lines contain markdown bold patterns
    // like "**Category**:" which indicates analysis breakdown
    if lines.len() >= 2 {
        let mut bold_header_count: usize = 0;
        for line in lines.iter().take(5) {
            let trimmed = line.trim();
            // Check for patterns like "**Category**:" or "**Category** (file):"
            if (trimmed.contains("**") && trimmed.contains("**:"))
                || (trimmed.contains("**") && trimmed.contains("** ("))
            {
                bold_header_count = bold_header_count.saturating_add(1);
            }
        }
        if bold_header_count >= 1 {
            return true;
        }
    }

    false
}

fn looks_like_commit_message_start(text: &str) -> bool {
    let trimmed = text.trim();
    let conventional_types = [
        "feat", "fix", "chore", "docs", "test", "refactor", "perf", "style", "build", "ci",
        "revert",
    ];

    for commit_type in &conventional_types {
        // Check for "type:" or "type(scope):" pattern
        if let Some(rest) = trimmed.strip_prefix(commit_type) {
            if rest.starts_with(':')
                || (rest.starts_with('(') && rest[1..].contains("):"))
                || (rest.starts_with('(') && rest[1..].contains("): "))
            {
                return true;
            }
        }
    }

    false
}

#[must_use] 
pub fn find_conventional_commit_start(text: &str) -> Option<usize> {
    let conventional_types = [
        "feat", "fix", "chore", "docs", "test", "refactor", "perf", "style", "build", "ci",
        "revert",
    ];

    // Look for each commit type pattern
    for commit_type in &conventional_types {
        let mut search_pos = 0;
        while search_pos < text.len() {
            if let Some(pos) = text[search_pos..].find(commit_type) {
                let actual_pos = search_pos + pos;
                let rest = &text[actual_pos + commit_type.len()..];

                // Check if this is a valid conventional commit pattern
                if rest.starts_with(':') || (rest.starts_with('(') && rest[1..].contains("):")) {
                    // Make sure it's at the start of a line or preceded by newline
                    let prefix = &text[..actual_pos];
                    if prefix.is_empty() || prefix.ends_with('\n') {
                        return Some(actual_pos);
                    }
                }
                search_pos = actual_pos + commit_type.len();
            } else {
                break;
            }
        }
    }

    None
}

#[must_use] 
pub fn looks_like_analysis_text(text: &str) -> bool {
    let text_lower = text.to_lowercase();

    // Check for analysis indicator phrases
    let analysis_indicators = [
        "looking at",
        "analyzing",
        "the changes",
        "the change",
        "the diff",
        "i can see",
        "here are",
        "based on",
        "after reviewing",
        "first change",
        "second change",
        "third change",
        // Additional patterns to catch more variations
        "here are the changes",
        "based on the git diff",
        "based on the diff",
        "the following changes",
        "changes include",
        "here's what changed",
        "here is what changed",
        "after reviewing the diff",
        "after reviewing the changes",
        "after analyzing",
        "this diff shows",
        "i've analyzed the changes",
        "i've analyzed",
        "looking at the changes",
        "key changes",
        "several changes",
        "distinct changes",
        "key categories of changes",
        "several categories of changes",
        "user-facing change",
    ];

    for indicator in &analysis_indicators {
        if text_lower.contains(indicator) {
            return true;
        }
    }

    // Check for numbered/bullet list patterns
    let lines: Vec<&str> = text.lines().collect();
    if lines.len() >= 2 {
        let mut numbered_count: usize = 0;
        for line in &lines {
            let trimmed = line.trim();
            if trimmed.starts_with("1. ")
                || trimmed.starts_with("2. ")
                || trimmed.starts_with("3. ")
                || trimmed.starts_with("- ")
                || trimmed.starts_with("* ")
            {
                numbered_count = numbered_count.saturating_add(1);
            }
        }
        if numbered_count >= 2 {
            return true;
        }
    }

    false
}
