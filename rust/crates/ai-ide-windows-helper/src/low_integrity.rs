use std::io;
use std::path::Path;

#[cfg(windows)]
use std::ffi::OsStr;
#[cfg(windows)]
use std::os::windows::ffi::OsStrExt;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{GetLastError, HANDLE, LocalFree};
#[cfg(windows)]
use windows_sys::Win32::Security::Authorization::{
    ConvertStringSidToSidW, GetNamedSecurityInfoW, SE_FILE_OBJECT, SetNamedSecurityInfoW,
};
#[cfg(windows)]
use windows_sys::Win32::Security::{
    ACL, ACL_REVISION, AddMandatoryAce, CONTAINER_INHERIT_ACE, GetLengthSid, InitializeAcl,
    LABEL_SECURITY_INFORMATION, OBJECT_INHERIT_ACE, PSECURITY_DESCRIPTOR, PSID, SID_AND_ATTRIBUTES,
    SYSTEM_MANDATORY_LABEL_ACE, SetTokenInformation, TOKEN_MANDATORY_LABEL, TokenIntegrityLevel,
};
#[cfg(windows)]
use windows_sys::Win32::System::SystemServices::{
    SE_GROUP_INTEGRITY, SYSTEM_MANDATORY_LABEL_NO_EXECUTE_UP, SYSTEM_MANDATORY_LABEL_NO_READ_UP,
    SYSTEM_MANDATORY_LABEL_NO_WRITE_UP,
};

#[cfg(windows)]
const LOW_INTEGRITY_SID: &str = "S-1-16-4096";
#[cfg(windows)]
const MEDIUM_INTEGRITY_SID: &str = "S-1-16-8192";

#[cfg(windows)]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SavedLabelSecurityDescriptor {
    sacl: Option<Vec<u8>>,
}

#[cfg(windows)]
struct OwnedSid {
    sid: PSID,
}

#[cfg(windows)]
impl OwnedSid {
    fn from_string(value: &str) -> io::Result<Self> {
        let mut sid = std::ptr::null_mut();
        let wide = to_wide(value);
        let converted = unsafe { ConvertStringSidToSidW(wide.as_ptr(), &mut sid) };
        if converted == 0 {
            return Err(io::Error::from_raw_os_error(
                unsafe { GetLastError() } as i32
            ));
        }
        Ok(Self { sid })
    }

    fn len(&self) -> u32 {
        unsafe { GetLengthSid(self.sid) }
    }
}

#[cfg(windows)]
impl Drop for OwnedSid {
    fn drop(&mut self) {
        if !self.sid.is_null() {
            unsafe {
                LocalFree(self.sid as _);
            }
        }
    }
}

#[cfg(windows)]
pub fn apply_low_integrity_to_token(token: HANDLE) -> io::Result<()> {
    let sid = OwnedSid::from_string(LOW_INTEGRITY_SID)?;
    let label = TOKEN_MANDATORY_LABEL {
        Label: SID_AND_ATTRIBUTES {
            Sid: sid.sid,
            Attributes: SE_GROUP_INTEGRITY as u32,
        },
    };
    let label_len = std::mem::size_of::<TOKEN_MANDATORY_LABEL>() as u32 + sid.len();
    let applied = unsafe {
        SetTokenInformation(
            token,
            TokenIntegrityLevel,
            &label as *const _ as *const _,
            label_len,
        )
    };
    if applied == 0 {
        return Err(io::Error::from_raw_os_error(
            unsafe { GetLastError() } as i32
        ));
    }
    Ok(())
}

#[cfg(windows)]
pub fn apply_low_integrity_label(path: &Path) -> io::Result<()> {
    apply_mandatory_label(path, LOW_INTEGRITY_SID, SYSTEM_MANDATORY_LABEL_NO_WRITE_UP)
}

#[cfg(windows)]
pub fn apply_blocked_read_guard_label(path: &Path) -> io::Result<()> {
    apply_mandatory_label(
        path,
        MEDIUM_INTEGRITY_SID,
        SYSTEM_MANDATORY_LABEL_NO_WRITE_UP
            | SYSTEM_MANDATORY_LABEL_NO_READ_UP
            | SYSTEM_MANDATORY_LABEL_NO_EXECUTE_UP,
    )
}

#[cfg(windows)]
pub fn capture_label_security_descriptor(path: &Path) -> io::Result<SavedLabelSecurityDescriptor> {
    let wide = to_wide_path(path);
    let mut sacl = std::ptr::null_mut();
    let mut security_descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
    let result = unsafe {
        GetNamedSecurityInfoW(
            wide.as_ptr(),
            SE_FILE_OBJECT,
            LABEL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut sacl,
            &mut security_descriptor,
        )
    };
    if result != 0 {
        return Err(io::Error::from_raw_os_error(result as i32));
    }
    let saved = if sacl.is_null() {
        None
    } else {
        let acl_size = unsafe { (*sacl).AclSize as usize };
        Some(unsafe { std::slice::from_raw_parts(sacl as *const u8, acl_size) }.to_vec())
    };
    if !security_descriptor.is_null() {
        unsafe {
            LocalFree(security_descriptor as _);
        }
    }
    Ok(SavedLabelSecurityDescriptor { sacl: saved })
}

#[cfg(windows)]
pub fn restore_label_security_descriptor(
    path: &Path,
    saved: &SavedLabelSecurityDescriptor,
) -> io::Result<()> {
    let wide = to_wide_path(path);
    let mut sacl = saved.sacl.clone();
    let sacl_ptr = sacl
        .as_mut()
        .map_or(std::ptr::null_mut(), |bytes| bytes.as_mut_ptr() as *mut ACL);
    let result = unsafe {
        SetNamedSecurityInfoW(
            wide.as_ptr() as *mut _,
            SE_FILE_OBJECT,
            LABEL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null(),
            sacl_ptr,
        )
    };
    if result != 0 {
        return Err(io::Error::from_raw_os_error(result as i32));
    }
    Ok(())
}

#[cfg(windows)]
fn apply_mandatory_label(path: &Path, sid_value: &str, policy: u32) -> io::Result<()> {
    let sid = OwnedSid::from_string(sid_value)?;
    let acl_len = std::mem::size_of::<ACL>()
        + std::mem::size_of::<SYSTEM_MANDATORY_LABEL_ACE>()
        + sid.len() as usize
        - std::mem::size_of::<u32>();
    let mut acl = vec![0u8; acl_len];
    let initialized =
        unsafe { InitializeAcl(acl.as_mut_ptr() as *mut ACL, acl_len as u32, ACL_REVISION) };
    if initialized == 0 {
        return Err(io::Error::from_raw_os_error(
            unsafe { GetLastError() } as i32
        ));
    }
    let added = unsafe {
        AddMandatoryAce(
            acl.as_mut_ptr() as *mut ACL,
            ACL_REVISION,
            inheritance_flags_for_path(path),
            policy,
            sid.sid,
        )
    };
    if added == 0 {
        return Err(io::Error::from_raw_os_error(
            unsafe { GetLastError() } as i32
        ));
    }
    let wide = to_wide_path(path);
    let result = unsafe {
        SetNamedSecurityInfoW(
            wide.as_ptr() as *mut _,
            SE_FILE_OBJECT,
            LABEL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null(),
            acl.as_mut_ptr() as *mut ACL,
        )
    };
    if result != 0 {
        return Err(io::Error::from_raw_os_error(result as i32));
    }
    Ok(())
}

#[cfg(windows)]
fn inheritance_flags_for_path(path: &Path) -> u32 {
    if path.is_dir() {
        OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
    } else {
        0
    }
}

#[cfg(windows)]
fn to_wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain([0]).collect()
}

#[cfg(windows)]
fn to_wide_path(path: &Path) -> Vec<u16> {
    OsStr::new(path).encode_wide().chain([0]).collect()
}

#[cfg(not(windows))]
pub fn apply_low_integrity_to_token(_token: *mut core::ffi::c_void) -> io::Result<()> {
    Ok(())
}

#[cfg(not(windows))]
pub fn apply_low_integrity_label(_path: &Path) -> io::Result<()> {
    Ok(())
}

#[cfg(not(windows))]
pub fn apply_blocked_read_guard_label(_path: &Path) -> io::Result<()> {
    Ok(())
}

#[cfg(not(windows))]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SavedLabelSecurityDescriptor;

#[cfg(not(windows))]
pub fn capture_label_security_descriptor(_path: &Path) -> io::Result<SavedLabelSecurityDescriptor> {
    Ok(SavedLabelSecurityDescriptor)
}

#[cfg(not(windows))]
pub fn restore_label_security_descriptor(
    _path: &Path,
    _saved: &SavedLabelSecurityDescriptor,
) -> io::Result<()> {
    Ok(())
}
