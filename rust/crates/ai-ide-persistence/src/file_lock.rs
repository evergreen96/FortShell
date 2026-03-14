use std::fs::{File, OpenOptions};
use std::io::{self, Seek, SeekFrom, Write};
use std::path::Path;

use fs2::FileExt;

pub struct FileLockGuard {
    file: File,
}

impl FileLockGuard {
    pub fn acquire(path: &Path) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;

        file.lock_exclusive()?;
        if file.metadata()?.len() == 0 {
            file.write_all(b"0")?;
            file.flush()?;
        }
        file.seek(SeekFrom::Start(0))?;

        Ok(Self { file })
    }
}

impl Drop for FileLockGuard {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}
