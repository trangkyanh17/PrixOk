use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs::{self, File}, io::Read, path::Path};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DirEntryInfo {
    pub name: String,
    pub bytes: u64,
    pub is_dir: bool,
}

pub fn hash_file_sha256(path: impl AsRef<Path>) -> Result<String> {
    let path = path.as_ref();
    let mut file = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let mut digest = Sha256::new();
    let mut buf = [0u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buf)?;
        if read == 0 { break; }
        digest.update(&buf[..read]);
    }
    Ok(hex::encode(digest.finalize()))
}

pub fn list_dir_bounded(path: impl AsRef<Path>, max_entries: usize) -> Result<Vec<DirEntryInfo>> {
    let mut out = Vec::new();
    for entry in fs::read_dir(path)?.take(max_entries) {
        let entry = entry?;
        let metadata = entry.metadata()?;
        out.push(DirEntryInfo {
            name: entry.file_name().to_string_lossy().into_owned(),
            bytes: if metadata.is_file() { metadata.len() } else { 0 },
            is_dir: metadata.is_dir(),
        });
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn hashes_file() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("a.txt");
        fs::write(&path, b"abc").unwrap();
        assert_eq!(hash_file_sha256(&path).unwrap(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }
}
