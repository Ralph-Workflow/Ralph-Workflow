use std::fs;
use std::io::Write;
use std::path::Path;

pub(super) fn append_to_file(path: &str, msg: &str) -> std::io::Result<()> {
    if let Some(parent) = Path::new(path).parent() {
        fs::create_dir_all(parent)?;
    }

    let msg_with_newline = format!("{msg}\n");
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;

    file.write_all(msg_with_newline.as_bytes())?;
    file.flush()?;
    file.sync_all()
}
