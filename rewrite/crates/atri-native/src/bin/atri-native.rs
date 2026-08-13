use anyhow::{bail, Result};
use atri_native::{hash_file_sha256, inspect_archive, list_dir_bounded, ArchiveLimits};
use std::env;

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    let Some(command) = args.next() else {
        bail!("usage: atri-native <hash|list-dir|inspect-archive> <path>");
    };
    let Some(path) = args.next() else {
        bail!("path is required");
    };

    match command.as_str() {
        "hash" => println!("{}", hash_file_sha256(path)?),
        "list-dir" => println!("{:#?}", list_dir_bounded(path, 20_000)?),
        "inspect-archive" => println!("{:#?}", inspect_archive(path, ArchiveLimits::default())?),
        other => bail!("unknown command: {other}"),
    }
    Ok(())
}
