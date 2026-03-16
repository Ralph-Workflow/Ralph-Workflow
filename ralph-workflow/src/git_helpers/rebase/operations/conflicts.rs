// Core rebase operations: conflicts.

/// Get a list of files that have merge conflicts.
///
/// This function queries libgit2's index to find all files that are
/// currently in a conflicted state.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_conflicted_files() -> io::Result<Vec<String>> {
    let repo = git2::Repository::discover(".").map_err(|e| git2_to_io_error(&e))?;
    get_conflicted_files_impl(&repo)
}

/// Implementation of `get_conflicted_files`.
fn get_conflicted_files_impl(repo: &git2::Repository) -> io::Result<Vec<String>> {
    let index = repo.index().map_err(|e| git2_to_io_error(&e))?;

    let mut conflicted_files = Vec::new();

    // Check if there are any conflicts
    if !index.has_conflicts() {
        return Ok(conflicted_files);
    }

    // Get the list of conflicted files
    let conflicts = index.conflicts().map_err(|e| git2_to_io_error(&e))?;

    for conflict in conflicts {
        let conflict = conflict.map_err(|e| git2_to_io_error(&e))?;
        // The conflict's `our` entry (stage 2) will have the path
        if let Some(our_entry) = conflict.our {
            if let Ok(path) = std::str::from_utf8(&our_entry.path) {
                let path_str = path.to_string();
                if !conflicted_files.contains(&path_str) {
                    conflicted_files.push(path_str);
                }
            }
        }
    }

    Ok(conflicted_files)
}

/// Extract conflict markers from a file.
///
/// This function reads a file and returns the conflict sections,
/// including both versions of the conflicted content.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_conflict_markers_for_file(path: &Path) -> io::Result<String> {
    use std::fs;
    use std::io::Read;

    let mut file = fs::File::open(path)?;
    let mut content = String::new();
    file.read_to_string(&mut content)?;

    // Extract conflict markers and their content
    let mut conflict_sections = Vec::new();
    let lines: Vec<&str> = content.lines().collect();
    let mut i = 0;

    while i < lines.len() {
        if lines[i].trim_start().starts_with("<<<<<<<") {
            // Found conflict start
            let mut section = Vec::new();
            section.push(lines[i]);

            i = i.saturating_add(1);
            // Collect "ours" version
            while i < lines.len() && !lines[i].trim_start().starts_with("=======") {
                section.push(lines[i]);
                i = i.saturating_add(1);
            }

            if i < lines.len() {
                section.push(lines[i]); // Add the ======= line
                i = i.saturating_add(1);
            }

            // Collect "theirs" version
            while i < lines.len() && !lines[i].trim_start().starts_with(">>>>>>>") {
                section.push(lines[i]);
                i = i.saturating_add(1);
            }

            if i < lines.len() {
                section.push(lines[i]); // Add the >>>>>>> line
                i = i.saturating_add(1);
            }

            conflict_sections.push(section.join("\n"));
        } else {
            i = i.saturating_add(1);
        }
    }

    if conflict_sections.is_empty() {
        Ok(String::new())
    } else {
        Ok(conflict_sections.join("\n\n"))
    }
}
