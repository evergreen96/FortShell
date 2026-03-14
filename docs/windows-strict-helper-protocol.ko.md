# Windows Strict Helper Protocol

이 문서는 향후 Windows 네이티브 strict sandbox helper가 따라야 하는 현재 CLI 계약을 정의합니다.

## 상태

- 현재 런타임 지원 범위: 계약 + validator + dev/test stub
- 현재 보안 수준: production-ready 아님
- 현재 stub: `ai_ide/windows_restricted_host_helper_stub.py`

stub는 launch/wiring 경로를 검증하기 위한 용도일 뿐이며, 실제 sandbox가 아닙니다.

## 탐색 순서

런타임은 아래 순서로 helper를 찾습니다.

1. `AI_IDE_WINDOWS_STRICT_HELPER`
2. `PATH` 상의 `ai-ide-restricted-host-helper.exe`
3. `PATH` 상의 `ai-ide-restricted-host-helper`
4. 없으면 현재 Windows WSL 경로로 fallback

`AI_IDE_WINDOWS_STRICT_HELPER`는 아래 둘 중 하나일 수 있습니다.

- 단일 실행 파일 경로
- 아래 같은 전체 command prefix

```text
python C:\repo\ai_ide\ai_ide\windows_restricted_host_helper_stub.py
```

development shortcut:

```text
rust-dev
```

## 실행 계약

런타임은 helper를 `shell=False`로 실행합니다.

### one-shot 실행

```text
<helper-prefix...>
--workspace <projected-root>
--cwd /workspace
--setenv AI_IDE_RUNNER_MODE strict
--setenv AI_IDE_STRICT_BACKEND restricted-host-helper
--setenv AI_IDE_STRICT_PREVIEW 1
--setenv AI_IDE_SANDBOX_ROOT /workspace
--setenv HOME <helper-home>
--setenv TMPDIR <helper-tmp>
--setenv XDG_CACHE_HOME <helper-cache>
[--setenv <NAME> <VALUE>]...
--command <raw-command-string>
```

### long-running process / stdio proxy 실행

```text
<helper-prefix...>
--workspace <projected-root>
--cwd /workspace
--setenv AI_IDE_RUNNER_MODE strict
--setenv AI_IDE_STRICT_BACKEND restricted-host-helper
--setenv AI_IDE_STRICT_PREVIEW 1
--setenv AI_IDE_SANDBOX_ROOT /workspace
--setenv HOME <helper-home>
--setenv TMPDIR <helper-tmp>
--setenv XDG_CACHE_HOME <helper-cache>
[--setenv <NAME> <VALUE>]...
--stdio-proxy
--control-file <control-file-path>
--response-file <response-file-path>
--argv=<arg0>
--argv=<arg1>
...
```

`--argv=<value>` 형식은 `-u`, `-c` 같은 하이픈 시작 child arg를 helper option으로 잘못 해석하지 않게 하기 위한 것입니다.

## 동작 요구사항

실제 helper는 아래를 만족해야 합니다.

- projected workspace를 `/workspace`로 노출
- 논리 working directory를 `/workspace`로 취급
- 전달된 env 계약을 sandbox 내부에 적용
- helper가 소유한 `HOME`, `TMPDIR`, `XDG_CACHE_HOME`이 결정적인 helper temp base 아래에 머물도록 보장
- direct `--argv=...` child process를 기본적으로 single-process containment 안에 두어 추가 subprocess fan-out을 막음
- explicit executable path는 projected workspace, helper-owned root, system root 아래에 있거나 제공된 PATH로 이미 도달 가능한 같은 binary인 경우에만 허용
- explicit existing file/script argv argument도 projected workspace, helper-owned root, system root 밖이면 거부
- raw shell command 안의 absolute host-path literal과 parent-traversal path segment도 거부
- one-shot 모드에서 raw command string을 실행하고 stdout/stderr/exit code를 전달
- stdio proxy 모드에서 child stdin/stdout/stderr를 전달
- child exit code를 그대로 반환
- 허용된 sandbox 밖의 direct host-path read/write를 막음

## IO 계약

- one-shot 모드에서는 helper 자체가 one-shot process입니다.
- 현재 Rust helper는 child 환경을 최소 allowlist와 명시적 helper override로 다시 만들고, helper `HOME`/`TMPDIR`에서 `USERPROFILE`/`TEMP`/`TMP` 호환 변수를 파생합니다.
- 현재 Rust helper는 `HOME`, `TMPDIR`, `XDG_CACHE_HOME` override가 helper temp base 밖을 가리키면 거부하고, override가 빠지면 workspace 기반의 안정적인 helper root를 스스로 만듭니다.
- 현재 Rust helper는 direct `--argv=...` child를 single-process Job Object 제한 안에서 실행해 기본적인 child fan-out을 막고, stdio-proxy child는 kill-on-close containment도 계속 유지합니다.
- 현재 Rust helper는 explicit argv executable path가 workspace/helper root 밖에 있어도, 제공된 PATH로 이미 같은 binary가 해석되는 경우에만 허용합니다. 즉 absolute host-path launch가 일반 PATH 해석보다 더 많은 권한을 만들지 않도록 합니다.
- 같은 guard는 explicit existing file/script argv argument에도 적용되어, interpreter-style launch가 host-side script path를 그대로 넘겨 boundary를 우회하지 못하게 합니다.
- 현재 Rust helper는 `cmd /C` 전에 raw shell command를 한 번 더 검사해서, obvious absolute host-path literal, drive-relative Windows path form, parent-traversal path segment를 거부합니다.
- 이 guard는 helper가 제공한 `%VAR%` / `!VAR!` path reference를 먼저 확장해서 검사하고, path-like shell token 안에서 unknown environment reference가 나오면 거부합니다. 이 검사는 실제 파일시스템 격리의 대체물이 아니라, shell-command mode가 여전히 shell wrapper에 의존하는 동안 노골적인 escape string을 줄이기 위한 추가 방어막입니다.
- 같은 execution guard는 direct argv와 shell-command mode 모두에서 nested shell launch(`cmd`, `powershell`, `pwsh`, `bash`, `sh`, `zsh`, `fish`)를 거부합니다.
- shell-command mode에서는 `start` builtin도 거부합니다. `start`는 helper가 소유해야 하는 child lifecycle 밖으로 추가 프로세스를 분리할 수 있기 때문입니다.
- shell-command mode에서는 `&&`, `||`, `|`, `;`, `<`, `>`, 괄호 같은 shell control operator도 거부합니다. 즉 남아 있는 shell-wrapper 경로는 chained / piped / redirected command가 아니라 단일 단순 command shape로만 제한됩니다.
- shell-command launch는 이제 helper-owned 두 프로세스 containment 정책(`cmd.exe` + 의도된 child 하나) 안에서 실행됩니다. 즉 shell wrapper가 남아 있더라도 추가 grandchild fan-out은 기본적으로 막습니다.
- 같은 execution guard는 direct argv와 shell-command mode에서 `\\\\server\\share\\tool.cmd`, `\\\\?\\C:\\...`, `\\\\.\\...` 같은 UNC / Windows device path form도 거부합니다.
- helper child-environment builder는 `PATH`, `PATHEXT`, `SystemRoot`, `WINDIR`, `ComSpec`, `USERPROFILE`, `TEMP`, `TMP` 같은 path-sensitive inherited/derived env key override도 거부합니다. 즉 caller가 request-level env injection으로 executable lookup이나 helper-owned home/temp state를 다시 가리키지 못하게 합니다.
- helper child-environment builder는 inherited `PATH`도 절대 로컬 디렉터리만 남기도록 정리하고, `NoDefaultCurrentDirectoryInExePath=1`을 강제로 넣습니다. 또 shell-command launch는 `ComSpec` 또는 `SystemRoot`에서 계산한 explicit `cmd.exe` 경로를 우선 사용합니다.
- 같은 child-environment seam은 inherited `PATHEXT`도 `.COM`, `.EXE`, `.BAT`, `.CMD`만 남기도록 정리합니다. 즉 host 환경이 `.JS`, `.VBS` 같은 확장자를 끼워 넣어도 strict child launch는 그대로 받지 않습니다.
- 같은 child-environment seam은 `SystemRoot`, `WINDIR`, `ComSpec`도 부모 shell env에서 신뢰하지 않고 로컬 Windows system-directory API 기준으로 다시 계산합니다. 즉 hostile `ComSpec` / `SystemRoot` override가 helper shell-command launch를 가로채지 못하게 합니다.
- 같은 child-environment seam은 `PYTHONPATH`, `NODE_PATH`, `CLASSPATH`, `LIBPATH`, `PYTHONHOME` 같은 언어별 path-bearing env override도 projected workspace 밖을 가리키면 거부합니다. 즉 request-level env injection으로 helper-owned root나 system root의 data/library search path를 임의로 변경하는 것을 방지합니다.
- 같은 child-environment seam은 workspace-relative path-bearing env override를 child launch 전에 absolute projected-workspace 경로로 정규화합니다. 즉 child가 예상된 cwd에 의존한 relative library/search path 해석을 안전하게 수행할 수 있습니다.
- 같은 helper/path-policy seam은 `.ai_ide_runtime`, `.ai-ide` 같은 workspace 내부 메타데이터 root도 차단 대상으로 봅니다. 즉 explicit argv path, shell-command path literal, path-bearing env override가 projected workspace 아래에 있다는 이유만으로 helper-managed internal file을 다시 가리키지 못하게 합니다.
- 같은 helper/path-policy seam은 Windows alternate data stream 구문(`file.txt:stream`)도 논리 `cwd`, explicit argv 경로, shell-command 경로 리터럴, path-bearing env override에서 거부합니다. 즉 NTFS stream 경로를 in-root 경로처럼 넘겨 strict helper admission을 우회하지 못하게 합니다.
- 같은 helper/path-policy seam은 `NUL`, `CON`, `COM1` 같은 Windows reserved device name도 logical `cwd`, explicit argv/script path, shell-command literal, path-bearing env override에서 거부합니다. 즉 DOS device alias를 in-root path처럼 취급하지 않게 합니다.
- 같은 helper admission은 bare cwd-relative existing token도 path candidate로 취급합니다. 즉 `dir .ai_ide_runtime`나 `python linked.py`처럼 `./` 없이 넘긴 토큰도 internal-root / hardlink 검사에서 빠지지 않습니다.
- 같은 helper admission은 explicit argv/script 경로, shell-command 경로 리터럴, env-expanded data path를 projected workspace 아래로만 제한하고, explicit native executable path만 projected workspace 또는 trusted system root 아래를 허용합니다. 즉 data/file path reach는 더 좁히고 direct launch 가능한 system tool 범위만 따로 남깁니다.
- 같은 helper admission은 `--config=...`, `/config:...` 같은 attached option value도 path-bearing data argument로 취급합니다. 즉 direct argv나 shell-command가 flag payload 안에 out-of-workspace data path를 숨겨 넘기지 못하게 합니다.
- 같은 실행 guard는 shell-wrapper 경로에서도 `dir`, `type` 같은 filesystem-touching builtin을 거부합니다. 즉 shell mode는 `echo` 같은 non-filesystem builtin만 허용하고, 나머지 명령은 가능한 한 direct-argv 경로로 우회된 launch 형태를 사용합니다.
- helper runtime은 shell-command mode를 `cmd.exe /D /E:OFF /V:OFF /S /C`로 실행합니다. 즉 남아 있는 shell-wrapper 경로에서도 AutoRun hook, command extension, delayed expansion을 기본적으로 끕니다.
- Python 쪽 strict backend invocation builder도 이제 helper-owned control-plane/path-sensitive env key overlay를 먼저 제거한 뒤 helper request를 만듭니다. 즉 helper의 자체 env validation이 돌기 전에도 backend launch contract가 `HOME`/`PATH`/`AI_IDE_STRICT_BACKEND` override에 흔들리지 않습니다.
- stdio proxy 모드에서는 IDE가 helper stdin에 쓴 바이트가 child stdin으로 전달돼야 합니다.
- stdio proxy 모드에서는 런타임이 `--control-file <path>`와 `--response-file <path>`를 함께 넘깁니다.
- helper는 control file을 structured request 채널로, response file을 structured status/reporting 채널로 취급해야 합니다.
- 현재 런타임의 helper-backed process stop 정책은 stdin-close-first입니다. helper stdin의 EOF는 parent가 terminate/kill로 올리기 전에 cooperative shutdown 신호로 해석돼야 합니다.
- helper는 child `stdout`을 자신의 `stdout`으로 전달해야 합니다.
- helper는 child `stderr`를 자신의 `stderr`로 전달해야 합니다.
- helper는 child exit code로 종료해야 합니다.
- 현재 계약은 streaming RPC transport까지 정의하지 않으며, status/control은 file 기반입니다.

현재 런타임 control-file payload 형태:

```json
{
  "version": 1,
  "command": "stop",
  "request_id": "ctl-1234",
  "run_id": "proc-1234",
  "backend": "restricted-host-helper"
}
```

현재 지원하는 `command` 값:

- `stop`: cooperative shutdown 요청
- `kill`: backend-owned 즉시 종료 요청
- `status`: 같은 `request_id`에 대한 structured status payload를 response file에 기록

현재 런타임 response-file payload 형태:

```json
{
  "version": 1,
  "request_id": "status-1234",
  "run_id": "proc-1234",
  "backend": "restricted-host-helper",
  "state": "running",
  "pid": 4242,
  "returncode": null
}
```

현재 지원하는 `state` 값:

- `running`
- `exited`

## direct-path token 계약

Windows helper backend에서는 direct host-path 검사 시 raw host literal을 shell command에 그대로 넣지 않습니다.
현재 런타임은 helper-backed validation에서 아래 형태의 opaque token을 사용합니다.

```text
aiide-helper://host-path/<base64url>
```

실제 helper는 이 token을 내부적으로 해석할 수 있지만, 상위 command guard는 이를 literal host-project path로 취급하지 않습니다.

## validator 규칙

현재 Python validator는 아래를 요구합니다.

- `--workspace <projected-root>`
- `--cwd /workspace`
- `AI_IDE_RUNNER_MODE=strict`
- `AI_IDE_STRICT_BACKEND=restricted-host-helper`
- `AI_IDE_STRICT_PREVIEW=1`
- `AI_IDE_SANDBOX_ROOT=/workspace`
- `--command ...` 또는 `--argv=...`
- process mode에서는 추가로 `--control-file ...` 과 `--response-file ...`

## fixture 기대값

`runner validate`는 helper backend에 대해 아래를 기대합니다.

- `AI_IDE_SANDBOX_ROOT=/workspace`
- `HOME`, `XDG_CACHE_HOME`이 helper temp root 아래에 있음
- `TMPDIR`, `TEMP`, `TMP`도 같은 helper temp root 아래에 있음
- denied relative path hidden
- denied direct path hidden
- direct host write blocked
- projected workspace 안에서만 write 허용

## 현재 한계

- 개발용 Rust 네이티브 helper binary는 이제 존재하지만, 아직 실제 Windows 파일시스템 격리를 제공하지는 않음
- direct shell-command launch는 여전히 shell wrapper를 사용하므로, direct-argv의 single-process containment 대신 `cmd.exe` + child 하나만 허용하는 더 약한 two-process containment를 사용함
- dev/test stub는 host environment를 재사용함
- stub는 실제 파일시스템 격리를 제공하지 않음
- stub는 `runner validate` fixture marker를 흉내낼 뿐 실제 read/write 차단을 강제하지 않음
- stub는 line-oriented stdio proxy만 제공함
- 실제 Windows sandbox semantics는 아직 구현되지 않음
