use serde::{Deserialize, Serialize};
use std::{env, path::PathBuf};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("missing required environment variable {0}")]
    Missing(&'static str),
    #[error("invalid integer for {0}: {1}")]
    InvalidInt(&'static str, String),
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum ProviderKind {
    OpenAiCompatible,
    Gemini,
    Vertex,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderConfig {
    pub kind: ProviderKind,
    pub base_url: String,
    pub api_key: Option<String>,
    pub model: String,
    pub project: Option<String>,
    pub location: Option<String>,
    pub oauth_access_token: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub telegram_bot_token: String,
    pub provider: ProviderConfig,
    pub data_dir: PathBuf,
    pub max_history_messages: usize,
    pub max_output_tokens: u32,
    pub request_timeout_seconds: u64,
    pub poll_timeout_seconds: u64,
}

impl AppConfig {
    pub fn from_env() -> Result<Self, ConfigError> {
        let telegram_bot_token = required("TELEGRAM_BOT_TOKEN")?;
        let provider_kind = env::var("ATRI_PROVIDER")
            .unwrap_or_else(|_| "openai".to_string())
            .to_ascii_lowercase();

        let provider = match provider_kind.as_str() {
            "gemini" => ProviderConfig {
                kind: ProviderKind::Gemini,
                base_url: env::var("ATRI_PROVIDER_BASE_URL").unwrap_or_else(|_| {
                    "https://generativelanguage.googleapis.com/v1beta".to_string()
                }),
                api_key: Some(required("GEMINI_API_KEY")?),
                model: env::var("ATRI_MODEL").unwrap_or_else(|_| "gemini-2.5-flash".to_string()),
                project: None,
                location: None,
                oauth_access_token: None,
            },
            "vertex" => ProviderConfig {
                kind: ProviderKind::Vertex,
                base_url: String::new(),
                api_key: None,
                model: env::var("ATRI_MODEL").unwrap_or_else(|_| "gemini-2.5-flash".to_string()),
                project: Some(required("GOOGLE_CLOUD_PROJECT")?),
                location: Some(env::var("GOOGLE_CLOUD_LOCATION").unwrap_or_else(|_| "us-central1".to_string())),
                oauth_access_token: Some(required("GOOGLE_OAUTH_ACCESS_TOKEN")?),
            },
            _ => ProviderConfig {
                kind: ProviderKind::OpenAiCompatible,
                base_url: env::var("ATRI_PROVIDER_BASE_URL")
                    .unwrap_or_else(|_| "https://api.openai.com/v1".to_string()),
                api_key: env::var("ATRI_PROVIDER_API_KEY").ok(),
                model: env::var("ATRI_MODEL").unwrap_or_else(|_| "gpt-5-mini".to_string()),
                project: None,
                location: None,
                oauth_access_token: None,
            },
        };

        Ok(Self {
            telegram_bot_token,
            provider,
            data_dir: PathBuf::from(env::var("ATRI_DATA_DIR").unwrap_or_else(|_| "/app/atri_data".to_string())),
            max_history_messages: env_usize("ATRI_RECENT_HISTORY_MESSAGES", 12)?,
            max_output_tokens: env_u32("ATRI_MAX_OUTPUT_TOKENS", 8192)?,
            request_timeout_seconds: env_u64("ATRI_REQUEST_TIMEOUT_SECONDS", 90)?,
            poll_timeout_seconds: env_u64("ATRI_POLL_TIMEOUT_SECONDS", 45)?,
        })
    }
}

fn required(name: &'static str) -> Result<String, ConfigError> {
    env::var(name).map_err(|_| ConfigError::Missing(name))
}

fn env_usize(name: &'static str, default: usize) -> Result<usize, ConfigError> {
    match env::var(name) {
        Ok(v) => v.parse().map_err(|_| ConfigError::InvalidInt(name, v)),
        Err(_) => Ok(default),
    }
}

fn env_u32(name: &'static str, default: u32) -> Result<u32, ConfigError> {
    match env::var(name) {
        Ok(v) => v.parse().map_err(|_| ConfigError::InvalidInt(name, v)),
        Err(_) => Ok(default),
    }
}

fn env_u64(name: &'static str, default: u64) -> Result<u64, ConfigError> {
    match env::var(name) {
        Ok(v) => v.parse().map_err(|_| ConfigError::InvalidInt(name, v)),
        Err(_) => Ok(default),
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum Role {
    System,
    User,
    Assistant,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChatMessage {
    pub role: Role,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenerateRequest {
    pub messages: Vec<ChatMessage>,
    pub max_output_tokens: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GenerateResponse {
    pub text: String,
    pub provider: String,
    pub model: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BotCommand {
    Start,
    Status,
    Clear,
    Help,
    Unknown(String),
}

pub fn parse_command(text: &str) -> Option<BotCommand> {
    let token = text.trim().split_whitespace().next()?;
    if !token.starts_with('/') {
        return None;
    }
    let name = token.trim_start_matches('/').split('@').next().unwrap_or("");
    Some(match name.to_ascii_lowercase().as_str() {
        "start" => BotCommand::Start,
        "status" => BotCommand::Status,
        "clear" => BotCommand::Clear,
        "help" => BotCommand::Help,
        other => BotCommand::Unknown(other.to_string()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_commands_with_bot_suffix() {
        assert_eq!(parse_command("/status@PrixOkBot x"), Some(BotCommand::Status));
        assert_eq!(parse_command("hello"), None);
    }
}
