# AI IDE Prototype

AI-first IDE MVP prototype focused on policy-enforced AI tooling rather than code editor UI.

## Research docs

- `docs/requirements.md`
- `docs/requirements.ko.md`
- `docs/architecture-research.md`
- `docs/architecture-research.ko.md`
- `docs/spec-log.ko.md`
- `docs/architecture-guardrails.ko.md`
- `docs/windows-strict-helper-protocol.md`
- `docs/windows-strict-helper-protocol.ko.md`

## What this prototype includes

- Policy-enforced `ToolBroker` for AI file tools (`ls`, `read`, `write`, `grep`)
- File/folder deny rules (checkbox-like exclusions) that are enforced at tool-result level
- Project-level policy persistence under `.ai-ide/policy.json`, hidden from AI file discovery and projected workspaces
- Explicit staged write-review flow with persisted proposals, diff inspection, apply, reject, and conflict detection
- Persistent broker metrics and audit history under the runtime root, with CLI inspection for allowed vs blocked operations
- Split session model for execution sessions, agent sessions, and terminal sessions
- Session manager that auto-rotates execution and agent sessions when policy changes
- Agent adapter registry for concrete CLIs such as Claude, Codex, Gemini, and OpenCode
- Agent runtime manager that launches the current agent session through the runner boundary
- Structured event bus for agent lifecycle/output and terminal messaging events
- Projected workspace runner that executes commands against a filtered workspace view
- Multi-terminal manager with runner-managed terminals, explicit unsafe host terminals, and lightweight inter-terminal messaging
- Usage and audit counters (read/write/list/grep/blocked and terminal command stats)

## Quick start

```bash
python -m ai_ide.main
```

Enable the Rust-backed control-plane bridge:

```bash
AI_IDE_USE_RUST_HOST=1 python -m ai_ide.main
```

When enabled, app startup adopts the Rust host's current execution/agent session snapshot, command boundaries resync the sidecar from the shared policy store before dispatch, `policy add/remove` and `agent rotate` keep Python session state synced to that snapshot, `review show` renders through the Rust bridge, `workspace list|tree|grep` uses the same Rust-backed policy-enforced catalog contract without incrementing AI broker metrics, and `workspace index show|refresh` uses the Rust-backed persisted index contract.

Optional overrides:

- `AI_IDE_RUST_HOST_BIN=/path/to/ai-ide-adapter`
- `AI_IDE_RUST_HOST_DEFAULT_AGENT_KIND=codex`
- `AI_IDE_RUST_HOST_POLICY_STORE=/custom/policy.json`
- `AI_IDE_RUST_HOST_REVIEW_STORE=/custom/reviews.json`
- `AI_IDE_RUST_HOST_WORKSPACE_INDEX_STORE=/custom/workspace-index.json`
- `AI_IDE_RUST_HOST_BROKER_STORE=/custom/broker-state.json`
- `AI_IDE_WINDOWS_STRICT_HELPER=C:\\path\\to\\ai-ide-restricted-host-helper.exe`
  - may also be a full command prefix such as `python C:\\repo\\ai_ide\\ai_ide\\windows_restricted_host_helper_stub.py`
  - development-only shortcut: `AI_IDE_WINDOWS_STRICT_HELPER=rust-dev` resolves to the repo-local Rust helper crate through `cargo run`

## Run tests

The Python runtime now centralizes visible-path resolution in a shared workspace-access seam, and a dedicated workspace-catalog seam shapes typed list/tree/search results for broker commands and future IDE file surfaces.

```bash
python -m unittest discover -s tests -v
```

Rust fast baseline:

```bash
python rust/scripts/test_rust.py
```

Rust fast baseline + long Windows helper integration suite:

```bash
python rust/scripts/test_rust.py --include-helper-binary
```

Windows helper integration suite only:

```bash
python rust/scripts/test_rust.py --helper-binary-only
```

The Rust helper split is intentional. The default fast baseline excludes `tests/helper_binary.rs`, which currently contains 168 serialized Windows helper integration tests and can exceed generic command timeouts. The helper protocol contract still stays in the fast baseline through `--lib --test helper_protocol`.

## Desktop shell

Experimental Stage 7 desktop scaffold now lives under `desktop/`.

Run the Python UI API first:

```bash
python -m ai_ide.ui_server
```

Then start the Tauri + React shell:

```bash
cd desktop
npm install
npm run tauri dev
```

The desktop shell talks to the existing UI server over HTTP. By default it targets `http://127.0.0.1:8765`; override with `VITE_AI_IDE_API_BASE` if the server is running elsewhere.

Then try:

```text
help
status
status json
session show
session show json
workspace list . json
workspace tree . json
workspace grep todo . json
workspace index show json
workspace index refresh json
agent show
agent rotate claude
agent registry
agent plan codex
agent transport codex --mode strict
agent exec -- --version
agent start -- --version
agent send run-12345678 hello
agent poll run-12345678
agent stop run-12345678
agent watch run-12345678 observer --replay
agent watches
agent inbox agent-watch-12345678 20
agent unwatch agent-watch-12345678
agent gc 86400
agent history
agent history all
agent show json
agent poll run-12345678 json
agent inbox agent-watch-12345678 20 json
events list 20
events tail evt-000001 20 agent.run
events cursor ui-main
events pull ui-main 20 agent.run
events pull ui-main 20 terminal.message terminal term-123456
events ack ui-main evt-000010
events compact 200
events gc 86400
term watch term-123456 agent.run json
term watches term-123456 json
term inbox term-123456 json
term attach term-123456 run-12345678 json
term input term-123456 hello
term gc 86400
policy add secrets/**
ai ls .
ai write notes/todo.txt hello
review list pending 20
review apply rev-12345678
unsafe write notes/todo.txt emergency-hotfix
ai read notes/todo.txt
review stage notes/todo.txt hello-from-review
review show rev-12345678
runner show
runner exec python -c "import os; print('\\n'.join(sorted(os.listdir('.'))))"
runner mode strict
runner exec curl https://example.com
metrics
metrics json
audit list 20 blocked
```

## CLI commands

- `help`
- `status`
- `status json`
- `policy show`
- `policy add <glob>`
- `policy remove <glob>`
- `session show`
- `session show json`
- `agent show`
- `agent rotate [kind]`
- `agent list`
- `agent registry`
- `agent plan [kind]`
- `agent transport [kind] [--mode <projected|strict>]`
- `agent exec [--mode <projected|strict>] [-- <args...>]`
- `agent start [--mode <projected|strict>] [-- <args...>]`
- `agent send <run_id> <text>`
- `agent poll <run_id>`
- `agent stop <run_id>`
- `agent watch <run_id> [name] [--replay]`
- `agent watches [run_id]`
- `agent inbox <watch_id> [limit]`
- `agent unwatch <watch_id>`
- `agent gc <max_age_seconds>`
- `agent history [auto|current|all]`
- `events list [limit] [kind_prefix|none] [source_type|none] [source_id|none]`
- `events tail <after_event_id> [limit] [kind_prefix|none] [source_type|none] [source_id|none]`
- `events cursor <consumer_id>`
- `events ack <consumer_id> <event_id|none>`
- `events pull <consumer_id> [limit] [kind_prefix|none] [source_type|none] [source_id|none]`
- `events compact <retain_last>`
- `events gc <max_age_seconds>`
- `review list [status|all] [limit]`
- `review stage <file> <text>`
- `review show <proposal_id>`
- `review apply <proposal_id>`
- `review reject <proposal_id>`
- `workspace list [dir] [json]`
- `workspace tree [dir] [json]`
- `workspace grep <pattern> [dir] [json]`
- `workspace index [show|refresh] [json]`
- `ai ls <dir>`
- `ai read <file>`
- `ai write <file> <text>`
- `unsafe write <file> <text>`
- `ai grep <pattern> [dir]`
- `term new [--host] [--mode <projected|strict>] [name]`
- `term list [json]`
- `term show <id> [json]`
- `term run <terminal_id> <command...>`
- `term msg <from_terminal_id> <to_terminal_id> <message...>`
- `term watch <id> <kind_prefix> [source_type] [source_id] [json]`
- `term watches <id> [json]`
- `term inbox <id> [json]`
- `term attach <id> <run_id> [json]`
- `term input <id> <text>`
- `term gc <max_age_seconds>`
- `runner show`
- `runner info`
- `runner probe`
- `runner validate [json]`
- `runner mode <host|projected|strict>`
- `runner refresh`
- `runner exec <command...>`
- `metrics`
- `metrics json`
- `audit list [limit] [all|allowed|blocked]`
- `exit`

## Notes

- This is a backend/agent runtime prototype, not a full GUI IDE.
- Commands are intentionally scoped to demonstrate enforced access control.
- Denied files are hidden from list/grep and blocked for read/write.
- `ai write` now stages a review proposal by default instead of mutating the host file immediately.
- Direct host mutation is now outside the AI tool namespace; `unsafe write` is the explicit override path for the PoC.
- `unsafe write` is now treated as a trusted control-plane command. The interactive CLI runs with trusted user context, but future agent/untrusted callers are blocked at the command boundary.
- `status json`, `session show json`, `metrics json`, and `workspace list|tree|grep|index ... json` now expose machine-readable control-plane snapshots so future Rust/TS surfaces do not have to parse human CLI strings.
- `workspace index show|refresh` persists a policy-versioned visible workspace snapshot under the runtime root so future IDE caches/indexes can detect stale policy state instead of leaking denied paths from old scans; when the Rust host bridge is enabled the same command surface routes through the Rust-backed index store.
- `workspace index show` now also validates the cached snapshot against the current visible workspace and reports `stale_reasons` (`policy` or `workspace`) so external visible file changes do not look fresh just because the policy version stayed the same.
- Workspace index snapshots now carry the same deterministic visible-workspace signature used by strict-backend validation staleness, so index freshness and runner-health checks share one fingerprint algorithm instead of drifting across two separate tree-comparison paths.
- The runtime now has a dedicated `WorkspaceVisibilityMonitor` seam that polls the same visible-workspace fingerprint and publishes `workspace.visible.changed` events for app-mediated mutations and externally detected visible-tree changes, which gives future watcher/daemon work one event contract to replace instead of wiring stale checks ad hoc in multiple commands.
- `workspace index show` and `runner info` now consume the visibility monitor's current fingerprint state instead of rescanning the visible tree independently after each command-boundary poll, which keeps stale decisions aligned while reducing duplicate traversal work.
- The visibility monitor now persists its last acknowledged visible-workspace state under the runtime root, so a restarted runtime can emit `workspace.visible.changed` on the first command after external edits instead of silently treating the new tree as the baseline.
- The visibility monitor now depends on a dedicated workspace-visibility source seam instead of directly owning snapshot-builder logic, so a future file-watcher backend can replace just the source implementation while leaving event/state/staleness wiring intact.
- The visibility monitor now also depends on a dedicated backend seam that owns current-state caching and poll/sync behavior, so a future push-driven watcher can replace polling without changing the monitor's persisted-baseline and event-publication responsibilities.
- The visibility backend contract now includes explicit `start`/`close` lifecycle hooks and optional push-style change callbacks, while the monitor uses an internal lock to serialize poll and callback updates before publishing events or persisting new baselines.
- The visibility stack now includes an explicit event-driven backend shell over a watcher protocol, so a future OS/file-watcher adapter can plug into the same backend contract instead of re-implementing state refresh and callback delivery from scratch.
- Workspace visibility backend selection now goes through a dedicated runtime factory, and the codebase includes a queue-signaled watcher skeleton that can drive the event-backed path end to end for tests and future OS-specific watcher adapters.
- Event-driven visibility backends now explicitly opt out of command-boundary polling, so the watcher path no longer pays the extra visible-tree rescan cost on every command once a push-driven backend is active.
- Platform adapters now expose the default workspace-visibility watcher seam, and the runtime resolves `override -> platform default -> polling fallback` in one place so future OS-specific watcher rollout will not require another app-construction refactor.
- Windows strict-backend probing now has a native-helper seam ahead of WSL: if `AI_IDE_WINDOWS_STRICT_HELPER` or an `ai-ide-restricted-host-helper(.exe)` binary is present, the runtime builds a validator-checked `restricted-host-helper` invocation instead of falling straight to the currently invalid WSL contract.
- The Windows helper override now accepts a full command prefix, not just a single binary path, and the repository includes a development-only Python stub helper that exercises the runner path end to end without claiming to provide real isolation.
- Strict-backend fixture expectations now come from the platform layer, so helper-backed validation can describe different `HOME`/cache prefixes without hard-coding Linux-style `/tmp/...` assumptions into the shared fixture service.
- The development-only Windows helper stub now emulates the `runner validate` fixture markers and uses opaque helper-visible direct-path tokens, so the helper-backed strict-validation path can be exercised end to end without teaching the command guard about literal host project paths.
- The Windows helper path now has an explicit stdio-proxy process contract: strict `start_process` launches use `--stdio-proxy` plus repeated `--argv=...` tokens, and the dev/test stub forwards line-oriented stdin/stdout/stderr so strict agent runs can be exercised through the helper seam before a native binary exists.
- Helper-backed strict process runs now also carry an explicit file-control contract: the runtime writes `stop` into a per-run control file before escalating to stdin-close/terminate/kill, which gives the future native helper a narrow cooperative stop channel without coupling process-stop policy to agent supervision.
- The helper control-file payload is now structured JSON (`version`, `command`, `request_id`, `run_id`, `backend`) and is written atomically, so future native helpers can extend control semantics without changing the higher-level runner stop flow.
- Helper-backed strict process runs now also carry a dedicated response-file contract, and the runtime owns a separate `ProcessControlService` seam for file-backed control/status requests instead of mixing helper RPC writes into process-stop policy.
- The helper control channel now distinguishes cooperative `stop`, backend-owned `kill`, and structured `status` reporting, so strict helper-backed runs can grow toward a native helper-owned control plane before the parent process falls back to `terminate()` / `kill()`.
- The Rust workspace now includes an `ai-ide-windows-helper` crate that mirrors the Windows helper CLI/control/status protocol and provides a native binary skeleton, so helper-specific parsing and file-message semantics can move under Rust before the real Windows sandbox backend exists.
- That Rust helper crate now executes a development-grade one-shot/fixture/stdio-proxy subset of the helper contract, including file-backed `status` and `kill` handling, so native-helper behavior can be exercised under Rust without yet claiming real Windows isolation.
- The Python runner test suite now also exercises the development-only `AI_IDE_WINDOWS_STRICT_HELPER=rust-dev` path end to end for one-shot execution, strict validation, and process streaming, so the existing Windows helper seam can be smoke-tested against the Rust helper without swapping the default stub.
- Windows helper one-shot strict runs now accept direct `--argv=...` execution without forcing a shell command string, which removes quoting drift between runner one-shots and agent `exec` flows and lets the `rust-dev` helper path cover `run_process` execution as well as streaming.
- The Rust Windows helper now assigns stdio-proxy child processes to a helper-owned kill-on-close Job Object, so abrupt helper termination does not leave orphaned strict child processes behind even before full Windows filesystem isolation exists.
- The Rust Windows helper now clears inherited child process environments and rebuilds them from an allowlist plus explicit helper overrides, including derived `USERPROFILE`/`TEMP`/`TMP` compatibility from helper-owned `HOME` and `TMPDIR`, so strict child runs no longer inherit arbitrary parent-shell secrets by default.
- The Rust Windows helper now validates that helper-owned `HOME`, `TMPDIR`, and `XDG_CACHE_HOME` roots stay under a deterministic helper temp base (and derives that base when overrides are missing), so strict child runs cannot silently redirect their sandbox-owned state back to arbitrary host locations through helper env overrides.
- The Rust Windows helper now runs direct-argv child processes inside a single-process job policy in addition to kill-on-close containment, so helper-backed direct launches cannot fan out extra child processes unless they go through the narrower shell-command path that still lacks full Windows filesystem isolation.
- The Rust Windows helper now applies a separate shell-command guard before `cmd /C` execution, rejecting absolute host-path literals and parent-traversal path segments in raw shell-command mode, so the still-weaker shell wrapper path does not accept obvious host escape strings even before real filesystem isolation exists.
- That shell-command guard now also resolves helper-provided `%VAR%` / `!VAR!` path references before validation and rejects drive-relative Windows paths such as `C:secret.txt`, so shell mode cannot bypass helper roots through env-expanded literals or current-drive-relative path forms.
- The same helper execution guard now also rejects nested shell launches (`powershell`, `cmd`, `pwsh`, `bash`, etc.) in direct argv mode and shell-command mode, and blocks the `start` builtin in shell mode, so helper-backed strict runs are less dependent on higher-level Python command guards for obvious shell escalation paths.
- The helper child-environment policy now also rejects overrides for path-sensitive inherited or derived variables such as `PATH`, `SystemRoot`, `ComSpec`, `USERPROFILE`, `TEMP`, and `TMP`, so strict helper callers cannot re-point executable lookup or helper-owned home/temp state through request-level env injection.
- The Rust Windows helper now sanitizes inherited `PATH` to absolute local directories only, forces `NoDefaultCurrentDirectoryInExePath=1`, and uses an explicit `cmd.exe` path from helper-owned environment data for shell-command mode, so strict child launches no longer depend on current-directory search or relative/UNC `PATH` entries before real filesystem isolation exists.
- The same helper child-environment policy now also sanitizes inherited `PATHEXT` down to standard executable extensions (`.COM`, `.EXE`, `.BAT`, `.CMD`), so strict child launches do not inherit arbitrary shell-association extensions such as `.JS` or `.VBS`.
- The helper shell-command path now launches `cmd.exe` with `/D /E:OFF /V:OFF /S /C`, so AutoRun hooks, command extensions, and delayed expansion are disabled by default on the remaining shell-wrapper path.
- The Rust Windows helper now derives `SystemRoot`, `WINDIR`, and `ComSpec` from local Windows API state instead of trusting inherited parent-shell values, so shell-command mode ignores hostile host `ComSpec` / `SystemRoot` overrides before real filesystem isolation exists.
- Platform strict-backend builders now also drop caller overlays for helper-owned control-plane and path-sensitive env keys before constructing WSL, `bwrap`, or Windows helper invocations, so backend launch contracts do not depend on lower layers to undo `HOME`/`PATH`/`AI_IDE_STRICT_BACKEND`-style overrides after the fact.
- The same helper/path-policy seam now also treats workspace-internal metadata roots such as `.ai_ide_runtime` and `.ai-ide` as blocked targets, so direct argv paths, shell-command literals, and path-bearing env overrides cannot point back into helper-managed internal files just because they live under the projected workspace root.
- The helper runtime now also rejects `/workspace/...` logical working directories that target `.ai_ide_runtime` or `.ai-ide`, so strict launches fail before child startup instead of letting helper-managed metadata subtrees behave like normal working directories.
- The same helper path-policy seam now also rejects workspace paths that traverse Windows reparse points such as junctions before launch, so strict helper cwd/env/path admission does not treat workspace-local aliases to outside content as normal in-root paths.
- The same helper reparse-point guard now also applies to helper-owned mutable roots (`HOME`, `TMPDIR`, `XDG_CACHE_HOME`), so strict env/path admission does not trust junction-backed aliases just because they sit under helper-managed temp directories.
- Helper environment roots themselves now also fail closed if the requested `HOME`/`TMPDIR`/`XDG_CACHE_HOME` layout traverses a reparse point under `ai_ide_strict_helper`, so override validation does not accept a junction-backed helper root and only discover the alias later during child launch.
- The same helper path admission now also rejects hardlink aliases under the projected workspace or helper-owned mutable roots, so explicit script paths and path-bearing env overrides cannot smuggle host-side files through in-root hardlinks.
- The same helper path-policy seam now also rejects Windows alternate-data-stream (`file.txt:stream`) paths in logical `cwd`, explicit argv/script paths, shell-command literals, and path-bearing env overrides, so strict launches do not treat NTFS stream syntax as an in-root path escape hatch.
- The same helper admission now also treats bare existing cwd-relative tokens as path candidates, so `dir .ai_ide_runtime` or `python linked.py` cannot bypass internal-root or hardlink checks just by omitting `./` or path separators.
- The same helper path-policy seam now also rejects Windows reserved device names such as `NUL`, `CON`, and `COM1` in logical `cwd`, explicit argv/script paths, shell-command literals, and path-bearing env overrides, so strict launches do not treat DOS device aliases as admissible in-root paths.
- The same helper shell-command guard now also requires non-builtin bare commands to resolve through the helper-owned sanitized `PATH` before launch, so shell mode no longer relies on `cmd.exe` to discover arbitrary unresolved program names outside the helper’s admitted roots.
- Simple non-builtin shell commands that resolve to native executables now bypass `cmd.exe` and run through the helper direct-argv path, so they inherit the tighter single-process containment instead of the weaker shell-wrapper path whenever no shell-only behavior is needed.
- Simple shell-command launches that use explicit workspace `.cmd`/`.bat` paths, including `%WORKSPACE_TOOL%`-style `call` patterns, now bypass raw shell text and run through an explicit `cmd.exe` argv wrapper. Bare batch-script lookup through helper PATH is no longer admitted on the shell path.
- The same structured batch path now rejects `%`-bearing batch arguments after helper env expansion, so `cmd.exe` does not get a second chance to reinterpret percent-based variable syntax inside admitted batch launches.
- Simple shell-command launches that resolve to native executables now also allow env-expanded executable references such as `%WORKSPACE_WHERE%`, so admitted `%VAR%` launcher paths can drop to direct argv instead of staying on raw shell text.
- The same helper direct-argv handoff now also treats `call <native-exe> ...` as a direct native launch instead of leaving that shape on raw shell text, so an unnecessary `call` wrapper does not keep native executable launches on the weaker `cmd.exe` path.
- The same helper direct-argv handoff now also keeps simple env-expanded native-executable arguments on the direct path when the expanded values remain single-token argv-safe strings, so more non-shell data passing avoids the weaker `cmd.exe` wrapper without changing tokenization semantics.
- The same helper direct-argv handoff now also preserves quoted env-expanded whitespace arguments on the direct path, so commands like `python script.py \"%VAR_WITH_SPACES%\"` no longer fall back to `cmd.exe` just to keep a single argv token.
- Simple `echo ...` shell commands now execute as a helper-local builtin instead of going through `cmd.exe`, so even env-expanded values containing shell metacharacters stay literal text and do not keep the remaining shell-wrapper path alive for non-filesystem output.
- The helper now also treats attached option values such as `--config=...` and `/config:...` as filesystem-bearing arguments, so direct argv and shell-command launches cannot smuggle out-of-workspace data paths through flag payloads that previously looked like ordinary options.
- The Windows helper now exposes a dedicated filesystem-boundary layout seam over the projected workspace, helper-owned mutable roots, and blocked internal metadata roots, and its restricted-token probe now reports capability availability instead of failing outright on hosts where the underlying Windows primitive is unavailable.
- The same helper boundary seam now also centralizes the allowed executable roots, allowed data roots, and helper-owned mutable roots used by argv and env admission, so upcoming filesystem-boundary enforcement can reuse one root model instead of re-deriving Windows path scope separately in each guard.
- The first Windows helper filesystem-boundary slice is now live for admitted launches: helper-managed internal metadata roots (`.ai_ide_runtime`, `.ai-ide`) are staged out of the projected workspace while one-shot direct argv, structured batch, and stdio-proxy launches run, and one-shot direct argv plus one-shot structured batch use a restricted token when the host supports it. This is still narrower than full host-path isolation, but it is no longer only string-level admission hardening.
- The same Windows helper boundary slice now also covers admitted stdio-proxy direct launches: direct `--argv ...` stdio-proxy runs, shell commands that lower into direct argv, and structured workspace batch launches now use the helper-owned restricted token when the host supports it, so interactive strict runs are no longer limited to unrestricted parent-token execution.
- The same restricted-launch path now also stages a low-integrity write boundary when the host supports it: the helper labels the projected workspace root plus helper-owned `HOME`/`TMPDIR`/`XDG_CACHE_HOME` roots as low-integrity targets before launching a restricted child, so direct-argv strict runs can create files inside allowed roots while writes to ordinary host directories fail closed under the Windows integrity model.
- The same internal-root staging path now also applies a medium-integrity `no-read-up`/`no-execute-up` guard to staged `.ai_ide_runtime` / `.ai-ide` trees, so a restricted child cannot recover those hidden metadata files just by guessing the helper-side hidden path during the run.
- The helper now also prepares a helper-owned blocked-read root for restricted launches and labels it with the same medium-integrity read/execute guard, so a restricted child can still read files inside the projected workspace while an explicitly known helper-side blocked path fails closed at runtime.
- External blocked-read roots are now staged with a saved/restored label snapshot for the lifetime of a restricted launch, so helper-driven host-path deny tests do not leave persistent mandatory-label mutations behind after the child exits.
- The same external blocked-read staging now captures and restores label state for the whole staged subtree, not just the root directory, so nested host files/directories do not keep helper-applied mandatory labels after the restricted child exits.
- Selected external blocked-read roots now also promote one safe parent directory when that parent does not contain any allowed workspace/system/helper roots, so a blocked host project root can hide sibling host trees under the same parent without over-blocking the projected workspace.
- The same external blocked-read staging now promotes the highest safe ancestor chain instead of stopping after one level, so a nested blocked host tree can hide sibling trees under a broader safe container when that container still stays outside all allowed workspace/system/helper roots.
- The same external blocked-read staging now also expands across safe siblings of higher unsafe ancestors, so a blocked host tree can deny broader sibling host trees under outer ancestor containers whenever those sibling trees still stay outside all allowed workspace/system/helper roots.
- The Windows strict helper uses `workspace-only` boundary scope, so production strict mode focuses on hiding denied content inside the active workspace.
- The helper-binary suite now also proves that `workspace-only` scope leaves external host trees visible, so an external tool can still read a sibling `Downloads\\secret.txt`.
- The same `workspace-only` runtime coverage now also proves that hidden workspace-internal metadata roots such as `.ai_ide_runtime` still stay invisible while that outside host sibling remains readable, which pins down the clarified requirement directly at the helper runtime boundary.
- The same `workspace-only` runtime proof now also covers a root-level external tool under `Tools\\Python311`, so the default host-visible behavior is pinned down for both `Scripts`-based and root-level external layouts instead of only one install shape.
- The helper-binary suite now also proves the other half of that root-level `workspace-only` contract: a root-level external tool still keeps outside host siblings visible while hidden workspace-internal metadata roots remain invisible, so both `Scripts`-based and root-level layouts follow the same clarified default behavior.
- The same root-level `workspace-only` runtime coverage now also proves `ProgramData` stays visible by default, so the clarified host-visible contract is pinned down for another common outside-host tree instead of only `Downloads`-style siblings.
- The same root-level `workspace-only` runtime coverage now also proves `Program Files` stays visible by default, so the default host-visible contract is pinned down for common install roots as well as user-profile siblings.
- The launch-layout regression suite now also locks the broader `workspace-only` default on user-profile surface, so `Downloads`, profile-root leaf files such as `.gitconfig`, and direct sibling subdirectories stay visible unless they are explicitly inside the active workspace policy.
- Runner status payloads now expose `strict_boundary_scope`, so the CLI/UI can tell whether the current strict path is using the default `workspace-only` contract or a future stricter mode without re-deriving it from backend env assembly.
- Runtime `status` text now also includes `strict_boundary_scope`, so the plain-text CLI surface stays aligned with the JSON/status payloads instead of hiding the active strict boundary mode.
- Restricted launches now fail closed if the helper cannot stage the read boundary after low-integrity write-boundary setup succeeds, so the helper no longer silently falls back to an unrestricted process when read-side enforcement is unavailable on a host that otherwise supports restricted launches.
- The helper runtime now also fails closed before launch when restricted-token or write-boundary support is missing for an admitted restricted shape, so one-shot argv, structured batch, and stdio-proxy launches no longer silently downgrade to unrestricted parent-token execution.
- `runner validate` / `runner info` now surface the helper's `restricted_token`, `write_boundary`, and `read_boundary` capability results directly, so boundary readiness no longer hides behind a single passed/failed summary.
- The Python strict-runner bridge now passes the original host project root plus host-side IDE metadata/runtime roots into `AI_IDE_BLOCKED_READ_ROOTS`, so the helper can stage the full source tree as a blocked-read root while still carving out the projected workspace as the allowed view.
- The Python strict-runner bridge now also eagerly materializes helper-owned runtime `controls` and `processes` directories before strict helper launch, so blocked-read staging does not fail closed on missing internal runtime roots during validation or one-shot execution.
- Shell admission now rejects env-expanded invoked program paths when the expansion introduces shell-sensitive syntax such as unquoted whitespace, so helper approval does not drift from how `cmd.exe` would actually split the command after `%VAR%` expansion.
- The remaining shell-command path now also rejects non-`echo` arguments whose `%VAR%` / `!VAR!` expansion would introduce shell control operators after helper approval, so env-expanded shell arguments cannot turn an admitted command into later `cmd.exe` control flow.
- The remaining shell-command path no longer interprets `!VAR!` delayed-expansion syntax at helper level, so strict shell parsing only honors the explicit `%VAR%` expansion model it can normalize safely and `!VAR!` no longer drives helper-local echo, direct-argv lowering, or structured batch selection.
- The remaining shell-command path now also rejects more stateful or interactive `cmd.exe` builtins such as `for`, `if`, `goto`, `shift`, `date`, `time`, `cls`, `color`, `pause`, `prompt`, and `title`, so strict helper mode does not leave UI/session-local shell behavior on the weak shell surface.
- The same nested-shell guard now also treats legacy `command` / `command.com` launchers as fail-closed shell wrappers, so strict helper mode does not leave another Windows shell hop that can reinterpret command text outside the direct-argv path.
- The remaining helper-local shell parsing now also rejects unquoted caret escape syntax (`^`), so `cmd.exe`-only escape semantics no longer influence helper-local echo, direct-argv lowering, or structured-batch selection.
- The same helper-local shell parsing now also rejects unterminated quoted command shapes before helper-local echo, direct-argv lowering, or structured-batch selection, so malformed shell text cannot drift from what `cmd.exe` itself would parse.
- The same shell admission now also rejects the `@` command-echo suppression prefix, so strict helper mode does not leave one more `cmd.exe`-only control form on the remaining shell surface while response-file arguments like `python @args.rsp` stay allowed.
- The remaining shell-command path now rejects non-batch script launches such as `helper.py` instead of relying on Windows file associations through `cmd.exe`, so interpreter-backed script execution has to stay explicit (`python helper.py`) and the weak shell wrapper surface is narrower.
- The helper no longer falls back to raw `cmd.exe /C <text>` execution for shell commands that it cannot lower into helper-local `echo`, direct argv, or structured batch launch. Unsupported shell-only shapes now fail closed instead of keeping a generic shell wrapper alive.
- The remaining shell-wrapper path now also rejects filesystem-touching builtins such as `dir`, `type`, `copy`, `del`, and `mkdir`, so shell mode keeps only non-filesystem builtins like `echo` and pushes more file access toward the tighter direct-argv path or an early rejection.
- The same filesystem-builtin denylist now also rejects `vol` before any `PATH` lookup, so strict helper mode does not let drive or volume inspection fall back through a weaker `cmd.exe` builtin shape even if `PATH` contains a matching `vol.exe`.
- The same weak shell path now also rejects `mklink`, so strict helper mode does not leave junction/symlink creation on the last remaining `cmd.exe` builtin surface.
- The remaining shell-wrapper path now also rejects stateful shell builtins such as `cd`, `pushd`, `popd`, `set`, `setlocal`, `endlocal`, and `path`, so strict helper launches do not rely on `cmd.exe` session state or cwd mutation for any admitted command shape.
- The same weak shell path now also rejects `assoc` and `ftype`, so strict helper mode does not leave Windows file-association mutation on the remaining `cmd.exe` builtin surface.
- The same stateful shell-builtin denylist now also rejects `dpath`, so strict helper mode does not leave `cmd.exe` data-search-path mutation on the remaining shell surface.
- The Rust Windows helper now also rejects shell control operators such as `&&`, `|`, `>`, `<`, `;`, and parentheses in raw shell-command mode, so the still-weaker shell wrapper path is limited to a single simple command shape rather than chained or redirected shell programs.
- The Rust Windows helper now runs shell-command launches under a helper-owned two-process job policy (`cmd.exe` plus one intended child), so the remaining shell-wrapper path cannot fan out extra grandchildren even before real filesystem isolation exists.
- The same helper execution guard now also rejects UNC and Windows device-path forms such as `\\\\server\\share\\tool.cmd` and `\\\\?\\C:\\...` in direct argv and shell-command mode, so helper-backed strict runs do not rely on normal drive-path parsing before real filesystem isolation exists.
- `agent poll` now goes through a dedicated run-inspection seam, so active helper-backed strict runs can surface `process_source=helper-control` and structured process state without mutating persisted run history or overloading the stop path.
- `agent history` now reuses the same run-inspection seam for active runs, so helper-backed strict processes report live `process_source/process_state/process_pid/process_code` in list output instead of falling back to stale record-only status.
- `term attach` and `term list` now reuse a dedicated terminal-inspection seam over active run inspection, so terminals bound to helper-backed strict runs surface live `run_status/process_source/process_state/process_pid/process_code` instead of only remembering the bound run id.
- `term list json` and `term show <id> json` now expose machine-readable terminal inspection snapshots, including live bound-run helper status, so future IDE panes can consume terminal/run state without parsing CLI text.
- `term watch ... json`, `term watches <id> json`, `term inbox <id> json`, and `term attach <id> <run_id> json` now expose machine-readable watch/inbox/bridge snapshots, so future terminal panes can track event subscriptions and buffered messages without scraping CLI text.
- Terminal inbox snapshots and terminal inspection JSON now include structured inbox-entry payloads alongside legacy rendered message strings, so future terminal panes can consume event/message metadata without reparsing stored text.
- `agent show/list/registry/plan/transport`, `agent poll/history`, and `agent watch/watches/inbox` now all have JSON variants, so future IDE panes can query agent session, transport, live run inspection, and watch/event state without scraping CLI output.
- Agent-run shutdown now goes through a dedicated process-stop seam that closes stdin first, waits briefly for a graceful exit, and only then escalates to terminate/kill, which gives helper-backed strict runs a cleaner stop path before a native process-control channel exists.
- `workspace panel [dir] [json]` and the `/api/workspace-panel` HTTP surface now share the same backend snapshot contract for visible tree entries, deny-rule suggestions, policy/session state, and workspace-index freshness, which gives the filesystem panel one stable control-plane shape.
- `python -m ai_ide.ui_server` now serves a minimal static filesystem panel from `ai_ide/ui_web/`, so the same backend contract is visible in a browser without adding a separate frontend toolchain yet.
- Successful `review apply`, `unsafe write`, and `runner refresh` operations now best-effort refresh the workspace index cache so IDE-visible workspace snapshots stay closer to the current host state without requiring a manual `workspace index refresh`.
- Review stage/apply/reject/conflict actions now emit structured review events and audit entries, so writeback decisions show up in the same runtime observability surface as agent and terminal activity.
- `session show` reports the current execution session and its bound agent session.
- `agent rotate` creates a fresh AI-facing session without forcing a new execution session.
- `agent show` now resolves the current session kind through an adapter registry and reports adapter readiness.
- `agent registry` lists known adapter kinds and whether each CLI launcher is currently available on PATH.
- `agent plan [kind]` exposes the current launch plan contract for a concrete adapter, including `io_pref` so the runtime can distinguish pipe fallback from future PTY-backed launches.
- `agent transport [kind] [--mode ...]` exposes the runtime-side transport decision for a concrete adapter, including whether the adapter is actually launchable in the current pipe-only runtime.
- `agent exec` now launches the current agent session kind through the runner boundary with session metadata in the child environment.
- Agent adapters now advertise `io_pref=session-placeholder|pipe|pty_preferred|pty_required`; the current runtime resolves `pty_preferred` adapters to `io=pipe transport_status=degraded` and marks `pty_required` adapters unavailable until a PTY transport exists.
- Agent transport resolution now goes through a dedicated transport-provider seam. Today that provider is `pipe-only`, which makes the current limitation explicit and gives the future PTY implementation a clean replacement point.
- `agent show` and `agent plan` now include resolved `io`, `transport_status`, and `launchable` fields so future UI surfaces do not have to guess whether a selected adapter can actually start.
- `agent start` creates a long-running agent process session, `agent poll` reads accumulated output, and `agent stop` terminates it.
- `agent send` writes structured stdin into an active agent run and publishes a matching `agent.run.stdin` event.
- `agent watch` creates a persisted per-run consumer cursor, `agent inbox` pulls unseen structured events for that run, and `agent unwatch` removes the runtime-managed watch state.
- Agent watch definitions now persist under the runtime root, so the same runtime can restore per-run observers across restart.
- `agent gc <max_age_seconds>` removes abandoned agent watch definitions and clears their pinned consumer cursors.
- `agent inbox` now opportunistically refreshes active agent processes before reading the event stream, so stdout/completion events can surface without a separate `agent poll` loop.
- `agent history` now defaults to the current execution session when it has runs, and falls back to all persisted runs after restart; `agent history all` forces the full persisted project-local run history.
- Agent run history is now persisted under the runtime root, and restored runs that were active before restart are marked `interrupted` because the new runtime instance no longer owns their live process handles.
- Policy-driven execution session rotation now also stops active agent processes that were bound to the stale execution session.
- `events list` exposes a structured runtime event stream for agent run lifecycle, output chunks, and terminal messaging.
- `events tail` lets future UIs sync incrementally from a known event cursor instead of reloading the whole event buffer.
- Runtime events are now persisted as JSONL under the runtime root at `events/events.jsonl`, so `events tail` cursors survive app restarts when the same runtime root is reused.
- Consumer cursor checkpoints are now persisted at `events/cursors.json`, so future UI clients can resume incremental sync without storing their own event checkpoint files.
- Event logs and cursor stores now use advisory lock files, so separate app instances sharing the same runtime root can append and read monotonic `evt-` ids without manual restart or reload steps.
- `events compact <retain_last>` trims old log entries while preserving the latest retained window and any cursor-pinned anchor events that active consumers still need.
- `events gc <max_age_seconds>` removes abandoned consumer checkpoints so stale cursors stop pinning old events forever.
- `EventBus.pull_events()` now supports exact kind/source filters while still advancing cursors past unrelated events, so watch consumers do not rescan non-matching traffic forever.
- CLI event queries accept `none` or `-` placeholders, so callers can filter by source without being forced to provide a kind prefix.
- `events list`, `events tail`, and `events pull` now refresh active agent runs before reading the event bus, which gives future UI clients a lighter-weight live view even before full PTY support lands.
- CLI command handling is now split across `ai_ide/commands/` modules, which keeps `AIIdeApp` focused on wiring and top-level dispatch instead of embedding every command contract inline.
- Agent runtime transport planning and runtime-local persistence are now split into `ai_ide/agent_transport.py` and `ai_ide/agent_state_store.py`, which narrows the remaining refactor target inside `AgentRuntimeManager`.
- Live process ownership is now split into `ai_ide/agent_supervisor.py`, which separates long-running subprocess control from runtime history/watch orchestration.
- Agent watch lifecycle and cursor plumbing are now split into `ai_ide/agent_watch_manager.py`, which keeps replay, inbox pull, and stale-watch cleanup out of the higher-level runtime orchestrator.
- Run-record creation, history filtering, and restored-run reconciliation are now split into `ai_ide/agent_run_ledger.py`, which leaves the runtime layer focused on orchestration instead of record-lifecycle policy.
- Agent run event payload shaping and publication are now split into `ai_ide/agent_event_publisher.py`, which makes the future daemon/Rust event bridge a cleaner cutover point.
- Agent launch preparation is now split into `ai_ide/agent_launch_coordinator.py`, which keeps runner-mode validation, adapter/transport unavailability, argv defaults, and session env metadata out of `AgentRuntimeManager`.
- Terminal runtime-local persistence is now split into `ai_ide/terminal_state_store.py`, which keeps JSON shape, lock handling, and restart restoration out of `TerminalManager`.
- Terminal watch and agent-bridge lifecycle are now split into `ai_ide/terminal_watch_manager.py`, which keeps event subscription cursors, inbox sync, stale-watch cleanup, and terminal-to-agent bridging out of `TerminalManager`.
- Terminal command execution is now split into `ai_ide/terminal_command_executor.py`, which keeps blocked formatting, runner/host command dispatch, metrics updates, and terminal command event publication out of `TerminalManager`.
- Runner backend status/probe formatting is now split into `ai_ide/runner_status_service.py`, which keeps platform capability/probe shaping and CLI-facing status text out of `RunnerManager`.
- Runner mode dispatch and execution-session stale blocking are now split into `ai_ide/runner_dispatch_service.py`, which keeps mode validation and runner selection out of `RunnerManager`.
- Strict runner backend/preview fallback policy is now split into `ai_ide/runner_strict_service.py`, which keeps strict guard evaluation, backend invocation fallback, preview env shaping, and blocked-result handling out of `StrictRunner`.
- Projected runner projection materialization, command-guard blocking, env-overlay process launch, and blocked-launch/result handling are now split into `ai_ide/runner_projected_service.py`, which keeps filtered-workspace execution policy out of `ProjectedRunner`.
- Shared subprocess execution, process-handle artifact creation, and runner result/launch models are now split into `ai_ide/runner_process_service.py` and `ai_ide/runner_models.py`, which keeps low-level subprocess policy and runtime data shapes out of `runner.py`.
- Strict-preview env shaping and shared argv/env helper rules are now split into `ai_ide/runner_environment_service.py`, which keeps environment scrubbing, env overlay merging, and argv-to-command normalization out of `runner.py`.
- Host-mode command execution and host process artifact policy are now split into `ai_ide/runner_host_service.py`, which keeps host subprocess routing out of `HostRunner`.
- Policy state persistence is now split into `ai_ide/policy_state_store.py`, and `.ai-ide/` project metadata is treated as internal so agent listing, grep, and projection views do not expose policy metadata back to the model.
- Command boundaries now resync persisted project policy before dispatch, so changes made by another app instance rotate execution state locally while preserving the currently selected agent kind.
- Broker metrics and audit persistence are now split into `ai_ide/broker_state_store.py`, and `audit list` exposes the persisted runtime audit trail with project-relative targets for allowed vs blocked operations.
- Review proposal persistence is now split into `ai_ide/review_state_store.py`, and `review stage/show/apply/reject` exposes an explicit staged writeback path with restart-safe proposal history and base-content conflict detection.
- The default AI file-write path now stages review proposals; direct host writes require the separate `unsafe write ...` command namespace.
- Unsafe direct-write bypasses now require trusted command context, which creates a cleaner future boundary between user control-plane actions and agent-facing command surfaces.
- Runtime status shaping is now split into `ai_ide/runtime_status_service.py`, which is the first explicit machine-readable control-plane seam for future Rust/TypeScript cutover.
- Agent transport-provider capability is now split into `ai_ide/agent_transport_provider.py`, which keeps the current pipe-only transport policy out of the planner and gives PTY work a dedicated insertion point.
- A Rust workspace now lives under `rust/`, with `ai-ide-protocol` for shared typed control-plane/domain payloads and `ai-ide-core` for pure session/review/status logic. This is the start of the real cutover, but it intentionally excludes PTY transport, live run supervision, and OS sandbox backends for now.
- Fresh Windows installs may need to call Cargo through `C:\Users\jsr27\.cargo\bin\cargo.exe` until the shell PATH is refreshed.
- Review event publication is now split into `ai_ide/review_event_publisher.py`, and review-related audit entries are recorded through the broker audit trail so staged writes and writeback decisions are observable and restart-safe.
- `term watch` now allocates a persisted event consumer cursor per terminal watch, so inbox delivery is pull-based and future terminal clients can recover from the shared runtime event store instead of relying on in-memory callbacks.
- Terminal sessions and watch definitions are now persisted under the runtime root, so host terminals and watch-based inboxes survive restart; restored runner terminals are immediately marked stale because their old execution session is no longer current.
- `term watch` subscribes a terminal inbox to structured events, which is the first direct terminal-to-agent event-channel bridge.
- `term attach` binds a terminal to an active agent run, and `term input` forwards terminal input to that run over the runtime boundary.
- `term gc <max_age_seconds>` removes stale terminal watch leases and clears stale bridge bindings that still point at abandoned event consumers.
- `term inbox` now refreshes active agent runs for the terminal's execution session before consuming watch cursors, so terminals can observe new agent output without needing a separate explicit poll step.
- Runner-managed terminals are bound to an execution session and become stale after policy-driven session rotation.
- The runner also rejects stale execution session ids directly, so stale terminal blocking is enforced at both terminal and runner boundaries.
- `runner exec` defaults to projected mode, which runs inside a filtered workspace copy.
- `runner probe` reports whether the current platform has a backend we can target for future strict sandbox integration.
- `runner info` returns machine-readable backend status for future UI/IDE policy and health surfaces.
- `runner probe`/`runner info` now distinguish raw backend probe status from contract validation, so `ready=true` means the backend is both detected and structurally valid for launch.
- `runner validate` executes a minimal strict-backend fixture when the backend is ready and reports whether the sandbox actually hides denied paths, exposes isolated home/cache env, and writes only into the projected workspace copy.
- `runner validate` now uses a deterministic hidden sentinel under internal metadata and checks both relative access and direct host-path access, so the fixture no longer depends on a user project already containing a denied file.
- `runner validate` now also attempts a direct host-path write to that internal sentinel area and requires the write to be blocked, so the fixture checks read and write escape paths separately.
- `runner info` now invalidates the cached strict-backend validation summary when the visible workspace tree changes after the last validation run, so an old "passed" result is not treated as current after external file edits.
- Workspace visibility and projection now fail closed on symlink path components, so AI-facing tree/search/projection surfaces do not follow symlink aliases into alternate paths or recursion loops.
- Workspace visibility and projection also fail closed on multiply linked files, so hardlink aliases cannot expose denied content under a different visible path until there is a stronger inode-aware policy model.
- Rust-backed `workspace` and `review` paths now share the same alias guards through `ai-ide-persistence::path_guard`, so Rust host queries and staged writes also fail closed on symlink and hardlink aliases instead of reintroducing a cutover-only visibility gap.
- `runner info` now also carries the latest strict-backend validation summary (`not_run`, `passed`, `failed`, `skipped`, or `stale`) so future IDE health surfaces can show whether the last fixture result still applies to the current execution session.
- `runner exec` output now includes the backend used, such as `projected`, `strict-preview`, or a future real backend like `wsl`.
- When a real strict backend is used, the reported `cwd` now reflects the backend-visible workspace path such as `/workspace` or a WSL mount path.
- The Linux `bwrap` backend now binds the projected workspace as the writable `/workspace`, mounts required system directories read-only, and isolates `HOME`/`TMPDIR`/`XDG_CACHE_HOME` under `/tmp` instead of inheriting host user directories.
- The Windows WSL strict backend now bootstraps the same isolated temp home/cache contract before executing inside the projected workspace mount.
- The runtime now treats that WSL path as detected-but-not-yet-strict: default WSL still exposes host filesystem mounts outside the projected workspace, so `runner info` marks it as an invalid contract and `strict` falls back to guarded preview until a stronger Windows backend exists.
- Strict backend invocations are now validated before launch; invalid WSL/`bwrap` contracts fail closed and fall back to guarded preview instead of trusting a malformed sandbox command line.
- If a selected real strict backend fails to launch, `strict` falls back to guarded preview and reports the backend launch failure in stderr.
- The projected workspace is created in an external runtime/cache directory instead of inside the project root.
- Projected mode blocks obvious direct host-project path references and reduces simple relative-path escapes.
- `runner mode strict` first tries a platform backend when `runner probe` says it is ready, otherwise it falls back to guarded preview mode.
- Guarded strict preview now blocks obvious interpreter launches such as `python` and `node` in addition to nested shells and network-capable commands.
- Projected and strict preview now block both direct and encoded host-project path references, including simple numeric/`chr(...)` reconstruction.
- Neither projected nor strict preview is a full OS-level sandbox yet; stronger process isolation is still missing.
- `term run` now defaults to runner-managed terminals; explicit host shell access requires `term new --host` and stays marked unsafe.

## Remaining work snapshot

- PTY-capable transport is still missing. The runtime can now express the need for it, but it still resolves real CLI launches through pipe-based stdin/stdout handling.
- Live run supervision does not survive restart. Restored `running` records become `interrupted` because process ownership is still in-process, not daemonized.
- Strict sandbox backends are still partial. WSL/bwrap probing exists, but end-to-end platform validation and macOS backend implementation are still incomplete.
- The Rust cutover has started, but only for stable control-plane/core domains. PTY transport, run supervision, and strict sandbox behavior still live in the Python reference runtime.
- Direct write remains only as the explicit `unsafe write ...` override. Full mandatory review enforcement is still future work.
- The desktop control plane and GUI are still ahead. This repository is still a runtime/backend PoC, not a beta desktop IDE.
