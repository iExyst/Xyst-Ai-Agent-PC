//! Confined export of a bundle created by the authenticated local engine.

use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
};

const MAX_BACKUP_BYTES: u64 = 1024 * 1024 * 1024;

pub fn export_backup(
    source: &Path,
    destination: &Path,
    backup_directory: &Path,
) -> Result<PathBuf, String> {
    if destination.extension().and_then(|value| value.to_str()) != Some("xaa-backup") {
        return Err("Choose a destination ending in .xaa-backup".into());
    }
    let source_metadata =
        fs::symlink_metadata(source).map_err(|_| "The generated backup is unavailable")?;
    if !source_metadata.is_file() || source_metadata.file_type().is_symlink() {
        return Err("The generated backup is not a regular file".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if source_metadata.file_attributes() & 0x400 != 0 {
            return Err("The generated backup cannot be a reparse point".into());
        }
    }
    if source_metadata.len() > MAX_BACKUP_BYTES {
        return Err("The generated backup exceeds the export size limit".into());
    }
    let source = source
        .canonicalize()
        .map_err(|_| "The generated backup is unavailable")?;
    let allowed = backup_directory
        .canonicalize()
        .map_err(|_| "The application backup directory is unavailable")?;
    if source.parent() != Some(allowed.as_path())
        || source.extension().and_then(|value| value.to_str()) != Some("xaa-backup")
    {
        return Err("The generated backup is outside the application backup directory".into());
    }
    let parent = destination
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .ok_or_else(|| "Choose a destination folder".to_string())?
        .canonicalize()
        .map_err(|_| "The selected destination folder is unavailable")?;
    let file_name = destination
        .file_name()
        .ok_or_else(|| "Choose a backup filename".to_string())?;
    let destination = parent.join(file_name);
    if destination.exists() {
        return Err("That backup file already exists. Choose a different filename".into());
    }

    let temporary = parent.join(format!(".xaa-backup-{}.partial", uuid::Uuid::new_v4()));
    let result = (|| -> io::Result<()> {
        let mut input = File::open(&source)?;
        let metadata = input.metadata()?;
        if !metadata.is_file() || metadata.len() > MAX_BACKUP_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid backup source",
            ));
        }
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        let copied = io::copy(
            &mut Read::by_ref(&mut input).take(MAX_BACKUP_BYTES + 1),
            &mut output,
        )?;
        if copied != metadata.len() || copied > MAX_BACKUP_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "backup size changed during export",
            ));
        }
        output.flush()?;
        output.sync_all()?;
        drop(output);
        if destination.exists() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "destination already exists",
            ));
        }
        fs::rename(&temporary, &destination)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
        return Err("Could not save the backup to the selected location".into());
    }
    Ok(destination)
}

#[cfg(test)]
mod tests {
    use super::export_backup;
    use std::{fs, path::PathBuf};

    fn fixture() -> PathBuf {
        std::env::temp_dir().join(format!("xaa-backup-export-{}", uuid::Uuid::new_v4()))
    }

    #[test]
    fn exports_only_a_generated_backup_and_refuses_overwrite() {
        let root = fixture();
        let backups = root.join("data/backups");
        let output = root.join("output");
        fs::create_dir_all(&backups).unwrap();
        fs::create_dir_all(&output).unwrap();
        let source = backups.join("generated.xaa-backup");
        fs::write(&source, b"verified fixture archive").unwrap();
        let destination = output.join("saved.xaa-backup");

        let result = export_backup(&source, &destination, &backups).unwrap();
        assert_eq!(fs::read(result).unwrap(), b"verified fixture archive");
        assert!(export_backup(&source, &destination, &backups).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn refuses_source_outside_owned_backup_directory() {
        let root = fixture();
        let backups = root.join("data/backups");
        let other = root.join("other");
        let output = root.join("output");
        fs::create_dir_all(&backups).unwrap();
        fs::create_dir_all(&other).unwrap();
        fs::create_dir_all(&output).unwrap();
        let source = other.join("untrusted.xaa-backup");
        fs::write(&source, b"not owned").unwrap();
        assert!(export_backup(&source, &output.join("saved.xaa-backup"), &backups).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
