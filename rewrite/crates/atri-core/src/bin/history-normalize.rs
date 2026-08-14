use anyhow::Result;
use serde_json::{json, Value};
use std::{env, fs};

fn normalize_history(history: &Value, max_items: usize) -> Vec<Value> {
    let Some(items) = history.as_array() else {
        return Vec::new();
    };
    let keep = max_items.max(2);
    let start = items.len().saturating_sub(keep);
    let mut result = Vec::new();

    for item in &items[start..] {
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
        let clean_parts: Vec<Value> = parts
            .iter()
            .filter(|part| part.is_object())
            .cloned()
            .collect();
        if !clean_parts.is_empty() {
            result.push(json!({"role": role, "parts": clean_parts}));
        }
    }

    result
}

fn main() -> Result<()> {
    let Some(path) = env::args().nth(1) else {
        return Ok(());
    };
    let source: Value = serde_json::from_str(&fs::read_to_string(path)?)?;
    let normalized = normalize_history(&source, 12);
    println!("{}", serde_json::to_string(&normalized)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_only_recent_valid_conversation_items() {
        let input = json!([
            {"role":"user","parts":[{"text":"one"}]},
            {"role":"system","parts":[{"text":"skip"}]},
            {"role":"model","parts":[{"text":"two"}]},
            {"role":"user","parts":["skip-part"]},
            {"role":"user","parts":[{"text":"three"}]}
        ]);
        let normalized = normalize_history(&input, 3);
        assert_eq!(normalized.len(), 2);
        assert_eq!(normalized[0]["role"], "model");
        assert_eq!(normalized[1]["role"], "user");
    }

    #[test]
    fn invalid_top_level_value_is_empty() {
        assert!(normalize_history(&json!({"x": 1}), 12).is_empty());
    }
}
