use atri_native::{normalize_history, RecentMemoryConfig, RecentMemoryStore};
use serde_json::json;
use tempfile::tempdir;

fn message(role: &str, text: &str) -> serde_json::Value {
    json!({"role": role, "parts": [{"text": text}]})
}

#[test]
fn normalizes_only_last_window_before_filtering() {
    let history = json!([
        message("user", "old-user"),
        message("model", "old-model"),
        {"role":"system","parts":[{"text":"ignored"}]},
        {"role":"user","parts":[]},
        message("user", "new-user"),
        message("model", "new-model")
    ]);
    let normalized = normalize_history(&history, 4);
    assert_eq!(normalized.len(), 2);
    assert_eq!(normalized[0]["parts"][0]["text"], "new-user");
    assert_eq!(normalized[1]["parts"][0]["text"], "new-model");
}

#[test]
fn drops_invalid_roles_parts_and_non_object_parts() {
    let history = json!([
        3,
        {"role":"assistant","parts":[{"text":"wrong-role"}]},
        {"role":"user","parts":"bad"},
        {"role":"user","parts":[1,"x",{"text":"kept"}]}
    ]);
    let normalized = normalize_history(&history, 12);
    assert_eq!(
        normalized,
        vec![json!({"role":"user","parts":[{"text":"kept"}]})]
    );
}

#[test]
fn save_load_and_clear_round_trip() {
    let dir = tempdir().unwrap();
    let store = RecentMemoryStore::new(
        dir.path().join("memory.sqlite3"),
        RecentMemoryConfig::default(),
    );
    let key = json!([123, 0]);
    let history = json!([message("user", "hello"), message("model", "world")]);

    store.save_at(&key, &history, 100).unwrap();
    assert_eq!(
        store.load(&key).unwrap(),
        history.as_array().unwrap().clone()
    );
    assert_eq!(store.count_rows().unwrap(), 1);

    store.clear(&key).unwrap();
    assert!(store.load(&key).unwrap().is_empty());
    assert_eq!(store.count_rows().unwrap(), 0);
}

#[test]
fn save_keeps_only_configured_recent_items() {
    let dir = tempdir().unwrap();
    let store = RecentMemoryStore::new(
        dir.path().join("memory.sqlite3"),
        RecentMemoryConfig {
            max_history_items: 3,
            ..RecentMemoryConfig::default()
        },
    );
    let history = json!([
        message("user", "1"),
        message("model", "2"),
        message("user", "3"),
        message("model", "4")
    ]);
    store.save_at(&json!("chat"), &history, 100).unwrap();
    let loaded = store.load(&json!("chat")).unwrap();
    assert_eq!(loaded.len(), 3);
    assert_eq!(loaded[0]["parts"][0]["text"], "2");
    assert_eq!(loaded[2]["parts"][0]["text"], "4");
}

#[test]
fn retention_prunes_stale_rows() {
    let dir = tempdir().unwrap();
    let store = RecentMemoryStore::new(
        dir.path().join("memory.sqlite3"),
        RecentMemoryConfig {
            retention_seconds: 3600,
            ..RecentMemoryConfig::default()
        },
    );
    let history = json!([message("user", "x")]);
    store.save_at(&json!("old"), &history, 100).unwrap();
    store.save_at(&json!("new"), &history, 4000).unwrap();
    assert!(store.load(&json!("old")).unwrap().is_empty());
    assert_eq!(store.load(&json!("new")).unwrap().len(), 1);
}

#[test]
fn max_chat_rows_prunes_oldest_rows() {
    let dir = tempdir().unwrap();
    let store = RecentMemoryStore::new(
        dir.path().join("memory.sqlite3"),
        RecentMemoryConfig {
            max_chat_rows: 10,
            retention_seconds: 100_000,
            ..RecentMemoryConfig::default()
        },
    );
    let history = json!([message("user", "x")]);
    for index in 0..12 {
        store
            .save_at(&json!(index), &history, 10_000 + index)
            .unwrap();
    }
    assert_eq!(store.count_rows().unwrap(), 10);
    assert!(store.load(&json!(0)).unwrap().is_empty());
    assert!(store.load(&json!(1)).unwrap().is_empty());
    assert_eq!(store.load(&json!(11)).unwrap().len(), 1);
}
