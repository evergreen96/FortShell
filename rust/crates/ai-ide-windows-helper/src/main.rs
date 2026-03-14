use std::process::ExitCode;

fn main() -> ExitCode {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match ai_ide_windows_helper::parse_helper_request_args(args) {
        Ok(request) => match ai_ide_windows_helper::runtime::run_request(&request) {
            Ok(code) => ExitCode::from(code as u8),
            Err(error) => {
                eprintln!("{error}");
                ExitCode::from(1)
            }
        },
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(64)
        }
    }
}
