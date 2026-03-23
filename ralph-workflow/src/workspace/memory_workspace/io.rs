//! In-memory Workspace implementation.
//
// This module implements all Workspace trait methods for the in-memory
// workspace, including file operations, directory operations, and metadata access.

use super::{MemoryFile, MemoryWorkspace};
use crate::workspace::{DirEntry, Workspace};
use std::io;
use std::path::Path;

impl Workspace for MemoryWorkspace {
    fn root(&self) -> &Path {
        &self.root
    }

    fn read(&self, relative: &Path) -> io::Result<String> {
        self.files
            .read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .get(relative)
            .map(|f| String::from_utf8_lossy(&f.content).to_string())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("File not found: {}", relative.display()),
                )
            })
    }

    fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
        self.files
            .read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .get(relative)
            .map(|f| f.content.clone())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("File not found: {}", relative.display()),
                )
            })
    }

    fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.ensure_parent_dirs(relative);
        self.files.write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .insert(
                relative.to_path_buf(),
                MemoryFile::new(content.as_bytes().to_vec()),
            );
        Ok(())
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.ensure_parent_dirs(relative);
        self.files
            .write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .insert(relative.to_path_buf(), MemoryFile::new(content.to_vec()));
        Ok(())
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.ensure_parent_dirs(relative);
        {
            let mut files = self.files.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock");
            let entry = files
                .entry(relative.to_path_buf())
                .or_insert_with(|| MemoryFile::new(Vec::new()));
            entry.content.extend_from_slice(content);
            entry.modified = std::time::SystemTime::now();
            drop(files);
        }
        Ok(())
    }

    fn exists(&self, relative: &Path) -> bool {
        self.files.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .contains_key(relative)
            || self.directories.read()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock")
                .contains(relative)
    }

    fn is_file(&self, relative: &Path) -> bool {
        self.files.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .contains_key(relative)
    }

    fn is_dir(&self, relative: &Path) -> bool {
        self.directories.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock")
            .contains(relative)
    }

    fn remove(&self, relative: &Path) -> io::Result<()> {
        self.files
            .write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .remove(relative)
            .map(|_| ())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("File not found: {}", relative.display()),
                )
            })
    }

    fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.files.write()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock")
            .remove(relative);
        Ok(())
    }

    fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
        self.ensure_dir_path(relative);
        Ok(())
    }

    fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
        self.write(relative, content)
    }

    fn set_readonly(&self, _relative: &Path) -> io::Result<()> {
        Ok(())
    }

    fn set_writable(&self, _relative: &Path) -> io::Result<()> {
        Ok(())
    }

    fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
        if !self.directories.read()
            .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock")
            .contains(relative) {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("Directory not found: {}", relative.display()),
            ));
        }
        self.remove_dir_all_impl(relative);
        Ok(())
    }

    fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
        self.remove_dir_all_impl(relative);
        Ok(())
    }

    fn read_dir(&self, relative: &Path) -> io::Result<Vec<DirEntry>> {
        let (file_entries, dir_entries) = {
            let files = self.files.read()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock");
            let dirs = self.directories.read()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock");

            if !relative.as_os_str().is_empty() && !dirs.contains(relative) {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("Directory not found: {}", relative.display()),
                ));
            }

            let file_entries: Vec<_> = files
                .iter()
                .filter_map(|(path, mem_file)| {
                    path.parent().filter(|p| *p == relative).and_then(|_| {
                        path.file_name()
                            .map(|name| (name.to_os_string(), path.clone(), mem_file.modified))
                    })
                })
                .collect();
            drop(files);

            let dir_entries: Vec<_> = dirs
                .iter()
                .filter_map(|dir_path| {
                    dir_path.parent().filter(|p| *p == relative).and_then(|_| {
                        dir_path
                            .file_name()
                            .map(|name| (name.to_os_string(), dir_path.clone()))
                    })
                })
                .collect();
            drop(dirs);
            (file_entries, dir_entries)
        };

        let entries: Vec<DirEntry> = file_entries
            .into_iter()
            .fold(
                (Vec::new(), std::collections::HashSet::new()),
                |(mut results, mut seen), (name, path, modified)| {
                    if seen.insert(name) {
                        results.push(DirEntry::with_modified(path, true, false, modified));
                    }
                    (results, seen)
                },
            )
            .0
            .into_iter()
            .chain(
                dir_entries
                    .into_iter()
                    .fold(
                        (Vec::new(), std::collections::HashSet::new()),
                        |(mut results, mut seen), (name, path)| {
                            if seen.insert(name) {
                                results.push(DirEntry::new(path, false, true));
                            }
                            (results, seen)
                        },
                    )
                    .0,
            )
            .collect();

        Ok(entries)
    }

    fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
        self.ensure_parent_dirs(to);
        {
            let mut files = self.files.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock");
            if let Some(file) = files.remove(from) {
                files.insert(to.to_path_buf(), file);
                drop(files);
                return Ok(());
            }
        }
        Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("File not found: {}", from.display()),
        ))
    }
}

impl MemoryWorkspace {
    fn remove_dir_all_impl(&self, relative: &Path) {
        {
            let mut files = self.files.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace files lock");
            files.retain(|path, _| !path.starts_with(relative));
        }
        {
            let mut dirs = self.directories.write()
                .expect("RwLock poisoned - indicates panic in another thread holding MemoryWorkspace directories lock");
            dirs.retain(|path| !path.starts_with(relative) && path != relative);
        }
    }
}
