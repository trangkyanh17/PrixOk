use anyhow::Result;
use rusqlite::{params, Connection};
use sha2::{Digest, Sha256};
use std::{
    cmp::Ordering,
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

const AUTO_MEMORY_MARKERS: &[&str] = &[
    "hãy nhớ",
    "nhớ là",
    "ghi nhớ",
    "lưu lại là",
    "chốt là",
    "từ giờ",
    "từ giờ về sau",
    "về sau hãy",
];

const STOPWORDS: &str = "anh atri ban bạn cai cái cho cua của dang đang day đây do đó duoc được em giu giữ hay hãy hien hiện khong không la là lai lại mot một nay này nhung nhưng noi nói nội prix roi rồi the thế thi thì toi tôi trong user va và voi với";
const MAX_EDIT_SIMILARITY_CHARS: usize = 512;

#[derive(Debug, Clone)]
pub struct LongMemoryConfig {
    pub retrieval_limit: usize,
    pub memory_card_limit: usize,
    pub auto_memory_dedupe_threshold: f64,
    pub manual_always_limit: usize,
}

impl Default for LongMemoryConfig {
    fn default() -> Self {
        Self {
            retrieval_limit: 3,
            memory_card_limit: 3,
            auto_memory_dedupe_threshold: 0.88,
            manual_always_limit: 2,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryHit {
    pub content: String,
    pub created_at: i64,
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct MemorySearchResult {
    pub cards: Vec<MemoryHit>,
    pub archive: Vec<MemoryHit>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct LongMemoryStats {
    pub archive_messages: i64,
    pub user_messages: i64,
    pub model_messages: i64,
    pub memory_cards: i64,
    pub oldest_at: Option<i64>,
    pub newest_at: Option<i64>,
    pub database_bytes: u64,
}

#[derive(Debug, Clone)]
pub struct LongMemoryStore {
    path: PathBuf,
    config: LongMemoryConfig,
}

impl LongMemoryStore {
    pub fn new(path: impl Into<PathBuf>, config: LongMemoryConfig) -> Self {
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
             CREATE TABLE IF NOT EXISTS chat_archive(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               chat_key TEXT NOT NULL,
               role TEXT NOT NULL,
               content TEXT NOT NULL,
               content_hash TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               source TEXT NOT NULL DEFAULT 'chat'
             );
             CREATE INDEX IF NOT EXISTS idx_chat_archive_key_time
               ON chat_archive(chat_key, created_at DESC, id DESC);
             CREATE INDEX IF NOT EXISTS idx_chat_archive_key_hash
               ON chat_archive(chat_key, content_hash);
             CREATE TABLE IF NOT EXISTS memory_cards(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               chat_key TEXT NOT NULL,
               content TEXT NOT NULL,
               content_hash TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               source TEXT NOT NULL DEFAULT 'manual',
               UNIQUE(chat_key, content_hash)
             );
             CREATE INDEX IF NOT EXISTS idx_memory_cards_key_time
               ON memory_cards(chat_key, created_at DESC, id DESC);",
        )?;
        Ok(conn)
    }

    pub fn add_memory_card(&self, chat_key: &str, content: &str, source: &str) -> Result<bool> {
        let content = content.trim().chars().take(4000).collect::<String>();
        if content.is_empty() || normalize_text(&content).is_empty() {
            return Ok(false);
        }

        let source = if source.trim().is_empty() {
            "manual"
        } else {
            source.trim()
        };
        let normalized = normalize_text(&content);
        let content_hash = hex::encode(Sha256::digest(normalized.as_bytes()));
        let conn = self.connect()?;

        if source == "automatic" {
            let mut statement = conn.prepare(
                "SELECT content
                 FROM memory_cards
                 WHERE chat_key=?1
                 ORDER BY created_at DESC, id DESC
                 LIMIT 40",
            )?;
            let candidates = statement
                .query_map([chat_key], |row| row.get::<_, String>(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?;

            if candidates
                .iter()
                .any(|old| similarity(&content, old) >= self.config.auto_memory_dedupe_threshold)
            {
                return Ok(false);
            }
        }

        let changed = conn.execute(
            "INSERT OR IGNORE INTO memory_cards(
               chat_key,content,content_hash,created_at,source
             ) VALUES(?1,?2,?3,?4,?5)",
            params![chat_key, content, content_hash, now_seconds(), source],
        )?;
        Ok(changed > 0)
    }

    pub fn archive_user_turn(&self, chat_key: &str, user_text: &str) -> Result<()> {
        let content = user_text.trim();
        if !content.is_empty() {
            let now = now_seconds();
            let hash_input = format!("user|{now}|{content}");
            let content_hash = hex::encode(Sha256::digest(hash_input.as_bytes()));
            let conn = self.connect()?;
            conn.execute(
                "INSERT INTO chat_archive(
                   chat_key,role,content,content_hash,created_at,source
                 ) VALUES(?1,'user',?2,?3,?4,'chat')",
                params![chat_key, content, content_hash, now],
            )?;
        }

        if should_auto_pin(user_text) {
            let _ = self.add_memory_card(chat_key, user_text, "automatic")?;
        }
        Ok(())
    }

    pub fn search(
        &self,
        chat_key: &str,
        query: &str,
        recent_texts: &HashSet<String>,
    ) -> Result<MemorySearchResult> {
        let conn = self.connect()?;
        let normalized_recent = recent_texts
            .iter()
            .map(|value| normalize_text(value))
            .filter(|value| !value.is_empty())
            .collect::<HashSet<_>>();

        let card_scan_limit = self.config.memory_card_limit.saturating_mul(8).max(24) as i64;
        let mut card_statement = conn.prepare(
            "SELECT content,created_at,source
             FROM memory_cards
             WHERE chat_key=?1
             ORDER BY created_at DESC, id DESC
             LIMIT ?2",
        )?;
        let card_rows = card_statement
            .query_map(params![chat_key, card_scan_limit], |row| {
                Ok(MemoryHit {
                    content: row.get(0)?,
                    created_at: row.get(1)?,
                    source: row.get(2)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let cards = select_cards(&card_rows, query, &normalized_recent, &self.config);

        let archive_scan_limit = self.config.retrieval_limit.saturating_mul(64).max(128) as i64;
        let mut archive_statement = conn.prepare(
            "SELECT content,created_at,source
             FROM chat_archive
             WHERE chat_key=?1 AND role='user'
             ORDER BY created_at DESC, id DESC
             LIMIT ?2",
        )?;
        let archive_rows = archive_statement
            .query_map(params![chat_key, archive_scan_limit], |row| {
                Ok(MemoryHit {
                    content: row.get(0)?,
                    created_at: row.get(1)?,
                    source: row.get(2)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let archive = select_archive(&archive_rows, query, &normalized_recent, &self.config);

        Ok(MemorySearchResult { cards, archive })
    }

    pub fn stats(&self, chat_key: &str) -> Result<LongMemoryStats> {
        let conn = self.connect()?;
        let archive = conn.query_row(
            "SELECT
               COUNT(*),
               COALESCE(SUM(role='user'),0),
               COALESCE(SUM(role='model'),0),
               MIN(created_at),
               MAX(created_at)
             FROM chat_archive
             WHERE chat_key=?1",
            [chat_key],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, Option<i64>>(3)?,
                    row.get::<_, Option<i64>>(4)?,
                ))
            },
        )?;
        let memory_cards = conn.query_row(
            "SELECT COUNT(*) FROM memory_cards WHERE chat_key=?1",
            [chat_key],
            |row| row.get::<_, i64>(0),
        )?;
        let database_bytes = fs::metadata(&self.path).map(|meta| meta.len()).unwrap_or(0);

        Ok(LongMemoryStats {
            archive_messages: archive.0,
            user_messages: archive.1,
            model_messages: archive.2,
            memory_cards,
            oldest_at: archive.3,
            newest_at: archive.4,
            database_bytes,
        })
    }

    pub fn forget_all(&self, chat_key: &str) -> Result<(usize, usize)> {
        let mut conn = self.connect()?;
        let transaction = conn.transaction()?;
        let archive_deleted =
            transaction.execute("DELETE FROM chat_archive WHERE chat_key=?1", [chat_key])?;
        let cards_deleted =
            transaction.execute("DELETE FROM memory_cards WHERE chat_key=?1", [chat_key])?;
        transaction.commit()?;
        Ok((archive_deleted, cards_deleted))
    }
}

pub fn normalize_text(value: &str) -> String {
    let mut output = String::new();
    let mut spaced = false;

    for ch in value.to_lowercase().chars() {
        if ch.is_alphanumeric() {
            output.push(ch);
            spaced = false;
        } else if !spaced && !output.is_empty() {
            output.push(' ');
            spaced = true;
        }
    }

    output.trim().to_string()
}

pub fn should_auto_pin(content: &str) -> bool {
    let normalized = normalize_text(content);
    AUTO_MEMORY_MARKERS
        .iter()
        .map(|marker| normalize_text(marker))
        .any(|marker| normalized.contains(&marker))
}

fn now_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

fn stopwords() -> &'static HashSet<&'static str> {
    static WORDS: OnceLock<HashSet<&'static str>> = OnceLock::new();
    WORDS.get_or_init(|| STOPWORDS.split_whitespace().collect())
}

fn meaningful_tokens_normalized(normalized: &str) -> HashSet<String> {
    normalized
        .split_whitespace()
        .filter(|token| token.chars().count() >= 3 && !stopwords().contains(*token))
        .map(ToOwned::to_owned)
        .collect()
}

fn meaningful_tokens(value: &str) -> HashSet<String> {
    meaningful_tokens_normalized(&normalize_text(value))
}

fn query_relevance_with_tokens(content: &str, query_tokens: &HashSet<String>) -> f64 {
    if query_tokens.is_empty() {
        return 0.0;
    }
    let content_tokens = meaningful_tokens(content);
    if content_tokens.is_empty() {
        return 0.0;
    }
    let shared = content_tokens.intersection(query_tokens).count();
    if shared == 0 {
        return 0.0;
    }
    let containment = shared as f64 / content_tokens.len().min(query_tokens.len()) as f64;
    let coverage = shared as f64 / content_tokens.len().max(query_tokens.len()) as f64;
    containment.max(coverage)
}

#[cfg(test)]
fn query_relevance(content: &str, query: &str) -> f64 {
    let query_tokens = meaningful_tokens(query);
    query_relevance_with_tokens(content, &query_tokens)
}

fn similarity(left: &str, right: &str) -> f64 {
    let left = normalize_text(left);
    let right = normalize_text(right);
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }
    if left == right {
        return 1.0;
    }

    let left_tokens = meaningful_tokens_normalized(&left);
    let right_tokens = meaningful_tokens_normalized(&right);
    let token_score = if left_tokens.is_empty() || right_tokens.is_empty() {
        0.0
    } else {
        let shared = left_tokens.intersection(&right_tokens).count();
        let union = left_tokens.union(&right_tokens).count();
        let containment = shared as f64 / left_tokens.len().min(right_tokens.len()) as f64;
        let jaccard = if union == 0 {
            0.0
        } else {
            shared as f64 / union as f64
        };
        containment.max(jaccard)
    };

    token_score.max(edit_similarity(&left, &right))
}

fn edit_similarity(left: &str, right: &str) -> f64 {
    let left_len = left.chars().count();
    let right_len = right.chars().count();
    if left_len == 0 || right_len == 0 {
        return 0.0;
    }
    if left_len > MAX_EDIT_SIMILARITY_CHARS || right_len > MAX_EDIT_SIMILARITY_CHARS {
        return 0.0;
    }

    let a = left.chars().collect::<Vec<_>>();
    let b = right.chars().collect::<Vec<_>>();
    let mut previous = (0..=b.len()).collect::<Vec<_>>();
    let mut current = vec![0; b.len() + 1];
    for (i, left_ch) in a.iter().enumerate() {
        current[0] = i + 1;
        for (j, right_ch) in b.iter().enumerate() {
            let cost = usize::from(left_ch != right_ch);
            current[j + 1] = (previous[j + 1] + 1)
                .min(current[j] + 1)
                .min(previous[j] + cost);
        }
        std::mem::swap(&mut previous, &mut current);
    }

    let distance = previous[b.len()];
    1.0 - distance as f64 / a.len().max(b.len()) as f64
}

fn select_cards(
    rows: &[MemoryHit],
    query: &str,
    recent_texts: &HashSet<String>,
    config: &LongMemoryConfig,
) -> Vec<MemoryHit> {
    let mut selected = Vec::new();
    let mut seen = HashSet::new();
    let mut manual_always = 0usize;
    let mut scored = Vec::new();
    let query_tokens = meaningful_tokens(query);

    for row in rows {
        let normalized = normalize_text(&row.content);
        if normalized.is_empty() || recent_texts.contains(&normalized) || !seen.insert(normalized) {
            continue;
        }

        let relevance = query_relevance_with_tokens(&row.content, &query_tokens);
        if row.source.eq_ignore_ascii_case("manual") && manual_always < config.manual_always_limit {
            selected.push(row.clone());
            manual_always += 1;
            if selected.len() >= config.memory_card_limit {
                return selected;
            }
            continue;
        }

        if relevance > 0.0 {
            scored.push((relevance, row.created_at, row.clone()));
        }
    }

    scored.sort_by(score_order);
    for (_, _, row) in scored {
        selected.push(row);
        if selected.len() >= config.memory_card_limit {
            break;
        }
    }
    selected
}

fn select_archive(
    rows: &[MemoryHit],
    query: &str,
    recent_texts: &HashSet<String>,
    config: &LongMemoryConfig,
) -> Vec<MemoryHit> {
    let mut seen = HashSet::new();
    let mut selected_contents = Vec::<String>::new();
    let mut scored = Vec::new();
    let query_tokens = meaningful_tokens(query);

    for row in rows {
        let normalized = normalize_text(&row.content);
        if normalized.is_empty() || recent_texts.contains(&normalized) || !seen.insert(normalized) {
            continue;
        }

        // Relevance is cheaper than fuzzy edit similarity and prevents an
        // irrelevant near-duplicate from suppressing an actually relevant row.
        let relevance = query_relevance_with_tokens(&row.content, &query_tokens);
        if relevance <= 0.0 {
            continue;
        }
        if selected_contents
            .iter()
            .any(|prior| similarity(&row.content, prior) >= config.auto_memory_dedupe_threshold)
        {
            continue;
        }
        selected_contents.push(row.content.clone());
        scored.push((relevance, row.created_at, row.clone()));
    }

    scored.sort_by(score_order);
    scored
        .into_iter()
        .take(config.retrieval_limit)
        .map(|(_, _, row)| row)
        .collect()
}

fn score_order(left: &(f64, i64, MemoryHit), right: &(f64, i64, MemoryHit)) -> Ordering {
    right
        .0
        .partial_cmp(&left.0)
        .unwrap_or(Ordering::Equal)
        .then_with(|| right.1.cmp(&left.1))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn store() -> (tempfile::TempDir, LongMemoryStore) {
        let dir = tempdir().unwrap();
        let store = LongMemoryStore::new(
            dir.path().join("memory.sqlite3"),
            LongMemoryConfig::default(),
        );
        (dir, store)
    }

    #[test]
    fn explicit_memory_markers_auto_pin_but_casual_requests_do_not() {
        assert!(should_auto_pin("Hãy nhớ từ giờ dùng câu trả lời ngắn."));
        assert!(should_auto_pin("Nhớ là máy này chạy Termux."));
        assert!(!should_auto_pin("T muốn câu trả lời ngắn lần này."));
    }

    #[test]
    fn automatic_cards_are_deduplicated() {
        let (_dir, store) = store();
        assert!(store
            .add_memory_card("chat", "Hãy nhớ máy chính chạy Termux Debian", "automatic")
            .unwrap());
        assert!(!store
            .add_memory_card(
                "chat",
                "Hãy nhớ máy chính chạy Termux Debian nhé",
                "automatic",
            )
            .unwrap());
        assert_eq!(store.stats("chat").unwrap().memory_cards, 1);
    }

    #[test]
    fn archive_keeps_user_turn_and_auto_card() {
        let (_dir, store) = store();
        store
            .archive_user_turn("chat", "Nhớ là bot production chạy trong /app")
            .unwrap();
        let stats = store.stats("chat").unwrap();
        assert_eq!(stats.archive_messages, 1);
        assert_eq!(stats.user_messages, 1);
        assert_eq!(stats.model_messages, 0);
        assert_eq!(stats.memory_cards, 1);
    }

    #[test]
    fn search_prefers_manual_cards_and_relevant_archive() {
        let (_dir, store) = store();
        store
            .add_memory_card("chat", "Ưu tiên giữ worker production duy nhất", "manual")
            .unwrap();
        store
            .add_memory_card("chat", "Màu giao diện là xanh", "automatic")
            .unwrap();
        store
            .archive_user_turn(
                "chat",
                "Bot production chạy Debian trong Termux và dùng tmux",
            )
            .unwrap();
        store
            .archive_user_turn("chat", "Hôm nay tôi ăn bánh mì")
            .unwrap();

        let result = store
            .search("chat", "worker bot Termux production", &HashSet::new())
            .unwrap();
        assert_eq!(result.cards[0].source, "manual");
        assert!(result
            .archive
            .iter()
            .any(|hit| hit.content.contains("Termux")));
        assert!(!result
            .archive
            .iter()
            .any(|hit| hit.content.contains("bánh mì")));
    }

    #[test]
    fn irrelevant_near_duplicate_does_not_hide_relevant_archive() {
        let rows = vec![
            MemoryHit {
                content: "máy chính chạy termux debian với worker production ổn định".repeat(8),
                created_at: 20,
                source: "chat".into(),
            },
            MemoryHit {
                content: format!(
                    "{} docker",
                    "máy chính chạy termux debian với worker production ổn định".repeat(8)
                ),
                created_at: 10,
                source: "chat".into(),
            },
        ];
        let selected = select_archive(
            &rows,
            "docker",
            &HashSet::new(),
            &LongMemoryConfig::default(),
        );
        assert_eq!(selected.len(), 1);
        assert!(selected[0].content.contains("docker"));
    }

    #[test]
    fn long_similarity_uses_token_path_without_large_edit_matrix() {
        let common = "alpha beta gamma ".repeat(200);
        let left = format!("{common}docker");
        let right = format!("{common}docker compose");
        assert!(similarity(&left, &right) > 0.8);
        assert_eq!(edit_similarity(&left, &right), 0.0);
    }

    #[test]
    fn recent_text_is_not_retrieved_again() {
        let (_dir, store) = store();
        let text = "Bot production chạy Debian trong Termux";
        store.archive_user_turn("chat", text).unwrap();
        let recent = HashSet::from([text.to_string()]);
        let result = store.search("chat", "bot Termux", &recent).unwrap();
        assert!(result.archive.is_empty());
    }

    #[test]
    fn forget_all_removes_archive_and_cards() {
        let (_dir, store) = store();
        store
            .archive_user_turn("chat", "Nhớ là bot dùng tmux")
            .unwrap();
        let (archive, cards) = store.forget_all("chat").unwrap();
        assert_eq!(archive, 1);
        assert_eq!(cards, 1);
        let stats = store.stats("chat").unwrap();
        assert_eq!(stats.archive_messages, 0);
        assert_eq!(stats.memory_cards, 0);
    }

    #[test]
    fn query_relevance_still_matches_expected_tokens() {
        assert!(query_relevance("bot production chạy Termux", "bot termux") > 0.0);
        assert_eq!(query_relevance("bánh mì", "bot termux"), 0.0);
    }
}
