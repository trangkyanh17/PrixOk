use anyhow::{bail, Context, Result};
use atri_native::{
    compare_delta_force_cn_seasons, get_delta_force_cn_history, hash_file_sha256,
    inspect_archive, list_dir_bounded, search_delta_force_cn, ArchiveLimits, DeltaCompareRequest,
    DeltaHistoryRequest, DeltaSearchRequest,
};
use std::{env, path::Path};

fn json_argument(args: &mut impl Iterator<Item = String>) -> Result<String> {
    args.next().context("JSON request argument is required")
}

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    let Some(command) = args.next() else {
        bail!(
            "usage: atri-native <hash|list-dir|inspect-archive|delta-search|delta-history|delta-compare> ..."
        );
    };

    match command.as_str() {
        "hash" => {
            let path = args.next().context("path is required")?;
            println!("{}", hash_file_sha256(path)?);
        }
        "list-dir" => {
            let path = args.next().context("path is required")?;
            println!("{:#?}", list_dir_bounded(path, 20_000)?);
        }
        "inspect-archive" => {
            let path = args.next().context("path is required")?;
            println!("{:#?}", inspect_archive(path, ArchiveLimits::default())?);
        }
        "delta-search" => {
            let db = args.next().context("database path is required")?;
            let request: DeltaSearchRequest = serde_json::from_str(&json_argument(&mut args)?)?;
            println!(
                "{}",
                serde_json::to_string(&search_delta_force_cn(Path::new(&db), &request))?
            );
        }
        "delta-history" => {
            let db = args.next().context("database path is required")?;
            let request: DeltaHistoryRequest = serde_json::from_str(&json_argument(&mut args)?)?;
            println!(
                "{}",
                serde_json::to_string(&get_delta_force_cn_history(Path::new(&db), &request))?
            );
        }
        "delta-compare" => {
            let db = args.next().context("database path is required")?;
            let request: DeltaCompareRequest = serde_json::from_str(&json_argument(&mut args)?)?;
            println!(
                "{}",
                serde_json::to_string(&compare_delta_force_cn_seasons(
                    Path::new(&db),
                    &request
                ))?
            );
        }
        other => bail!("unknown command: {other}"),
    }
    Ok(())
}
