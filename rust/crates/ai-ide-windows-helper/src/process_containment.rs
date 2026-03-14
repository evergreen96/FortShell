use std::io;

#[cfg(windows)]
use std::mem::size_of;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
#[cfg(windows)]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JobObjectExtendedLimitInformation, SetInformationJobObject,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HelperContainmentPolicy {
    KillOnCloseSingleProcess,
    KillOnCloseShellSingleChild,
}

pub struct HelperChildContainment {
    #[cfg(windows)]
    job: HANDLE,
}

impl HelperChildContainment {
    pub fn new(policy: HelperContainmentPolicy) -> io::Result<Self> {
        #[cfg(windows)]
        {
            let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if job.is_null() {
                return Err(io::Error::last_os_error());
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if matches!(
                policy,
                HelperContainmentPolicy::KillOnCloseSingleProcess
                    | HelperContainmentPolicy::KillOnCloseShellSingleChild
            ) {
                info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
                info.BasicLimitInformation.ActiveProcessLimit =
                    if matches!(policy, HelperContainmentPolicy::KillOnCloseSingleProcess) {
                        1
                    } else {
                        2
                    };
            }
            let result = unsafe {
                SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const _,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if result == 0 {
                unsafe { CloseHandle(job) };
                return Err(io::Error::last_os_error());
            }
            return Ok(Self { job });
        }

        #[cfg(not(windows))]
        {
            Ok(Self {})
        }
    }

    #[cfg(windows)]
    pub fn assign_process_handle(&self, process_handle: HANDLE) -> io::Result<()> {
        let result = unsafe { AssignProcessToJobObject(self.job, process_handle) };
        if result == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

#[cfg(windows)]
impl Drop for HelperChildContainment {
    fn drop(&mut self) {
        if !self.job.is_null() {
            unsafe { CloseHandle(self.job) };
        }
    }
}
