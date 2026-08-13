use rusqlite::{params, Connection, OptionalExtension};
use tempfile::tempdir;

#[test]
fn recent_memory_schema_round_trip() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("memory.sqlite3");
    let conn = Connection::open(path).unwrap();
    conn.execute_batch(
        "CREATE TABLE chat_memory(
           chat_key TEXT PRIMARY KEY,
           history_json TEXT NOT NULL,
           message_count INTEGER NOT NULL DEFAULT 0,
           updated_at INTEGER NOT NULL
         );",
    )
    .unwrap();
    conn.execute(
        "INSERT INTO chat_memory(chat_key,history_json,message_count,updated_at)
         VALUES(?1,?2,?3,?4)",
        params!["1:0", "[]", 0, 1],
    )
    .unwrap();
    let value: Option<String> = conn
        .query_row(
            "SELECT history_json FROM chat_memory WHERE chat_key=?1",
            ["1:0"],
            |row| row.get(0),
        )
        .optional()
        .unwrap();
    assert_eq!(value.as_deref(), Some("[]"));
}
