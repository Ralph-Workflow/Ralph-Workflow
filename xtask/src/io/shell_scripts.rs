use std::path::{Path, PathBuf};

pub fn scan_for_shell_scripts(
    dirs: &[PathBuf],
    excluded_dirs: &[PathBuf],
) -> (Vec<String>, Vec<String>) {
    dirs.iter().filter(|dir| dir.exists()).fold(
        (Vec::new(), Vec::new()),
        |(mut found, mut walk_errors), dir| {
            match collect_sh_files(dir, excluded_dirs) {
                Ok(mut files) => found.append(&mut files),
                Err(err) => {
                    walk_errors.push(format!("read_dir error for {}: {err}", dir.display()))
                }
            }
            (found, walk_errors)
        },
    )
}

fn collect_sh_files(dir: &Path, excluded_dirs: &[PathBuf]) -> std::io::Result<Vec<String>> {
    std::fs::read_dir(dir)?.try_fold(Vec::new(), |mut acc, entry| {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            if excluded_dirs.contains(&path) {
                return Ok(acc);
            }
            acc.extend(collect_sh_files(&path, excluded_dirs)?);
        } else if path.extension().and_then(|e| e.to_str()) == Some("sh") {
            acc.push(path.display().to_string());
        }
        Ok(acc)
    })
}
