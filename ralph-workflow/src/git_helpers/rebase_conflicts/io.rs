// git_helpers/rebase_conflicts/io.rs — boundary module for core rebase operations: conflicts.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

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

    // Check if there are any conflicts
    if !index.has_conflicts() {
        return Ok(Vec::new());
    }

    // Get the list of conflicted files
    let conflicts = index.conflicts().map_err(|e| git2_to_io_error(&e))?;

    Ok(conflicts
        .filter_map(|conflict| {
            conflict.ok().and_then(|c| c.our).and_then(|our_entry| {
                std::str::from_utf8(&our_entry.path)
                    .ok()
                    .map(|path| path.to_string())
            })
        })
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .collect::<Vec<_>>())
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

    let mut file = fs::File::open(path)?;
    let mut content = String::new();
    std::io::Read::read_to_string(&mut file, &mut content)?;

    let lines: Vec<&str> = content.lines().collect();

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum ParseState {
        Normal,
        InConflict,
        InOurs,
        InTheirs,
    }

    let conflict_sections: Vec<String> = lines
        .iter()
        .enumerate()
        .scan(
            (ParseState::Normal, Vec::new()),
            |(state, section), (_i, line)| {
                let trimmed = line.trim_start();
                match (
                    *state,
                    trimmed.starts_with("<<<<<<<"),
                    trimmed.starts_with("======="),
                    trimmed.starts_with(">>>>>>>"),
                ) {
                    (_, true, _, _) => {
                        *state = ParseState::InConflict;
                        section.clear();
                        section.push(*line);
                        None
                    }
                    (ParseState::InConflict, _, true, _) => {
                        *state = ParseState::InOurs;
                        section.push(*line);
                        None
                    }
                    (ParseState::InOurs, _, _, true) => {
                        *state = ParseState::InTheirs;
                        section.push(*line);
                        None
                    }
                    (ParseState::InTheirs, _, _, true) => {
                        *state = ParseState::Normal;
                        section.push(*line);
                        let result = Some(section.join("\n"));
                        section.clear();
                        result
                    }
                    (
                        ParseState::InConflict | ParseState::InOurs | ParseState::InTheirs,
                        _,
                        _,
                        _,
                    ) => {
                        section.push(*line);
                        None
                    }
                    _ => None,
                }
            },
        )
        .filter_map(Some)
        .collect::<Vec<_>>();

    if conflict_sections.is_empty() {
        Ok(String::new())
    } else {
        Ok(conflict_sections.join("\n\n"))
    }
}
