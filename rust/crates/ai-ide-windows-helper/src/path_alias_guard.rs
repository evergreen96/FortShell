use std::fs;
use std::path::Path;

pub fn path_is_hardlink_alias_under_root(root: &Path, candidate: &Path) -> bool {
    let Ok(relative) = candidate.strip_prefix(root) else {
        return false;
    };
    if relative.as_os_str().is_empty() {
        return false;
    }

    let metadata = match fs::symlink_metadata(candidate) {
        Ok(metadata) => metadata,
        Err(_) => return false,
    };
    if metadata.file_type().is_symlink() || metadata.is_dir() {
        return false;
    }

    link_count(candidate) > 1
}

#[cfg(windows)]
fn link_count(path: &Path) -> u64 {
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

#[cfg(not(windows))]
fn link_count(_: &Path) -> u64 {
    1
}

#[cfg(test)]
mod tests {
    use super::path_is_hardlink_alias_under_root;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn detects_hardlink_alias_under_root() {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let base = std::env::temp_dir().join(format!(
            "ai-ide-windows-helper-hardlink-{}-{id}",
            std::process::id()
        ));
        let root = base.join("root");
        let outside = base.join("outside");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        let target = outside.join("data.txt");
        let alias = root.join("alias.txt");
        std::fs::write(&target, b"alias").unwrap();
        std::fs::hard_link(&target, &alias).unwrap();

        assert!(path_is_hardlink_alias_under_root(&root, &alias));
        let _ = std::fs::remove_dir_all(&base);
    }
}
