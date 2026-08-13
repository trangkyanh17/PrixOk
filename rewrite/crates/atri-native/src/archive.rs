use flate2::read::GzDecoder;
use serde::{Deserialize, Serialize};
use std::{
    fs::File,
    io::{Read, Seek},
    path::{Component, Path},
};
use tar::Archive as TarArchive;
use thiserror::Error;
use zip::ZipArchive;

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct ArchiveLimits {
    pub max_entries: usize,
    pub max_total_uncompressed: u64,
    pub max_single_file: u64,
    pub max_ratio: u64,
}

impl Default for ArchiveLimits {
    fn default() -> Self {
        Self {
            max_entries: 1_500,
            max_total_uncompressed: 160 * 1024 * 1024,
            max_single_file: 48 * 1024 * 1024,
            max_ratio: 250,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ArchiveEntry {
    pub path: String,
    pub bytes: u64,
    pub directory: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ArchiveInspection {
    pub format: String,
    pub entries: Vec<ArchiveEntry>,
    pub total_uncompressed: u64,
}

#[derive(Debug, Error)]
pub enum ArchiveError {
    #[error("unsupported archive format")]
    Unsupported,
    #[error("archive limit exceeded")]
    Limit,
    #[error("unsafe archive path: {0}")]
    UnsafePath(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Zip(#[from] zip::result::ZipError),
}

fn safe_path(name: &str) -> Result<String, ArchiveError> {
    let normalized = name.replace('\\', "/");
    let path = Path::new(&normalized);
    if path.is_absolute()
        || path
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::Prefix(_)))
    {
        return Err(ArchiveError::UnsafePath(name.to_string()));
    }
    Ok(normalized.trim_start_matches("./").to_string())
}

fn enforce(
    limits: ArchiveLimits,
    count: usize,
    total: u64,
    single: u64,
) -> Result<(), ArchiveError> {
    if count > limits.max_entries
        || total > limits.max_total_uncompressed
        || single > limits.max_single_file
    {
        return Err(ArchiveError::Limit);
    }
    Ok(())
}

fn inspect_zip<R: Read + Seek>(
    reader: R,
    limits: ArchiveLimits,
) -> Result<ArchiveInspection, ArchiveError> {
    let mut archive = ZipArchive::new(reader)?;
    let mut entries = Vec::new();
    let mut total = 0u64;
    for index in 0..archive.len() {
        let file = archive.by_index(index)?;
        let size = file.size();
        total = total.saturating_add(size);
        enforce(limits, index + 1, total, size)?;
        let compressed = file.compressed_size().max(1);
        if size / compressed > limits.max_ratio {
            return Err(ArchiveError::Limit);
        }
        entries.push(ArchiveEntry {
            path: safe_path(file.name())?,
            bytes: size,
            directory: file.is_dir(),
        });
    }
    Ok(ArchiveInspection {
        format: "zip".into(),
        entries,
        total_uncompressed: total,
    })
}

fn inspect_tar<R: Read>(
    reader: R,
    format: &str,
    limits: ArchiveLimits,
) -> Result<ArchiveInspection, ArchiveError> {
    let mut archive = TarArchive::new(reader);
    let mut entries = Vec::new();
    let mut total = 0u64;
    for (index, entry) in archive.entries()?.enumerate() {
        let entry = entry?;
        let size = entry.size();
        total = total.saturating_add(size);
        enforce(limits, index + 1, total, size)?;
        let path = entry.path()?.to_string_lossy().to_string();
        entries.push(ArchiveEntry {
            path: safe_path(&path)?,
            bytes: size,
            directory: entry.header().entry_type().is_dir(),
        });
    }
    Ok(ArchiveInspection {
        format: format.into(),
        entries,
        total_uncompressed: total,
    })
}

pub fn inspect_archive(
    path: impl AsRef<Path>,
    limits: ArchiveLimits,
) -> Result<ArchiveInspection, ArchiveError> {
    let path = path.as_ref();
    let name = path
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if name.ends_with(".zip") {
        return inspect_zip(File::open(path)?, limits);
    }
    if name.ends_with(".tar.gz") || name.ends_with(".tgz") {
        return inspect_tar(GzDecoder::new(File::open(path)?), "tar.gz", limits);
    }
    if name.ends_with(".tar") {
        return inspect_tar(File::open(path)?, "tar", limits);
    }
    Err(ArchiveError::Unsupported)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_traversal() {
        assert!(safe_path("../etc/passwd").is_err());
    }
}
