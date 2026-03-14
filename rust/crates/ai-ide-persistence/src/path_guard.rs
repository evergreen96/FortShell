use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathGuardError {
    SymlinkPath { target: String },
    HardlinkPath { target: String },
}

pub fn ensure_no_symlink_components(root: &Path, path: &Path) -> Result<(), PathGuardError> {
    let candidate = candidate_path(root, path);
    let Ok(relative) = candidate.strip_prefix(root) else {
        return Ok(());
    };

    let mut current = root.to_path_buf();
    for component in relative.components() {
        current.push(component.as_os_str());
        let metadata = match fs::symlink_metadata(&current) {
            Ok(metadata) => metadata,
            Err(_) => continue,
        };
        if metadata.file_type().is_symlink() {
            let target = current
                .strip_prefix(root)
                .map(path_to_posix)
                .unwrap_or_else(|_| current.display().to_string());
            return Err(PathGuardError::SymlinkPath { target });
        }
    }

    Ok(())
}

pub fn ensure_no_hardlink_alias(root: &Path, path: &Path) -> Result<(), PathGuardError> {
    let candidate = candidate_path(root, path);
    let Ok(relative) = candidate.strip_prefix(root) else {
        return Ok(());
    };
    if relative.as_os_str().is_empty() {
        return Ok(());
    }

    let metadata = match fs::symlink_metadata(&candidate) {
        Ok(metadata) => metadata,
        Err(_) => return Ok(()),
    };
    if metadata.file_type().is_symlink() || metadata.is_dir() {
        return Ok(());
    }

    if link_count(&candidate, &metadata) > 1 {
        return Err(PathGuardError::HardlinkPath {
            target: path_to_posix(relative),
        });
    }

    Ok(())
}

fn candidate_path(root: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        root.join(path)
    }
}

fn path_to_posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(unix)]
fn link_count(_: &Path, metadata: &fs::Metadata) -> u64 {
    use std::os::unix::fs::MetadataExt;

    metadata.nlink()
}

#[cfg(windows)]
fn link_count(path: &Path, _: &fs::Metadata) -> u64 {
    use std::fs::File;
    use std::mem::zeroed;
    use std::os::windows::io::AsRawHandle;

    #[repr(C)]
    struct FileTime {
        dw_low_date_time: u32,
        dw_high_date_time: u32,
    }

    #[repr(C)]
    struct ByHandleFileInformation {
        dw_file_attributes: u32,
        ft_creation_time: FileTime,
        ft_last_access_time: FileTime,
        ft_last_write_time: FileTime,
        dw_volume_serial_number: u32,
        n_file_size_high: u32,
        n_file_size_low: u32,
        n_number_of_links: u32,
        n_file_index_high: u32,
        n_file_index_low: u32,
    }

    unsafe extern "system" {
        fn GetFileInformationByHandle(
            h_file: *mut core::ffi::c_void,
            lp_file_information: *mut ByHandleFileInformation,
        ) -> i32;
    }

    let Ok(file) = File::open(path) else {
        return 1;
    };

    let mut info = unsafe { zeroed::<ByHandleFileInformation>() };
    let status = unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info as *mut _) };
    if status == 0 {
        1
    } else {
        u64::from(info.n_number_of_links)
    }
}

#[cfg(not(any(unix, windows)))]
fn link_count(_: &Path, _: &fs::Metadata) -> u64 {
    1
}
