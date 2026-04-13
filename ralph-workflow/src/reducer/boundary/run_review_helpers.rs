// Issue snippet helpers for the review boundary.
// Split from run_review.rs to keep file size < 1000 lines.
// This file is included (not mod'd) from run_review.rs.

fn extract_issue_snippets(
    issues: &[String],
    workspace: &dyn crate::workspace::Workspace,
) -> Vec<XmlCodeSnippet> {
    let requests = crate::phases::review::snippet_domain::collect_issue_snippet_requests(
        issues,
        workspace.root(),
    );

    requests
        .into_iter()
        .filter_map(|request| {
            let content = workspace.read(Path::new(&request.file)).ok()?;
            let snippet = crate::phases::review::snippet_domain::extract_snippet_lines(
                &content,
                request.start,
                request.end,
            )?;
            Some(XmlCodeSnippet {
                file: request.file,
                line_start: request.start,
                line_end: request.end,
                content: snippet,
            })
        })
        .collect()
}
