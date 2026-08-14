use atri_native::{
    compare_delta_force_cn_seasons, get_delta_force_cn_history, normalize_delta_text,
    search_delta_force_cn, DeltaCompareRequest, DeltaHistoryRequest, DeltaSearchRequest,
};
use rusqlite::{params, Connection};
use tempfile::TempDir;

fn fixture() -> (TempDir, std::path::PathBuf) {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("delta.sqlite3");
    let connection = Connection::open(&path).unwrap();
    connection
        .execute_batch(
            r#"
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                name_cn TEXT,
                name_en TEXT,
                name_vi TEXT,
                aliases_json TEXT,
                category TEXT,
                subcategory TEXT,
                mode_json TEXT,
                platform_json TEXT,
                region TEXT,
                season_introduced INTEGER,
                season_last_seen INTEGER,
                grade TEXT,
                stats_json TEXT,
                source_url TEXT,
                source_type TEXT,
                confidence TEXT,
                snapshot_at TEXT,
                category_key TEXT,
                mode_key TEXT,
                platform_key TEXT,
                name_key TEXT,
                aliases_key TEXT,
                search_text TEXT
            );
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                season INTEGER,
                title TEXT,
                content TEXT,
                source_url TEXT,
                source_type TEXT,
                confidence TEXT,
                published_date TEXT,
                chunk_index INTEGER,
                search_text TEXT
            );
            "#,
        )
        .unwrap();
    connection
        .execute(
            "INSERT INTO metadata(key,value) VALUES('coverage','S1-S10 test')",
            [],
        )
        .unwrap();
    connection
        .execute(
            "INSERT INTO entities(
                id,name_cn,name_en,name_vi,aliases_json,category,subcategory,
                mode_json,platform_json,region,season_introduced,season_last_seen,
                grade,stats_json,source_url,source_type,confidence,snapshot_at,
                category_key,mode_key,platform_key,name_key,aliases_key,search_text
             ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            params![
                "weapon-m4",
                "M4A1",
                "M4A1",
                "M4A1",
                r#"["M4"]"#,
                "weapon",
                "assault_rifle",
                r#"["operations","warfare"]"#,
                r#"["pc","mobile"]"#,
                "cn",
                1,
                10,
                "purple",
                r#"{"damage":32}"#,
                "https://example.com/m4",
                "official",
                "verified",
                "2026-08-14",
                "weapon",
                "|operations|warfare|",
                "|pc|mobile|",
                "m4a1",
                "|m4|",
                "m4a1 m4 rifle weapon"
            ],
        )
        .unwrap();
    for (season, id, content) in [
        (1, "s1-m4", "M4 S1 damage 30"),
        (5, "s5-m4", "M4 S5 damage 31"),
        (10, "s10-m4", "M4 S10 damage 32"),
    ] {
        connection
            .execute(
                "INSERT INTO documents(
                    id,season,title,content,source_url,source_type,confidence,
                    published_date,chunk_index,search_text
                 ) VALUES(?,?,?,?,?,?,?,?,?,?)",
                params![
                    id,
                    season,
                    format!("M4 season {season}"),
                    content,
                    format!("https://example.com/{id}"),
                    "official",
                    "verified",
                    format!("2026-0{}-01", season.min(9)),
                    0,
                    format!("m4 season {season} damage")
                ],
            )
            .unwrap();
    }
    drop(connection);
    (temp, path)
}

#[test]
fn normalizer_matches_search_key_shape() {
    assert_eq!(normalize_delta_text("  M4/A1 + Test  "), "m4 a1 + test");
    assert_eq!(normalize_delta_text("ＡＢＣ"), "abc");
}

#[test]
fn current_search_combines_entity_and_documents() {
    let (_temp, path) = fixture();
    let result = search_delta_force_cn(
        &path,
        &DeltaSearchRequest {
            query: "M4".into(),
            category: "weapon".into(),
            mode: "operations".into(),
            platform: "pc".into(),
            limit: Some(4),
            ..Default::default()
        },
    );
    assert_eq!(result["ok"], true);
    assert_eq!(result["region"], "cn");
    assert_eq!(result["coverage"]["coverage"], "S1-S10 test");
    let results = result["results"].as_array().unwrap();
    assert!(!results.is_empty());
    assert_eq!(results[0]["kind"], "entity");
    assert_eq!(results[0]["stats"]["damage"], 32);
}

#[test]
fn historical_season_does_not_backfill_current_entity() {
    let (_temp, path) = fixture();
    let result = search_delta_force_cn(
        &path,
        &DeltaSearchRequest {
            query: "M4".into(),
            season: Some(5),
            limit: Some(5),
            ..Default::default()
        },
    );
    assert_eq!(result["ok"], true);
    let results = result["results"].as_array().unwrap();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0]["kind"], "source_document");
    assert_eq!(results[0]["season"], 5);
}

#[test]
fn history_groups_evidence_by_season() {
    let (_temp, path) = fixture();
    let result = get_delta_force_cn_history(
        &path,
        &DeltaHistoryRequest {
            query: "M4".into(),
            season_from: Some(1),
            season_to: Some(10),
            limit: Some(10),
        },
    );
    assert_eq!(result["ok"], true);
    assert_eq!(result["result_count"], 3);
    assert_eq!(result["results_by_season"]["1"][0]["season"], 1);
    assert_eq!(result["results_by_season"]["5"][0]["season"], 5);
    assert_eq!(result["results_by_season"]["10"][0]["season"], 10);
}

#[test]
fn compare_returns_two_evidence_sides_without_claiming_diff() {
    let (_temp, path) = fixture();
    let result = compare_delta_force_cn_seasons(
        &path,
        &DeltaCompareRequest {
            query: "M4".into(),
            season_a: 1,
            season_b: 10,
            limit: Some(3),
        },
    );
    assert_eq!(result["ok"], true);
    assert_eq!(result["season_a"]["season"], 1);
    assert_eq!(result["season_b"]["season"], 10);
    assert_eq!(result["season_a"]["evidence"][0]["season"], 1);
    assert_eq!(result["season_b"]["evidence"][0]["season"], 10);
    assert!(result["strict_note"]
        .as_str()
        .unwrap()
        .contains("không phải diff"));
}

#[test]
fn invalid_search_and_missing_database_return_safe_envelopes() {
    let (_temp, path) = fixture();
    let empty = search_delta_force_cn(
        &path,
        &DeltaSearchRequest {
            query: " ".into(),
            ..Default::default()
        },
    );
    assert_eq!(empty["ok"], false);

    let missing = search_delta_force_cn(
        std::path::Path::new("/definitely/missing/delta.sqlite3"),
        &DeltaSearchRequest {
            query: "M4".into(),
            ..Default::default()
        },
    );
    assert_eq!(missing["ok"], false);
    assert!(missing["error"].as_str().unwrap().contains("chưa tồn tại"));
}
