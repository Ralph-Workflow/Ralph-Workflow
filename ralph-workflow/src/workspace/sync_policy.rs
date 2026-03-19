use std::io;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AtomicWriteSync {
    Full,
    SkipInterrupt,
}

pub fn decide_atomic_write_sync(interrupted: bool) -> AtomicWriteSync {
    if interrupted {
        AtomicWriteSync::SkipInterrupt
    } else {
        AtomicWriteSync::Full
    }
}

pub fn sync_temp_file(file: &std::fs::File, policy: AtomicWriteSync) -> io::Result<()> {
    match policy {
        AtomicWriteSync::Full => {
            file.sync_all()?;
            Ok(())
        }
        AtomicWriteSync::SkipInterrupt => Ok(()),
    }
}

#[cfg(unix)]
pub fn set_restrictive_permissions(path: &std::path::Path) -> io::Result<()> {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    let metadata = fs::metadata(path)?;
    let mut perms = metadata.permissions();
    perms.set_mode(0o600);
    fs::set_permissions(path, perms)
}

#[cfg(not(unix))]
pub fn set_restrictive_permissions(_path: &std::path::Path) -> io::Result<()> {
    Ok(())
}
