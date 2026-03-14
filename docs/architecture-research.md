# AI IDE Architecture and Research

## 1. Recommendation Summary

The recommended architecture is:

- desktop shell: Tauri v2
- core runtime: Rust
- UI: React + TypeScript
- terminal UI: xterm.js
- local persistence: SQLite in WAL mode
- agent integration: adapter processes plus a normalized control protocol
- execution model: host control plane plus isolated runner data plane

The important decision is not "Rust vs TypeScript". It is the boundary:

- the UI is not trusted to enforce security
- the core runtime is the trust anchor
- external AI CLIs run in isolated runners
- policy is enforced before the agent sees the workspace

## 2. Why This Shape Fits the Product

Your product is closer to a local orchestrator than a traditional editor. That changes the design target:

- multiple long-lived subprocesses
- policy-sensitive filesystem views
- PTY management
- audit storage
- crash isolation
- deterministic session rotation

That pushes the design toward a small trusted core and clear module boundaries.

## 3. Option Analysis

### 3.1 Desktop Shell

#### Option A: Tauri + Rust

Pros:

- small trusted backend surface
- native Rust core process is a better fit for policy, PTY, storage, and sandbox orchestration
- Tauri v2 has a capabilities model and scoped shell permissions, which matches the least-privilege mindset
- sidecar support is explicit, which helps package helper binaries

Cons:

- desktop ecosystem is smaller than Electron
- native packaging and signing work is stricter

Verdict:

- best fit for this product

#### Option B: Electron + Node

Pros:

- mature ecosystem
- large pool of desktop examples
- good terminal ecosystem

Cons:

- larger trusted computing base
- more security hardening work is required in the desktop shell
- easy to accidentally leak host APIs into the renderer or preload boundary

Verdict:

- viable, but not the first choice when strict local security is central

#### Option C: VS Code extension only

Pros:

- fast time to market
- familiar editor surface

Cons:

- weak fit for strong OS-level isolation
- product identity remains "AI inside editor", not "AI runtime as IDE"
- too much security depends on the host editor model

Verdict:

- useful as a compatibility layer later, not as the core product

## 4. Core Architectural Decision

Use a control-plane/data-plane split.

### 4.1 Control Plane

Runs on the host and owns:

- project config
- policy engine
- session manager
- runner supervisor
- terminal gateway
- audit store
- UI event distribution

This is the trusted core.

Inside the session manager, treat at least these scopes as separate:

- execution session: the runtime boundary tied to policy version, projection, runner, and environment
- agent session: the AI conversation or context boundary
- terminal session: the PTY and user interaction boundary

### 4.2 Data Plane

Runs the actual agent CLIs and tools inside an isolated runner.

The runner gets:

- a policy snapshot
- an execution profile
- a projected workspace view
- a bounded environment

This is the untrusted execution zone.

## 5. Recommended Module Layout

Suggested top-level layout:

```text
apps/
  desktop-shell/
ui/
  web/
crates/
  policy-core/
  session-core/
  runner-supervisor/
  runner-protocol/
  workspace-projection/
  terminal-gateway/
  audit-store/
  metrics-core/
  agent-adapters/
  review-writeback/
  project-config/
  security-tests/
```

Responsibilities:

- `policy-core`: path rules, operation rules, policy versions, reason codes
- `session-core`: session IDs, rotation, stale-session invalidation
- `runner-supervisor`: create/stop runners, health checks, resource limits
- `runner-protocol`: normalized API between core and runners
- `workspace-projection`: materialize the visible workspace
- `terminal-gateway`: PTY lifecycle, resize, backpressure, message bus
- `audit-store`: events, queries, retention, export
- `metrics-core`: counters, spans, latency histograms
- `agent-adapters`: wrappers for Claude Code, Codex CLI, Gemini CLI, OpenCode, MCP
- `review-writeback`: diff, approval, apply, rollback hooks
- `project-config`: `.ai-ide` schema and validation
- `security-tests`: escape-attempt fixtures and regression suite

## 6. Runner Strategy

This is the hardest part of the system. If the requirement is "block access even if the CLI tries another command", a broker alone is not enough. The execution environment itself must be constrained.

### 6.1 Recommended Execution Modes

#### Mode 1: Broker Mode

- fast for local development
- not a real security boundary
- useful only for prototyping

#### Mode 2: Projected Workspace Mode

- the runner sees a projected workspace containing only allowed files
- denied files are absent, not just blocked by convention
- direct host writes are disabled or tightly scoped

This should be the default mode.

#### Mode 3: Strict Sandbox Mode

- projected workspace plus OS or VM isolation
- network policy
- process allowlist
- resource limits

This is the production security mode.

### 6.2 Cross-Platform Recommendation

Use a consistent "managed Linux runner" strategy wherever possible, because it reduces behavioral drift across platforms.

- Windows host: prefer WSL2 or a managed dev-container style runner; use AppContainer or restricted tokens only for host-side helpers where needed
- macOS host: prefer a managed Linux VM for strict mode; use native app sandboxing for the desktop shell and helper packaging, not as the only boundary for arbitrary agent CLIs
- Linux host: prefer rootless containers and add Landlock where available for extra defense in depth

Reasoning:

- a single runner model is easier to test
- CLI tools already expect a Unix-like environment frequently
- host-native sandboxing differs too much across Windows, macOS, and Linux
- strict local guarantees are easier when the agent never sees the host workspace directly

## 7. Workspace Projection

This module is central.

### 7.1 Design

Instead of giving the runner the host repository directly, create a projection:

- include allowed files and directories
- exclude denied paths completely
- preserve relative layout for allowed content
- support read-only projection by default
- stage writes separately
- prefer placing the projection outside the project root in a separate runtime/cache area

Possible implementation choices:

- copy-on-open cache for small projects
- synced mirror for medium and large projects
- overlay or bind mount style projection where the platform supports it safely

### 7.2 Why Projection Matters

It solves several classes of problems at once:

- `ls`, `find`, `grep`, `git`, `python`, `node`, and shell builtins only see what exists in the runner
- stale indexes do not leak denied files if indexes live inside the runner
- logs and diagnostics can refer only to visible paths
- keeping the projection outside the project root reduces simple `..` relative-path escapes back into the host workspace

## 8. Policy Engine

### 8.1 Requirements

- deterministic precedence rules
- explicit operation scopes: read, write, search, execute, network, secret
- policy versioning
- reason codes for denials
- compiled representation for fast evaluation

### 8.2 Suggested Rule Order

1. explicit deny
2. explicit allow
3. inherited project default
4. runtime profile default deny

### 8.3 Policy Representation

Use a project file plus local overrides:

```text
.ai-ide/
  policy.json
  agents.json
  benchmarks.json
```

Local user overrides should not silently weaken project policy.

## 9. Agent Adapter Model

External tools differ, so the core should not talk to them directly.

Each adapter should normalize:

- startup command
- environment contract
- working directory
- prompt injection guardrails where applicable
- token and cost extraction when available
- streaming output parsing
- structured events

Adapter responsibilities stop at process integration. They must not own security policy.

## 10. IPC and Protocols

### 10.1 Recommended Choice

- UI to core: Tauri commands/events
- core to runners/adapters: JSON-RPC over stdio or local sockets
- MCP compatibility: support stdio first

### 10.2 Why

The Model Context Protocol uses JSON-RPC and standard transports including stdio, and explicitly recommends stdio support where possible. For local child-process boundaries this is simpler than adopting gRPC everywhere.

Use gRPC only if you later add remote runners or distributed execution.

## 11. Terminal Architecture

Recommended split:

- frontend rendering: xterm.js
- backend PTY management: Rust
- Windows PTY: ConPTY
- Unix PTY: native PTY support

Key requirements:

- bounded output buffers and backpressure
- per-terminal ownership and permissions
- structured control channel in addition to raw PTY streams

The structured channel is what enables terminal-to-terminal or terminal-to-agent coordination without parsing shell text.

## 12. Persistence and Audit

SQLite is the practical default for local audit and metrics.

Recommended data classes:

- sessions
- policy_versions
- audit_events
- terminal_events
- write_intents
- benchmarks

Why SQLite:

- embedded
- reliable local storage
- simple operational model
- WAL improves concurrency for reads and writes on one host

Store large raw logs as files only if necessary, and index them from SQLite.

## 13. Security Model

### 13.1 Trust Boundaries

- renderer/UI: low trust
- desktop shell and Rust core: trusted
- adapters and agent CLIs: untrusted
- projected workspace: controlled data surface
- host workspace: protected asset

### 13.2 Required Controls

- default-deny process launch policy
- no direct host workspace mount in strict mode
- network deny by default, with explicit allow profiles
- secrets injection only through approved policy channels
- stale-session invalidation on policy version mismatch
- symlink and path canonicalization checks
- writeback approval path for host changes

### 13.3 Security Test Matrix

The product should ship with automated escape attempts, including:

- `ls`, `find`, `fd`, `grep`, `rg`, `cat`, `type`
- `python`, `node`, `ruby`, shell builtins
- `git show`, `git ls-files`, `git grep`
- symlink traversal
- archive extraction into allowed paths
- reading from caches, indexes, or temp files
- child-process spawn attempts
- localhost and external network egress attempts

## 14. Maintainability Strategy

### 14.1 Keep the Trusted Core Small

Do not let UI code or agent-specific code creep into policy evaluation or runner supervision.

### 14.2 Favor Pure Modules

`policy-core`, `session-core`, and most of `project-config` should be pure libraries with minimal side effects. That keeps them easy to fuzz, unit test, and reason about.

### 14.3 Isolate OS Adapters

Put Windows, macOS, and Linux sandbox details behind clear traits or interfaces. The control plane should ask for capabilities, not call platform APIs directly everywhere.

## 15. Test Strategy

### 15.1 Unit Tests

- policy matching
- precedence and reason codes
- session rotation rules
- config schema parsing
- audit serialization

### 15.2 Integration Tests

- projected workspace creation
- runner boot and teardown
- adapter lifecycle
- PTY lifecycle
- writeback review flow

### 15.3 Security Regression Tests

- all known escape vectors as executable fixtures
- per-platform deny assertions
- stale-session tests after policy edits

### 15.4 Chaos and Reliability Tests

- kill agent during write
- crash terminal host
- disk full during audit append
- partial projection sync failure

## 16. Benchmark Plan

Benchmarking should be part of the design, not an afterthought.

### 16.1 Core Metrics

- cold start time
- warm start time
- session rotation latency after policy edit
- runner launch latency
- terminal first-output latency
- search throughput on projected workspace
- memory per idle terminal
- memory per active agent session
- audit ingest throughput
- writeback apply latency

### 16.2 Repository Classes

- small: 5k files
- medium: 50k to 100k files
- large: 250k to 500k files
- monorepo stress: 1M files or equivalent metadata load

### 16.3 Security Benchmarks

- time to apply new deny rule and invalidate old session
- false negative rate for blocked-path detection
- stale index leakage checks
- network egress denial verification

### 16.4 UX Benchmarks

- time from policy toggle to visible session refresh
- terminal scroll smoothness under heavy output
- diff review latency for large generated patches

### 16.5 Suggested Benchmark Harness

- fixture repos checked into a separate benchmark corpus
- repeatable runner profiles
- fixed command sets per agent
- percentile reporting: p50, p95, p99
- CI gate for regressions on startup, runner launch, and policy-apply latency

### 16.6 Baseline Comparisons

Benchmark each feature against a relevant baseline, not a single global number.

- desktop shell footprint: Tauri shell build versus Electron shell build
- terminal latency: native system terminal versus xterm.js plus PTY gateway
- workspace search: direct host `rg` versus projected workspace `rg`
- runner startup: host-direct CLI launch versus projected runner versus strict sandbox runner
- remote-style isolation: dev-container style workflow versus managed runner workflow

The most useful comparison is usually not "our app versus another branded IDE". It is "how much overhead did isolation and observability add compared to the unsafe direct path".

### 16.7 Initial Performance Budgets

Treat these as starting targets for MVP and revise them with real measurements:

- warm shell start: under 1.5s on a medium developer laptop
- warm runner launch: under 2s for projected mode
- policy toggle to new session ready: under 500ms without full reprojection, under 2s with reprojection on medium repos
- terminal first output after launch: under 150ms
- projected workspace search overhead: less than 15% slower than direct host `rg` on medium repos
- idle memory: under 300MB for the shell with no active agent runs
- per active agent session overhead, excluding model process: under 150MB

## 17. Suggested Implementation Phases

### Phase 1

- Tauri shell
- Rust policy engine
- session manager
- SQLite audit store
- xterm.js plus PTY gateway
- broker mode prototype

### Phase 2

- projected workspace
- staged writeback
- adapter normalization for at least two AI CLIs
- benchmark harness

### Phase 3

- strict sandbox mode
- network policy
- secrets policy
- full security regression suite

### Phase 4

- remote runners
- team policy sharing
- richer approval workflows

## 18. Current Prototype Gap

The current Python prototype in this repository now demonstrates policy-gated tool calls, session rotation, metrics, simple terminal handling, a projected workspace runner mode, a guarded strict-preview mode, and explicit platform-adapter boundaries. It is still not sufficient for the full product because host shell execution can bypass policy, and strict preview is not yet a full OS-level sandbox: it reduces simple relative-path escapes, blocks obvious direct host-project path references and some network-capable commands, but computed absolute host-path access and deeper process/network controls are still open gaps. That is expected at this stage and is the reason the production design still centers on isolated runners with stronger process and network controls.

The current prototype also exposes strict-backend probe status through platform adapters and writes projection manifests while cleaning stale projection sessions. Those are useful control-plane capabilities, but they do not replace full stale runner/index invalidation or a real OS-level sandbox.

Strict mode now also follows a backend-selection path: if the platform adapter reports a usable backend, the runner can execute through that backend; otherwise it falls back to guarded preview mode. This preserves a stable integration point for future WSL, bubblewrap, or VM-backed strict runners.

## 19. Sources

- Tauri architecture: https://v2.tauri.app/concept/architecture/
- Tauri capabilities: https://v2.tauri.app/ko/security/capabilities/
- Tauri shell plugin permissions: https://v2.tauri.app/plugin/shell/
- Tauri sidecars: https://v2.tauri.app/develop/sidecar/
- Electron process model: https://www.electronjs.org/docs/latest/tutorial/process-model
- Electron security checklist: https://www.electronjs.org/docs/latest/tutorial/security
- VS Code extension host: https://code.visualstudio.com/api/advanced-topics/extension-host
- VS Code dev containers: https://code.visualstudio.com/docs/devcontainers/create-dev-container
- Dev Container specification overview: https://containers.dev/overview
- Dev Container CLI: https://code.visualstudio.com/docs/devcontainers/devcontainer-cli
- xterm.js security: https://xtermjs.org/docs/guides/security/
- xterm.js flow control: https://xtermjs.org/docs/guides/flowcontrol/
- node-pty: https://github.com/microsoft/node-pty
- Windows ConPTY: https://learn.microsoft.com/windows/console/createpseudoconsole
- Windows restricted tokens: https://learn.microsoft.com/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken
- Windows AppContainer isolation: https://learn.microsoft.com/windows/win32/secauthz/appcontainer-isolation
- macOS App Sandbox: https://developer.apple.com/documentation/security/app-sandbox
- macOS sandbox configuration: https://developer.apple.com/documentation/xcode/configuring-the-macos-app-sandbox/
- Linux Landlock: https://docs.kernel.org/userspace-api/landlock.html
- Docker bind mounts: https://docs.docker.com/engine/storage/bind-mounts/
- Docker tmpfs mounts: https://docs.docker.com/engine/storage/tmpfs/
- SQLite WAL: https://sqlite.org/wal.html
- SQLite limits: https://sqlite.org/limits.html
- JSON-RPC 2.0: https://www.jsonrpc.org/specification
- Model Context Protocol transports: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
- Anthropic MCP overview: https://docs.anthropic.com/en/docs/mcp
