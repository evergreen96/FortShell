use std::io;

#[cfg(windows)]
use std::ptr;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, WAIT_ABANDONED, WAIT_OBJECT_0};
#[cfg(windows)]
use windows_sys::Win32::System::Threading::{
    CreateMutexW, INFINITE, ReleaseMutex, WaitForSingleObject,
};

const BOUNDARY_LOCK_NAME: &str = r"Local\AIIdeWindowsHelperBoundaryLock";

pub struct RestrictedLaunchBoundaryLock {
    #[cfg(windows)]
    handle: HANDLE,
}

impl RestrictedLaunchBoundaryLock {
    pub fn acquire() -> io::Result<Self> {
        #[cfg(windows)]
        {
            let name = encode_wide(BOUNDARY_LOCK_NAME);
            let handle = unsafe { CreateMutexW(ptr::null(), 0, name.as_ptr()) };
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            let wait_result = unsafe { WaitForSingleObject(handle, INFINITE) };
            if wait_result != WAIT_OBJECT_0 && wait_result != WAIT_ABANDONED {
                unsafe {
                    CloseHandle(handle);
                }
                return Err(io::Error::last_os_error());
            }
            return Ok(Self { handle });
        }

        #[cfg(not(windows))]
        {
            Ok(Self {})
        }
    }
}

#[cfg(windows)]
impl Drop for RestrictedLaunchBoundaryLock {
    fn drop(&mut self) {
        unsafe {
            ReleaseMutex(self.handle);
            CloseHandle(self.handle);
        }
    }
}

#[cfg(not(windows))]
impl Drop for RestrictedLaunchBoundaryLock {
    fn drop(&mut self) {}
}

#[cfg(windows)]
fn encode_wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}
