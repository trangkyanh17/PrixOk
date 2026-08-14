use anyhow::Result;
use regex::Regex;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    sync::OnceLock,
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Chunk {
    pub path: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoreRequest {
    pub chat_key: String,
    pub filename: String,
    pub sha256: String,
    pub chunks: Vec<Chunk>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StoreResult {
    pub artifact_ref: String,
    pub chunk_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SearchHit {
    pub artifact_ref: String,
    pub filename: String,
    pub path: String,
    pub content: String,
    pub score: f64,
}

pub struct ArtifactIndex {
    db_path: PathBuf,
}

impl ArtifactIndex {
    pub fn new(path: impl AsRef<Path>) -> Result<Self> {
        let db_path = path.as_ref().to_path_buf();
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let this = Self { db_path };
        let conn = this.connect()?;
        conn.execute_batch("CREATE TABLE IF NOT EXISTS artifacts(id INTEGER PRIMARY KEY,artifact_ref TEXT NOT NULL,chat_key TEXT NOT NULL,filename TEXT NOT NULL,sha256 TEXT NOT NULL,created_at INTEGER NOT NULL DEFAULT (unixepoch()),UNIQUE(chat_key,sha256));CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,path TEXT NOT NULL,content TEXT NOT NULL,content_sha256 TEXT NOT NULL);CREATE INDEX IF NOT EXISTS idx_artifacts_chat ON artifacts(chat_key,created_at DESC);CREATE INDEX IF NOT EXISTS idx_chunks_artifact ON chunks(artifact_id,id);")?;
        Ok(this)
    }

    fn connect(&self) -> Result<Connection> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;",
        )?;
        Ok(conn)
    }

    pub fn store(&self, request: StoreRequest) -> Result<StoreResult> {
        let mut conn = self.connect()?;
        let tx = conn.transaction()?;
        let artifact_ref = format!(
            "{}-{}",
            request.sha256.chars().take(12).collect::<String>(),
            short_hash(&request.chat_key)
        );
        tx.execute(
            "DELETE FROM artifacts WHERE chat_key=?1 AND sha256=?2",
            params![request.chat_key, request.sha256],
        )?;
        tx.execute(
            "INSERT INTO artifacts(artifact_ref,chat_key,filename,sha256) VALUES(?1,?2,?3,?4)",
            params![
                artifact_ref,
                request.chat_key,
                request.filename,
                request.sha256
            ],
        )?;
        let artifact_id = tx.last_insert_rowid();
        let mut count = 0usize;
        for chunk in request.chunks.into_iter().take(2500) {
            let content = redact(&chunk.content);
            if content.trim().is_empty() {
                continue;
            }
            let digest = hex::encode(Sha256::digest(content.as_bytes()));
            tx.execute(
                "INSERT INTO chunks(artifact_id,path,content,content_sha256) VALUES(?1,?2,?3,?4)",
                params![artifact_id, chunk.path, content, digest],
            )?;
            count += 1;
        }
        tx.commit()?;
        Ok(StoreResult {
            artifact_ref,
            chunk_count: count,
        })
    }

    pub fn search(&self, chat_key: &str, query: &str, limit: usize) -> Result<Vec<SearchHit>> {
        if limit == 0 {
            return Ok(Vec::new());
        }
        let mut seen = HashSet::new();
        let words: Vec<String> = query
            .split_whitespace()
            .map(str::to_lowercase)
            .filter(|word| word.len() > 1)
            .filter(|word| seen.insert(word.clone()))
            .collect();
        if words.is_empty() {
            return Ok(Vec::new());
        }

        let conn = self.connect()?;
        let mut stmt = conn.prepare("SELECT a.artifact_ref,a.filename,c.path,c.content FROM chunks c JOIN artifacts a ON a.id=c.artifact_id WHERE a.chat_key=?1 ORDER BY a.created_at DESC,c.id ASC LIMIT 10000")?;
        let rows = stmt.query_map([chat_key], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
            ))
        })?;
        let mut hits = Vec::new();
        for row in rows {
            let (artifact_ref, filename, path, content) = row?;
            let lower = content.to_lowercase();
            let matched = words
                .iter()
                .filter(|word| lower.contains(word.as_str()))
                .count();
            if matched == 0 {
                continue;
            }
            hits.push(SearchHit {
                artifact_ref,
                filename,
                path,
                content,
                score: matched as f64 / words.len() as f64,
            });
        }
        hits.sort_by(|a, b| b.score.total_cmp(&a.score));
        hits.truncate(limit);
        Ok(hits)
    }
}

fn short_hash(value: &str) -> String {
    hex::encode(Sha256::digest(value.as_bytes()))
        .chars()
        .take(6)
        .collect()
}

fn secret_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?im)^(\s*(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*)(.+)$")
            .unwrap()
    })
}

pub fn redact(text: &str) -> String {
    secret_re().replace_all(text, "$1<REDACTED>").into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn stores_and_searches() {
        let dir = tempdir().unwrap();
        let index = ArtifactIndex::new(dir.path().join("a.sqlite3")).unwrap();
        let stored = index
            .store(StoreRequest {
                chat_key: "1:0".into(),
                filename: "x.log".into(),
                sha256: "abcdefabcdef".into(),
                chunks: vec![Chunk {
                    path: "x.log".into(),
                    content: "token=secret\nfatal database timeout".into(),
                }],
            })
            .unwrap();
        assert_eq!(stored.chunk_count, 1);
        let hits = index.search("1:0", "database timeout database", 5).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].score, 1.0);
        assert!(!hits[0].content.contains("secret"));
    }

    #[test]
    fn skips_scan_for_empty_terms_or_zero_limit() {
        let dir = tempdir().unwrap();
        let index = ArtifactIndex::new(dir.path().join("a.sqlite3")).unwrap();
        assert!(index.search("missing", "a I", 5).unwrap().is_empty());
        assert!(index.search("missing", "database", 0).unwrap().is_empty());
    }
}
