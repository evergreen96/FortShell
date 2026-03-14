# Windows Strict Helper Protocol

This document defines the current CLI contract for the future Windows native strict sandbox helper.

## Status

- Current runtime support: contract + validator + dev/test stub
- Current security level: not production-ready
- Current stub: `ai_ide/windows_restricted_host_helper_stub.py`

The stub exists only to exercise launch and wiring paths. It is not a sandbox.

## Discovery

The runtime resolves the helper in this order:

1. `AI_IDE_WINDOWS_STRICT_HELPER`
2. `ai-ide-restricted-host-helper.exe` on `PATH`
3. `ai-ide-restricted-host-helper` on `PATH`
4. fall back to the current Windows WSL path

`AI_IDE_WINDOWS_STRICT_HELPER` may be either:

- a single executable path
- or a full command prefix such as:

```text
python C:\repo\ai_ide\ai_ide\windows_restricted_host_helper_stub.py
```

Development-only shortcut:

```text
rust-dev
```

When set to `rust-dev`, the Python runtime resolves the helper to the repo-local Rust helper crate via `cargo run --quiet --manifest-path <repo>\\rust\\Cargo.toml -p ai-ide-windows-helper --`.

## Invocation contract

The runtime launches the helper with `shell=False`.

### One-shot execution

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

One-shot execution may also use direct child argv instead of a shell string:

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
--argv=<arg0>
--argv=<arg1>
...
```

### Long-running process / stdio proxy execution

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

The `--argv=<value>` shape avoids the helper parser misreading hyphen-prefixed child args such as `-u` or `-c` as helper options.

## Behavioral requirements

A real helper must:

- expose the projected workspace as `/workspace`
- treat `/workspace` as the logical working directory
- apply the provided env contract inside the sandbox
- clear inherited child process environments and rebuild them from a minimal allowlist plus explicit helper overrides
- keep helper-owned `HOME`, `TMPDIR`, and `XDG_CACHE_HOME` rooted under a deterministic helper temp base
- execute the raw command string and proxy stdout/stderr in one-shot mode
- execute direct child argv without shell wrapping when one-shot mode is expressed as `--argv=...`
- proxy child stdin/stdout/stderr in stdio-proxy mode
- return the child exit code
- keep stdio-proxy child processes inside a helper-owned process container so helper exit tears down the child tree
- keep direct-argv child processes inside a single-process containment policy so they cannot fan out extra subprocesses by default
- reject nested shell launches (`cmd`, `powershell`, `pwsh`, `bash`, `sh`, `zsh`, `fish`) in direct argv and shell-command mode
- reject the `start` builtin in shell-command mode
- reject shell control operators such as `&&`, `||`, `|`, `;`, `<`, `>`, and parentheses in shell-command mode
- keep shell-command launches inside a two-process containment policy (`cmd.exe` plus one intended child) so they cannot fan out extra grandchildren by default
- reject UNC and Windows device-path forms such as `\\\\server\\share\\tool.cmd`, `\\\\?\\C:\\...`, and `\\\\.\\...` in direct argv and shell-command mode
- reject override attempts for helper-sensitive inherited or derived env keys such as `PATH`, `PATHEXT`, `SystemRoot`, `WINDIR`, `ComSpec`, `USERPROFILE`, `TEMP`, and `TMP`
- sanitize inherited `PATH` to absolute local directories, force `NoDefaultCurrentDirectoryInExePath=1`, and prefer an explicit `cmd.exe` path from `ComSpec`/`SystemRoot` for shell-command launches
- sanitize inherited `PATHEXT` down to standard executable extensions (`.COM`, `.EXE`, `.BAT`, `.CMD`)
- derive `SystemRoot`, `WINDIR`, and `ComSpec` from local Windows system-directory APIs instead of trusting inherited parent-shell values
- treat workspace-internal metadata roots such as `.ai_ide_runtime` and `.ai-ide` as blocked targets for explicit argv paths, shell-command path literals, and path-bearing env overrides
- reject logical `/workspace/...` working directories that target those same internal metadata roots before child launch begins
- reject workspace paths that traverse Windows reparse points (for example junctions) before cwd mapping, explicit argv admission, shell-command path admission, or path-bearing env admission can treat them like ordinary in-root paths
- apply the same reparse-point rejection to helper-owned mutable roots such as `HOME`, `TMPDIR`, and `XDG_CACHE_HOME`
- fail helper-environment layout resolution itself if the requested helper root under `ai_ide_strict_helper` traverses a reparse point
- reject hardlink aliases under the projected workspace or helper-owned mutable roots during explicit argv admission, shell-command path admission, and path-bearing env admission
- reject Windows alternate-data-stream path syntax (`file.txt:stream`) in logical `cwd`, explicit argv paths, shell-command path literals, and path-bearing env admission
- treat bare existing cwd-relative tokens as path candidates during argv and shell-command admission, so blocked internal roots or alias-backed files cannot bypass helper checks merely by omitting `./` or path separators
- reject Windows root-relative path forms such as `\\Users\\Public\\secret.txt` and `/Users/Public/secret.txt` in explicit argv paths, shell-command path literals, and path-bearing env admission, so the helper does not fall back to current-drive root resolution outside the projected workspace
- reject Windows reserved device names such as `NUL`, `CON`, and `COM1` in logical `cwd`, explicit argv/script paths, shell-command path literals, and path-bearing env admission
- require non-builtin bare shell-command programs to resolve through the helper-owned sanitized `PATH` before launch, while only non-filesystem builtins such as `echo` remain admissible on the shell-wrapper path
- when a shell-command string is a single non-builtin command that resolves to a native executable (`.exe` / `.com`), prefer the helper direct-argv path instead of `cmd.exe`
- the same native-executable handoff also applies when the invoked executable token is a helper-expanded `%VAR%` / `!VAR!` reference that resolves to an admitted `.exe` / `.com` path
- the same direct-argv handoff also applies when a simple native-executable shell command is unnecessarily wrapped in `call`, so `call <native-exe> ...` does not stay on raw shell text
- the same direct-argv handoff also applies when non-invoked `%VAR%` / `!VAR!` arguments expand to single-token argv-safe values, so simple helper-expanded data arguments do not force a fallback to raw shell text
- the same direct-argv handoff also preserves quoted env-expanded whitespace arguments as single argv tokens, so `"%VAR_WITH_SPACES%"` does not need the weaker shell wrapper just to preserve token boundaries
- treat attached option values such as `--config=...` and `/config:...` as filesystem-bearing arguments during direct argv and shell-command admission so internal-root and path-safety checks still apply
- when a shell-command string is a single non-builtin batch-script launch (`.cmd` / `.bat`), require an explicit workspace script path (including `%VAR%`-expanded `call <script>` forms) and prefer an explicit `cmd.exe /D /E:OFF /V:OFF /S /C <script> ...` argv wrapper instead of a raw shell command string
- reject `%`-bearing batch arguments after helper env expansion, so admitted structured batch launches do not hand `cmd.exe` a second round of percent-based variable interpolation
- treat simple `echo ...` shell commands as a helper-local builtin so env-expanded metacharacters are emitted as literal text instead of being reinterpreted by `cmd.exe`
- reject non-batch script launches such as `helper.py` on the shell-command path, so the helper does not rely on Windows file associations or implicit interpreter lookup through `cmd.exe`
- reject remaining admitted shell-command shapes that cannot be lowered into helper-local `echo`, direct argv, or structured batch launch, so strict mode does not keep a generic raw `cmd.exe /C <text>` fallback alive
- reject filesystem-touching shell builtins such as `dir`, `type`, `copy`, `del`, `move`, `ren`, `mkdir`, and `rmdir`; only non-filesystem builtins like `echo` remain admissible on the shell-wrapper path
- launch shell-command mode through `cmd.exe /D /E:OFF /V:OFF /S /C` so AutoRun hooks, command extensions, and delayed expansion are disabled by default

## IO contract

- In one-shot mode, the helper itself is a one-shot process.
- The current Rust helper implementation clears the inherited child environment, keeps only a small platform allowlist (for example `PATH`, `SystemRoot`, `ComSpec`, `WINDIR`, `PATHEXT` on Windows), then applies helper-provided overrides.
- On Windows, the helper currently derives `USERPROFILE` from helper `HOME` and `TEMP`/`TMP` from helper `TMPDIR` when those compatibility variables were not explicitly provided.
- The current Rust helper implementation also validates that helper `HOME`, `TMPDIR`, and `XDG_CACHE_HOME` stay under a helper-owned temp base; if those overrides are missing, it derives a stable helper root from the workspace path.
- In stdio-proxy mode, bytes written by the IDE to helper stdin must reach child stdin.
- In stdio-proxy mode, the runtime also passes `--control-file <path>` and `--response-file <path>`.
- A helper should treat the control file as a structured request channel and the response file as a structured status/reporting channel.
- The current runtime stop policy for helper-backed process runs is stdin-close-first: EOF on helper stdin should be treated as a cooperative shutdown signal before the parent escalates to terminate/kill.
- The helper must proxy child `stdout` to its own `stdout`.
- The helper must proxy child `stderr` to its own `stderr`.
- The helper must exit with the child's exit code.
- The current Rust helper implementation also places stdio-proxy children in a kill-on-close Job Object, so killing the helper tears down the child process.
- The current Rust helper implementation also runs direct `--argv=...` children under a single-process Job Object limit, so direct child-process fan-out is denied by default for argv-backed launches.
- The current Rust helper also applies a syntactic shell-command guard before `cmd /C`, rejecting obvious absolute host-path literals, drive-relative Windows path forms, and parent-traversal path segments in raw command strings.
- That guard resolves helper-provided `%VAR%` / `!VAR!` path references before validation, and rejects unknown environment references when they appear inside a path-like shell token. This is intentionally narrower than real filesystem isolation and exists to reduce obvious escape paths while shell-command mode still relies on a shell wrapper.
- The same execution guard now also rejects nested shell invocations in direct argv and shell-command mode, and shell-command mode rejects `start` entirely because it can detach additional processes outside the intended helper-owned child lifecycle.
- Shell-command mode now also rejects shell control operators outright, so the remaining shell-wrapper path is limited to one simple command shape instead of chained, piped, or redirected shell programs.
- Shell-command launches now also run inside a helper-owned two-process job policy, which intentionally allows the wrapper shell plus one intended child process but blocks further process fan-out from that weaker shell-backed path.
- The same execution guard now also rejects UNC and Windows device-path forms in direct argv and shell-command mode, so the helper does not treat UNC shares or verbatim/device namespaces as ordinary host paths that can slip past the existing drive/path admission rules.
- The helper child-environment builder now rejects request-level overrides for path-sensitive inherited or derived env keys, so the caller cannot redirect executable lookup or helper-owned home/temp state by injecting `PATH`, `ComSpec`, `USERPROFILE`, `TEMP`, `TMP`, and related variables.
- The helper child-environment builder now also sanitizes inherited `PATH` down to absolute local directories, forces `NoDefaultCurrentDirectoryInExePath=1`, and the runtime prefers an explicit `cmd.exe` path from `ComSpec` or `SystemRoot`, so shell-command mode no longer depends on relative/current-directory path search before real filesystem isolation exists.
- The same child-environment seam also sanitizes inherited `PATHEXT` down to standard executable extensions only, so strict child launches do not inherit arbitrary script-association suffixes through host environment state.
- The same child-environment seam now derives `SystemRoot`, `WINDIR`, and `ComSpec` from the local Windows system directory instead of inherited parent-shell values, so hostile `ComSpec` / `SystemRoot` / `WINDIR` environment state cannot hijack helper shell-command launches.
- The helper path-policy seam also treats workspace-internal metadata roots such as `.ai_ide_runtime` and `.ai-ide` as blocked targets, so explicit argv/script paths, shell-command path literals, and path-bearing env overrides cannot point back into helper-managed internal files even though those paths technically sit under the projected workspace root.
- The helper runtime now also launches shell-command mode with `cmd.exe /D /E:OFF /V:OFF /S /C`, which disables AutoRun hooks plus most extension/delayed-expansion shell features on the weaker shell-backed path.
- The Python-side strict backend invocation builders also now drop caller overlays for helper-owned control-plane and path-sensitive env keys before the helper request is even constructed, so backend launch contracts stay stable even before the helper's own env validation runs.
- The current contract still does not define a streaming RPC transport; status/control remain file-backed.

Current runtime control-file payload shape:

```json
{
  "version": 1,
  "command": "stop",
  "request_id": "ctl-1234",
  "run_id": "proc-1234",
  "backend": "restricted-host-helper"
}
```

Currently recognized `command` values:

- `stop`: cooperative shutdown request
- `kill`: immediate backend-owned termination request
- `status`: write a structured status payload to the response file for the matching `request_id`

Current runtime response-file payload shape:

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

Currently recognized `state` values:

- `running`
- `exited`

## Direct-path token contract

For the Windows helper backend, direct host-path checks do not use raw host literals in the shell command.
The runtime currently hands helper-backed validation commands opaque tokens shaped like:

```text
aiide-helper://host-path/<base64url>
```

A real helper may interpret those tokens internally, but user-level command guards do not treat them as direct host-project path literals.

## Validation rules

The Python validator currently requires:

- `--workspace <projected-root>`
- `--cwd /workspace`
- `AI_IDE_RUNNER_MODE=strict`
- `AI_IDE_STRICT_BACKEND=restricted-host-helper`
- `AI_IDE_STRICT_PREVIEW=1`
- `AI_IDE_SANDBOX_ROOT=/workspace`
- `--command ...` or `--argv=...`
- only stdio-proxy process mode additionally requires `--control-file ...` and `--response-file ...`

## Fixture expectations

`runner validate` currently expects helper-backed runs to report:

- `AI_IDE_SANDBOX_ROOT=/workspace`
- `HOME` and `XDG_CACHE_HOME` under the helper temp root
- `TMPDIR`, `TEMP`, and `TMP` under the same helper temp root
- denied relative path hidden
- denied direct path hidden
- direct host write blocked
- writes only inside the projected workspace

## Known gaps

- A development-grade native Rust helper binary now exists, but it still does not provide real Windows filesystem isolation
- Direct shell-command launches still rely on a shell wrapper, so they now use a weaker two-process containment rule (`cmd.exe` plus one intended child) instead of the stricter direct-argv single-process rule
- The dev/test stub reuses the host environment
- The stub does not provide filesystem isolation
- The dev/test stub emulates `runner validate` fixture markers instead of enforcing real read/write denial
- The dev/test stub only provides a line-oriented stdio proxy path for launch/process integration testing
- Real Windows sandbox semantics still need implementation
