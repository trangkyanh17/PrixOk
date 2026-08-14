use anyhow::{bail, Result};
use rusqlite::{params, Connection, OptionalExtension};
use std::{
    env,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

const MAX_CHAT_ROWS: i64 = 500;
const RETENTION_SECONDS: i64 = 2_592_000;

fn open_db(path: impl AsRef<Path>) -> Result<Connection> {
    let conn = Connection::open(path)?;
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

fn now_seconds() -> Result<i64> {
    Ok(SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs() as i64)
}

fn load(conn: &Connection, chat_key: &str) -> Result<Option<String>> {
    Ok(conn
        .query_row(
            "SELECT history_json FROM chat_memory WHERE chat_key=?1",
            [chat_key],
            |row| row.get(0),
        )
        .optional()?)
}

fn save(conn: &mut Connection, chat_key: &str, history: &str, count: i64) -> Result<()> {
    let now = now_seconds()?;
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO chat_memory(chat_key,history_json,message_count,updated_at)
         VALUES(?1,?2,?3,?4)
         ON CONFLICT(chat_key) DO UPDATE SET
           history_json=excluded.history_json,
           message_count=excluded.message_count,
           updated_at=excluded.updated_at",
        params![chat_key, history, count.max(0), now],
    )?;
    tx.execute(
        "DELETE FROM chat_memory WHERE updated_at < ?1",
        [now.saturating_sub(RETENTION_SECONDS)],
    )?;
    tx.execute(
        "DELETE FROM chat_memory WHERE chat_key IN (
           SELECT chat_key FROM chat_memory
           ORDER BY updated_at DESC, chat_key DESC
           LIMIT -1 OFFSET ?1
         )",
        [MAX_CHAT_ROWS],
    )?;
    tx.commit()?;
    Ok(())
}

fn clear(conn: &Connection, chat_key: &str) -> Result<()> {
    conn.execute("DELETE FROM chat_memory WHERE chat_key=?1", [chat_key])?;
    Ok(())
}

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    let Some(command) = args.next() else {
        bail!("command required")
    };
    let Some(db) = args.next() else {
        bail!("database required")
    };
    let Some(chat_key) = args.next() else {
        bail!("chat key required")
    };
    let mut conn = open_db(db)?;
    match command.as_str() {
        "load" => {
            if let Some(value) = load(&conn, &chat_key)? {
                println!("{value}");
            }
        }
        "save" => {
            let Some(history) = args.next() else {
                bail!("history required")
            };
            let count = args
                .next()
                .and_then(|value| value.parse::<i64>().ok())
                .unwrap_or(0);
            save(&mut conn, &chat_key, &history, count)?;
        }
        "clear" => clear(&conn, &chat_key)?,
        other => bail!("unknown command: {other}"),
    }
    Ok(())
}
