use std::path::{Path, PathBuf};

pub fn scan_for_shell_scripts(dirs: &[PathBuf]) -> (Vec<String>, Vec<String>) {
    dirs.iter().filter(|dir| dir.exists()).fold(
        (Vec::new(), Vec::new()),
        |(mut found, mut walk_errors), dir| {
            match collect_sh_files(dir) {
                Ok(mut files) => found.append(&mut files),
                Err(err) => {
                    walk_errors.push(format!("read_dir error for {}: {err}", dir.display()))
                }
            }
            (found, walk_errors)
        },
    )
}

fn collect_sh_files(dir: &Path) -> std::io::Result<Vec<String>> {
    std::fs::read_dir(dir)?.try_fold(Vec::new(), |mut acc, entry| {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            acc.extend(collect_sh_files(&path)?);
        } else if path.extension().and_then(|e| e.to_str()) == Some("sh") {
            acc.push(path.display().to_string());
        }
        Ok(acc)
    })
}
