use anyhow::{bail, Result};
use atri_native::{hash_file_sha256, inspect_archive, list_dir_bounded, ArchiveLimits};
use serde_json::json;
use std::env;

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    let Some(command) = args.next() else { bail!("usage: atri-native <hash|list-dir|inspect-archive> <path>"); };
    let Some(path) = args.next() else { bail!("path is required"); };
    let value = match command.as_str() {
        "hash" => json!({"sha256": hash_file_sha256(path)?}),
        "list-dir" => json!(list_dir_bounded(path, 20_000)?),
        "inspect-archive" => json!(inspect_archive(path, ArchiveLimits::default())?),
        other => bail!("unknown command: {other}"),
    };
    println!("{}", serde_json::to_string(&value)?);
    Ok(())
}
