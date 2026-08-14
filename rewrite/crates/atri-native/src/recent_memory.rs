use anyhow::Result;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{Map, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Debug, Clone)]
pub struct RecentMemoryConfig {
    pub max_history_items: usize,
    pub max_chat_rows: i64,
    pub retention_seconds: i64,
}

impl Default for RecentMemoryConfig {
    fn default() -> Self {
        Self {
            max_history_items: 12,
            max_chat_rows: 500,
            retention_seconds: 2_592_000,
        }
    }
}

#[derive(Debug, Clone)]
pub struct RecentMemoryStore {
    path: PathBuf,
    config: RecentMemoryConfig,
}

impl RecentMemoryStore {
    pub fn new(path: impl Into<PathBuf>, config: RecentMemoryConfig) -> Self {
        Self {
            path: path.into(),
            config,
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn connect(&self) -> Result<Connection> {
        if let Some(parent) = self.path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        let conn = Connection::open(&self.path)?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             PRAGMA busy_timeout=30000;
             CREATE TABLE IF NOT EXISTS chat_memory(
               chat_key TEXT PRIMARY KEY,
               history_json TEXT NOT NULL,
               message_count INTEGER NOT NULL DEFAULT 0,
               updated_at INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_chat_memory_updated
               ON chat_memory(updated_at DESC, chat_key DESC);",
        )?;
        Ok(conn)
    }

    pub fn load(&self, key: &Value) -> Result<Vec<Value>> {
        let conn = self.connect()?;
        let chat_key = key_to_text(key);
        let payload = conn
            .query_row(
                "SELECT history_json FROM chat_memory WHERE chat_key=?1",
                [chat_key],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        let Some(payload) = payload else {
            return Ok(Vec::new());
        };
        let history = serde_json::from_str::<Value>(&payload).unwrap_or(Value::Array(Vec::new()));
        Ok(normalize_history(&history, self.config.max_history_items))
    }

    pub fn save(&self, key: &Value, history: &Value) -> Result<()> {
        self.save_at(key, history, now_seconds())
    }

    pub fn save_at(&self, key: &Value, history: &Value, now: i64) -> Result<()> {
        let normalized = normalize_history(history, self.config.max_history_items);
        let payload = serde_json::to_string(&normalized)?;
        let chat_key = key_to_text(key);
        let mut conn = self.connect()?;
        let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute(
            "INSERT INTO chat_memory(chat_key,history_json,message_count,updated_at)
             VALUES(?1,?2,?3,?4)
             ON CONFLICT(chat_key) DO UPDATE SET
               history_json=excluded.history_json,
               message_count=excluded.message_count,
               updated_at=excluded.updated_at",
            params![chat_key, payload, normalized.len() as i64, now],
        )?;
        tx.execute(
            "DELETE FROM chat_memory WHERE updated_at < ?1",
            [now.saturating_sub(self.config.retention_seconds.max(3600))],
        )?;
        tx.execute(
            "DELETE FROM chat_memory WHERE chat_key IN (
               SELECT chat_key FROM chat_memory
               ORDER BY updated_at DESC, chat_key DESC
               LIMIT -1 OFFSET ?1
             )",
            [self.config.max_chat_rows.max(10)],
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn clear(&self, key: &Value) -> Result<()> {
        let conn = self.connect()?;
        conn.execute(
            "DELETE FROM chat_memory WHERE chat_key=?1",
            [key_to_text(key)],
        )?;
        Ok(())
    }

    pub fn count_rows(&self) -> Result<i64> {
        let conn = self.connect()?;
        Ok(conn.query_row("SELECT COUNT(*) FROM chat_memory", [], |row| row.get(0))?)
    }
}

pub fn key_to_text(key: &Value) -> String {
    serde_json::to_string(key).unwrap_or_else(|_| format!("{key:?}"))
}

pub fn normalize_history(history: &Value, max_history_items: usize) -> Vec<Value> {
    let Some(items) = history.as_array() else {
        return Vec::new();
    };
    let limit = max_history_items.max(2);
    let mut result = Vec::with_capacity(limit.min(items.len()));

    // Walk from newest to oldest and count only valid user/model messages.
    // Slicing before validation could let trailing tool/system/invalid entries
    // crowd useful conversational history out of the retained window.
    for item in items.iter().rev() {
        if result.len() >= limit {
            break;
        }
        let Some(object) = item.as_object() else {
            continue;
        };
        let role = object
            .get("role")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if role != "user" && role != "model" {
            continue;
        }
        let Some(parts) = object.get("parts").and_then(Value::as_array) else {
            continue;
        };
        let clean_parts = parts
            .iter()
            .filter(|part| part.is_object())
            .cloned()
            .collect::<Vec<_>>();
        if clean_parts.is_empty() {
            continue;
        }
        let mut clean = Map::new();
        clean.insert("role".to_string(), Value::String(role.to_string()));
        clean.insert("parts".to_string(), Value::Array(clean_parts));
        result.push(Value::Object(clean));
    }
    result.reverse();
    result
}

fn now_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs() as i64)
        .unwrap_or(0)
}
