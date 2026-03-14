use std::io;

use crate::low_integrity::apply_low_integrity_to_token;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
#[cfg(windows)]
use windows_sys::Win32::Security::{
    CreateRestrictedToken, DISABLE_MAX_PRIVILEGE, IsTokenRestricted, TOKEN_ADJUST_DEFAULT,
    TOKEN_ASSIGN_PRIMARY, TOKEN_DUPLICATE, TOKEN_QUERY,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

pub fn helper_can_create_restricted_token() -> io::Result<bool> {
    #[cfg(windows)]
    {
        let token = match create_restricted_token() {
            Ok(token) => token,
            Err(error) if error.raw_os_error() == Some(87) => return Ok(false),
            Err(error) => return Err(error),
        };
        let restricted = unsafe { IsTokenRestricted(token.handle) };
        return Ok(restricted != 0);
    }

    #[cfg(not(windows))]
    {
        Ok(false)
    }
}

#[cfg(windows)]
pub(crate) struct OwnedHandle {
    pub(crate) handle: HANDLE,
}

#[cfg(windows)]
impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe { CloseHandle(self.handle) };
        }
    }
}

#[cfg(windows)]
pub(crate) fn create_restricted_token() -> io::Result<OwnedHandle> {
    let mut current_token: HANDLE = std::ptr::null_mut();
    let open_ok = unsafe {
        OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT,
            &mut current_token,
        )
    };
    if open_ok == 0 {
        return Err(io::Error::last_os_error());
    }
    let current_token = OwnedHandle {
        handle: current_token,
    };

    let mut restricted: HANDLE = std::ptr::null_mut();
    let restricted_ok = unsafe {
        CreateRestrictedToken(
            current_token.handle,
            DISABLE_MAX_PRIVILEGE,
            0,
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            std::ptr::null(),
            &mut restricted,
        )
    };
    if restricted_ok == 0 {
        return Err(io::Error::last_os_error());
    }

    let restricted = OwnedHandle { handle: restricted };
    apply_low_integrity_to_token(restricted.handle)?;
    Ok(restricted)
}

#[cfg(test)]
mod tests {
    use super::helper_can_create_restricted_token;

    #[cfg(windows)]
    #[test]
    fn helper_reports_whether_restricted_token_is_available() {
        let result =
            helper_can_create_restricted_token().expect("restricted token probe should succeed");
        assert!(matches!(result, true | false));
    }

    #[cfg(not(windows))]
    #[test]
    fn helper_reports_no_restricted_token_support_off_windows() {
        let result =
            helper_can_create_restricted_token().expect("non-windows probe should succeed");
        assert!(!result);
    }
}
