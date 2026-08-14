use rusqlite::{params_from_iter, Connection, OpenFlags, Row, ToSql};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::path::Path;
use unicode_normalization::UnicodeNormalization;

const REGION: &str = "cn";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DeltaSearchRequest {
    pub query: String,
    pub season: Option<i64>,
    #[serde(default)]
    pub category: String,
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub platform: String,
    #[serde(default)]
    pub limit: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DeltaHistoryRequest {
    pub query: String,
    #[serde(default)]
    pub season_from: Option<i64>,
    #[serde(default)]
    pub season_to: Option<i64>,
    #[serde(default)]
    pub limit: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DeltaCompareRequest {
    pub query: String,
    pub season_a: i64,
    pub season_b: i64,
    #[serde(default)]
    pub limit: Option<i64>,
}

pub fn normalize_delta_text(value: &str) -> String {
    let mut output = String::new();
    let mut spaced = false;

    for ch in value.nfkc().flat_map(char::to_lowercase) {
        if ch.is_alphanumeric() || matches!(ch, '.' | '+' | '-' | '_') {
            output.push(ch);
            spaced = false;
        } else if !spaced {
            output.push(' ');
            spaced = true;
        }
    }

    output.trim().to_string()
}

fn parse_json_or(value: Option<String>, fallback: Value) -> Value {
    value
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .unwrap_or(fallback)
}

fn optional_string(row: &Row<'_>, name: &str) -> Option<String> {
    row.get::<_, Option<String>>(name).ok().flatten()
}

fn optional_i64(row: &Row<'_>, name: &str) -> Option<i64> {
    row.get::<_, Option<i64>>(name).ok().flatten()
}

fn entity_result(row: &Row<'_>) -> Value {
    json!({
        "kind": "entity",
        "id": optional_string(row, "id"),
        "name_cn": optional_string(row, "name_cn"),
        "name_en": optional_string(row, "name_en"),
        "name_vi": optional_string(row, "name_vi"),
        "aliases": parse_json_or(optional_string(row, "aliases_json"), json!([])),
        "category": optional_string(row, "category"),
        "subcategory": optional_string(row, "subcategory"),
        "mode": parse_json_or(optional_string(row, "mode_json"), json!([])),
        "platform": parse_json_or(optional_string(row, "platform_json"), json!([])),
        "region": REGION,
        "season_introduced": optional_i64(row, "season_introduced"),
        "season_last_seen": optional_i64(row, "season_last_seen"),
        "grade": optional_string(row, "grade"),
        "stats": parse_json_or(optional_string(row, "stats_json"), json!({})),
        "source_url": optional_string(row, "source_url"),
        "source_type": optional_string(row, "source_type"),
        "confidence": optional_string(row, "confidence"),
        "snapshot_at": optional_string(row, "snapshot_at"),
    })
}

fn document_result(row: &Row<'_>) -> Value {
    let content = optional_string(row, "content").unwrap_or_default();
    let excerpt: String = content.chars().take(2600).collect();
    json!({
        "kind": "source_document",
        "id": optional_string(row, "id"),
        "season": optional_i64(row, "season"),
        "title": optional_string(row, "title"),
        "excerpt": excerpt,
        "source_url": optional_string(row, "source_url"),
        "source_type": optional_string(row, "source_type"),
        "confidence": optional_string(row, "confidence"),
        "published_date": optional_string(row, "published_date"),
        "chunk_index": optional_i64(row, "chunk_index"),
    })
}

fn delta_connect(path: &Path) -> Result<Connection, String> {
    if !path.is_file() {
        return Err(format!(
            "Knowledge base Delta Force China chưa tồn tại: {}",
            path.display()
        ));
    }
    Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|error| format!("Không mở được Delta Force China KB: {error}"))
}

fn metadata(connection: &Connection) -> Map<String, Value> {
    let mut output = Map::new();
    let Ok(mut statement) = connection.prepare("SELECT key, value FROM metadata") else {
        return output;
    };
    let Ok(mut rows) = statement.query([]) else {
        return output;
    };
    while let Ok(Some(row)) = rows.next() {
        let key = row.get::<_, String>(0).unwrap_or_default();
        let value = row.get::<_, String>(1).unwrap_or_default();
        if !key.is_empty() {
            output.insert(key, Value::String(value));
        }
    }
    output
}

fn collect_documents(
    connection: &Connection,
    sql: &str,
    parameters: Vec<Box<dyn ToSql>>,
) -> rusqlite::Result<Vec<Value>> {
    let refs: Vec<&dyn ToSql> = parameters.iter().map(|item| item.as_ref()).collect();
    let mut statement = connection.prepare(sql)?;
    let mut rows = statement.query(params_from_iter(refs))?;
    let mut output = Vec::new();
    while let Some(row) = rows.next()? {
        output.push(document_result(row));
    }
    Ok(output)
}

fn search_documents(
    connection: &Connection,
    query: &str,
    season: Option<i64>,
    limit: i64,
) -> Vec<Value> {
    let query_n = normalize_delta_text(query);
    let tokens: Vec<&str> = query_n
        .split_whitespace()
        .filter(|token| !token.is_empty())
        .collect();

    if !tokens.is_empty() {
        let expression = tokens
            .iter()
            .map(|token| format!("\"{}\"*", token.replace('"', "")))
            .collect::<Vec<_>>()
            .join(" OR ");

        let fts_result = if let Some(season) = season {
            collect_documents(
                connection,
                "SELECT d.* FROM documents_fts f JOIN documents d ON d.id = f.id \
                 WHERE documents_fts MATCH ? AND d.season = ? \
                 ORDER BY bm25(documents_fts), d.season DESC, d.chunk_index LIMIT ?",
                vec![Box::new(expression), Box::new(season), Box::new(limit)],
            )
        } else {
            collect_documents(
                connection,
                "SELECT d.* FROM documents_fts f JOIN documents d ON d.id = f.id \
                 WHERE documents_fts MATCH ? \
                 ORDER BY bm25(documents_fts), d.season DESC, d.chunk_index LIMIT ?",
                vec![Box::new(expression), Box::new(limit)],
            )
        };

        if let Ok(rows) = fts_result {
            if !rows.is_empty() {
                return rows;
            }
        }
    }

    let like = format!("%{query_n}%");
    let fallback = if let Some(season) = season {
        collect_documents(
            connection,
            "SELECT d.* FROM documents d WHERE d.search_text LIKE ? AND d.season = ? \
             ORDER BY d.season DESC, d.chunk_index LIMIT ?",
            vec![Box::new(like), Box::new(season), Box::new(limit)],
        )
    } else {
        collect_documents(
            connection,
            "SELECT d.* FROM documents d WHERE d.search_text LIKE ? \
             ORDER BY d.season DESC, d.chunk_index LIMIT ?",
            vec![Box::new(like), Box::new(limit)],
        )
    };
    fallback.unwrap_or_default()
}

fn search_entities(
    connection: &Connection,
    request: &DeltaSearchRequest,
    query_n: &str,
    limit: i64,
) -> Vec<Value> {
    let mut filters = vec!["region = 'cn'".to_string()];
    let mut parameters: Vec<Box<dyn ToSql>> = Vec::new();

    if !request.category.trim().is_empty() {
        filters.push("category_key = ?".to_string());
        parameters.push(Box::new(normalize_delta_text(&request.category)));
    }
    if !request.mode.trim().is_empty() {
        filters.push("mode_key LIKE ?".to_string());
        parameters.push(Box::new(format!(
            "%|{}|%",
            normalize_delta_text(&request.mode)
        )));
    }
    if !request.platform.trim().is_empty() {
        filters.push("platform_key LIKE ?".to_string());
        parameters.push(Box::new(format!(
            "%|{}|%",
            normalize_delta_text(&request.platform)
        )));
    }

    parameters.push(Box::new(format!("%{query_n}%")));
    parameters.push(Box::new(format!("%|{query_n}|%")));
    parameters.push(Box::new(format!("%{query_n}%")));
    parameters.push(Box::new(limit));

    let sql = format!(
        "SELECT * FROM entities WHERE {} AND \
         (name_key LIKE ? OR aliases_key LIKE ? OR search_text LIKE ?) \
         ORDER BY CASE confidence WHEN 'verified' THEN 0 \
         WHEN 'cross_checked' THEN 1 ELSE 2 END, name_cn LIMIT ?",
        filters.join(" AND ")
    );

    let refs: Vec<&dyn ToSql> = parameters.iter().map(|item| item.as_ref()).collect();
    let Ok(mut statement) = connection.prepare(&sql) else {
        return Vec::new();
    };
    let Ok(mut rows) = statement.query(params_from_iter(refs)) else {
        return Vec::new();
    };
    let mut output = Vec::new();
    while let Ok(Some(row)) = rows.next() {
        output.push(entity_result(row));
    }
    output
}

pub fn search_delta_force_cn(path: &Path, request: &DeltaSearchRequest) -> Value {
    let query = request.query.trim();
    if query.is_empty() {
        return json!({"ok": false, "error": "Thiếu nội dung tra cứu.", "region": REGION});
    }
    if let Some(season) = request.season {
        if !(1..=10).contains(&season) {
            return json!({"ok": false, "error": "Season hoặc limit không hợp lệ.", "region": REGION});
        }
    }
    let limit = request.limit.unwrap_or(8);
    if limit <= 0 {
        return json!({"ok": false, "error": "Season hoặc limit không hợp lệ.", "region": REGION});
    }
    let limit = limit.clamp(1, 12);

    let connection = match delta_connect(path) {
        Ok(connection) => connection,
        Err(error) => return json!({"ok": false, "error": error, "region": REGION}),
    };
    let coverage = metadata(&connection);
    let query_n = normalize_delta_text(query);
    let mut results = Vec::new();

    if request.season.is_none() || request.season == Some(10) {
        results.extend(search_entities(&connection, request, &query_n, limit));
    }

    let doc_limit = (limit - results.len() as i64).max(1);
    results.extend(search_documents(
        &connection,
        query,
        request.season,
        doc_limit,
    ));
    results.truncate(limit as usize);

    let season_scope = request
        .season
        .map(Value::from)
        .unwrap_or_else(|| Value::String("current_cn_s10_plus_history_docs".to_string()));

    json!({
        "ok": true,
        "region": REGION,
        "season_scope": season_scope,
        "query": query,
        "result_count": results.len(),
        "results": results,
        "coverage": coverage,
        "strict_note": "Không được dùng entity current để điền ngược chỉ số cho S1-S9. Không có bằng chứng trong results thì phải nói chưa xác minh."
    })
}

pub fn get_delta_force_cn_history(path: &Path, request: &DeltaHistoryRequest) -> Value {
    let mut season_from = request.season_from.unwrap_or(1).clamp(1, 10);
    let mut season_to = request.season_to.unwrap_or(10).clamp(1, 10);
    let limit = request.limit.unwrap_or(16).clamp(1, 20);
    if season_from > season_to {
        std::mem::swap(&mut season_from, &mut season_to);
    }

    let connection = match delta_connect(path) {
        Ok(connection) => connection,
        Err(error) => return json!({"ok": false, "error": error, "region": REGION}),
    };
    let per_season = limit.clamp(1, 4);
    let mut all_results = Vec::new();
    for season in season_from..=season_to {
        all_results.extend(search_documents(
            &connection,
            &request.query,
            Some(season),
            per_season,
        ));
        if all_results.len() >= limit as usize {
            break;
        }
    }
    all_results.truncate(limit as usize);

    let mut grouped = Map::new();
    for item in all_results {
        let season = item
            .get("season")
            .and_then(Value::as_i64)
            .unwrap_or_default()
            .to_string();
        grouped
            .entry(season)
            .or_insert_with(|| Value::Array(Vec::new()))
            .as_array_mut()
            .expect("grouped history value is always an array")
            .push(item);
    }
    let result_count: usize = grouped
        .values()
        .filter_map(Value::as_array)
        .map(Vec::len)
        .sum();

    json!({
        "ok": true,
        "region": REGION,
        "query": request.query.trim(),
        "season_from": season_from,
        "season_to": season_to,
        "results_by_season": grouped,
        "result_count": result_count,
        "coverage": metadata(&connection),
        "strict_note": "Mùa không có kết quả phải được coi là chưa có bằng chứng, không phải chắc chắn chưa tồn tại."
    })
}

pub fn compare_delta_force_cn_seasons(path: &Path, request: &DeltaCompareRequest) -> Value {
    if !(1..=10).contains(&request.season_a) || !(1..=10).contains(&request.season_b) {
        return json!({"ok": false, "error": "Season so sánh không hợp lệ.", "region": REGION});
    }
    let limit = request.limit.unwrap_or(5);
    if limit <= 0 {
        return json!({"ok": false, "error": "Season so sánh không hợp lệ.", "region": REGION});
    }
    let limit = limit.clamp(1, 10);

    let connection = match delta_connect(path) {
        Ok(connection) => connection,
        Err(error) => return json!({"ok": false, "error": error, "region": REGION}),
    };
    let side_a = search_documents(&connection, &request.query, Some(request.season_a), limit);
    let side_b = search_documents(&connection, &request.query, Some(request.season_b), limit);

    json!({
        "ok": true,
        "region": REGION,
        "query": request.query.trim(),
        "season_a": {"season": request.season_a, "evidence": side_a},
        "season_b": {"season": request.season_b, "evidence": side_b},
        "coverage": metadata(&connection),
        "strict_note": "Đây là bằng chứng hai phía, không phải diff đã được chứng minh. Chỉ kết luận thay đổi khi tài liệu ghi rõ giá trị hoặc cơ chế cũ và mới."
    })
}
