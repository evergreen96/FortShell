# AI IDE Requirements

## 1. Product Definition

### 1.1 Problem

Existing IDEs treat AI as an assistant attached to the editor. This product is different: it is a **terminal-centric IDE** where multiple AI CLI tools run simultaneously, and the user controls what each terminal can access at the filesystem level.

The core requirement is not "good autocomplete" or "AI code editor". It is:

- **Terminal is the main surface.** Users run Claude Code, Codex CLI, Gemini CLI, Cursor CLI, and similar tools in managed terminals.
- **File access control.** Users select files/folders to protect from the file tree. Protected files are **visible but not readable** in the terminal — they exist, but any attempt to read or modify them is denied with "Permission Denied".
- **Live workspace.** Changes made by CLI tools in the terminal are immediately reflected in the file tree and editor. No copy, no sync, no approval step required.
- **Editor is secondary.** A code editor exists for viewing and editing files, but it is not the main workflow surface.

### 1.2 Product Thesis

The main screen is:

```
┌─────────────────────────────────────────────┐
│  File Tree (full view)  │  Terminal (protected view)   │
│  - see all files        │  - protected files visible   │
│  - toggles to protect   │  - but read/write denied     │
│                         │  - multiple CLI tabs          │
├─────────────────────────┴──────────────────────────────┤
│  Code Editor (secondary, read/edit allowed files)       │
└─────────────────────────────────────────────────────────┘
```

- File tree shows ALL files, with controls to mark files/folders as protected
- Terminals run inside a **filtered filesystem view** where protected files are visible but access-denied
- Editor opens files from the original project for viewing/editing

### 1.3 Architecture: Filtered Filesystem

The filtered view is implemented using a **userspace filesystem driver** that mirrors the project directory while protecting denied paths:

```
Original project (C:/projects/myapp/):     Filtered view (Z:/ or mount point):
├── notes/todo.txt                         ├── notes/todo.txt    ← read/write OK
├── secrets/token.txt                      ├── secrets/token.txt ← visible, Permission Denied on read
├── .env                                   ├── .env              ← visible, Permission Denied on read
└── src/main.py                            └── src/main.py       ← read/write OK
```

- **No file copies.** The filtered view reads/writes through to original files.
- **No sync needed.** Changes in the terminal are immediately visible in the file tree and vice versa.
- **Protected files are visible but access-denied.** `ls` shows them, but `cat`, `read`, `write`, `chmod` all return Permission Denied.
- Protection is enforced at the OS filesystem level — all programs see the same behavior.

The filtered filesystem is provided by a platform-specific backend that implements a common interface (`FilteredFSBackend`). The product code depends on the interface, not on a specific driver.

Initial backend candidates:

| Platform | Candidate | Status |
|----------|-----------|--------|
| Windows | Dokan (LGPL) | PoC verified |
| Linux | FUSE / libfuse | Untested (expected compatible) |
| macOS | macFUSE or alternatives | Research needed |

### 1.4 Users

- Solo developers using AI CLIs heavily
- Small teams that need local privacy boundaries inside a repository
- Organizations that need auditable AI-assisted development

## 2. Product Principles

- **Terminal-first.** The terminal is the primary workflow surface, not the editor.
- **Access control, not invisibility.** Protected files are visible (they exist) but cannot be read or modified. This signals "user restricted this" rather than pretending the file doesn't exist.
- **Filesystem-level enforcement.** Protection is enforced by the filesystem driver — not by tool cooperation. `chmod` and other bypass attempts are also denied.
- **No copies, no sync.** The filtered view is a live proxy to the original project.
- **Editor is a companion.** The editor exists for convenience, not as the main surface.
- **Policy is simple.** Select files/folders in the tree → they become protected in the terminal view.
- **Review is optional.** Staged writeback/approval is available but not the default workflow.
- **Observability is a first-class surface.** Audit logs, metrics, and session tracking are always available.

## 3. Functional Requirements

### 3.1 File Protection Policy

- Users mark files/folders as protected via the file tree UI (toggles).
- Protected files must be **visible but access-denied** at the filesystem level to all processes running in managed terminals.
- Specifically for protected paths:
  - `ls`, `find`, `readdir` → file/folder **IS listed** (visible, exists)
  - `stat`, `getattr` → returns file metadata with **zero permissions** (----------)
  - `cd` into protected directory → **allowed** (directory traversal OK, but contents are protected)
  - `cat`, `read`, `open` → **Permission Denied** (EACCES)
  - `write`, `create` inside protected dir → **Permission Denied**
  - `chmod`, `chown` → **Permission Denied** (cannot change protection)
  - `rm`, `unlink`, `rmdir` → **Permission Denied** (cannot delete)
  - `cp`, `mv`, `rename` → **Permission Denied** if source or target is protected
  - `ln`, `symlink`, `hardlink` to protected file → **Permission Denied**
- Protected file access attempts (EACCES) are recorded in the audit log.
- Policy changes (protect/unprotect) are reflected **immediately** at the filesystem level. No terminal restart, no session rotation, no CLI notification. The next filesystem access from any terminal simply sees the new state.
- Policy is stored per-project (e.g., `.ai-ide/policy.json`).

### 3.2 Managed Multi-Terminal

- The IDE must support many terminals at once with independent state.
- Each terminal runs with its working directory set to the filtered filesystem view.
- Terminals must support PTY semantics: resize, ANSI colors, interactive prompts.
- Terminal types:
  - **Managed terminal:** CWD is the filtered view. AI CLI tools run here. Protected files are access-denied.
  - **Unrestricted terminal (optional):** CWD is the original project. For user's own use, marked as "unfiltered".

### 3.3 File Tree

- Shows ALL files in the original project directory.
- Provides controls (toggles) to mark files/folders as protected.
- Reflects filesystem changes in real-time (file watcher).
- Protected files are visually distinguished (lock icon, dimmed, badge) but visible in the tree.
- Clicking a protected file in the file tree opens it in the editor from the **original** project (not from the filtered view). The editor reads the original — protection only applies to managed terminals.

### 3.4 Code Editor

- Opens files from the original project directory.
- Supports syntax highlighting, basic editing.
- Secondary surface — not the main workflow.
- Uses Monaco Editor or similar embeddable component.

### 3.5 Session and Audit

- Policy changes are logged with timestamps.
- Terminal activity (commands run, files accessed) is observable.
- Metrics: read/write/search/denial counts per session.
- Audit data survives application restarts.

### 3.6 Staged Review (Optional)

- Available as an opt-in feature, not the default workflow.
- When enabled: AI writes go to a staging area, user reviews diffs, applies or rejects.
- When disabled (default): AI writes go directly to the original project through the filtered view.

## 4. Non-Functional Requirements

### 4.1 Security

- File protection must be enforced at the filesystem level, not by tool cooperation.
- The filtered filesystem view must prevent path traversal (`../secrets/`) and symlink escape.
- `chmod` and permission-changing operations on protected files must be denied — the protection is controlled only by the IDE policy, not by filesystem permissions.
- Symlink and hardlink creation targeting protected files must be denied — prevents protection bypass through aliases.
- The product boundary is the active workspace. Paths outside the workspace root are accessible normally (e.g., global tool configs like `~/.claude/`).

### 4.2 Maintainability

- Filesystem filtering logic must be isolated behind a common interface (`FilteredFSBackend`).
- Platform-specific driver code must be pluggable — adding a new backend must not require changing product code.
- Terminal, editor, file tree, and policy engine are separate modules with clean interfaces.

### 4.3 Performance

- The filtered filesystem must add minimal overhead to file operations.
- The file tree must handle large repositories (25K+ files) without freezing.
- Terminal output must stream without lag.

### 4.4 Reliability

- A crashing terminal or CLI tool must not crash the IDE.
- The filtered filesystem must handle edge cases: file locks, concurrent writes, rapid policy changes.
- Audit data must survive application restarts.

## 5. Technology Stack

| Component | Technology |
|-----------|-----------|
| Desktop shell | Tauri 2.0 |
| Frontend | React + TypeScript |
| Terminal UI | xterm.js |
| Editor | Monaco Editor |
| File tree | Custom React component + virtual scrolling |
| Backend | Python (policy, filesystem, audit, session) |
| Filtered filesystem | FilteredFSBackend interface; Windows implementation: Dokan |
| IPC | Tauri sidecar (JSON-line protocol) |

## 6. Platform Strategy

### 6.1 Overview

The filtered filesystem is the core product differentiator. Product code depends on a common `FilteredFSBackend` interface. Platform-specific backends implement this interface using the appropriate driver.

```
앱 코드 (공통):
  PolicyEngine       → protect/allow 규칙 관리
  FilteredFSBackend  → mount / unmount / update_policy 인터페이스
            │
    ┌───────┼───────┐
    ▼       ▼       ▼
  Dokan   (FUSE)   (macOS)
 backend  backend   backend
 (검증됨)  (후보)   (연구 중)
```

### 6.2 Windows

| 항목 | 내용 |
|------|------|
| 드라이버 | Dokan 2.x |
| 라이선스 | LGPL (수정 없이 사용 시 상용 무료) |
| 설치 | 앱 installer에서 Dokan MSI 자동 설치 (1회, 관리자 UAC) |
| 마운트 방식 | 드라이브 문자 (예: Z:) |
| PTY | winpty / ConPTY |
| 테스트 상태 | PoC 통과 |
| 배포 | Dokan 드라이버 + 앱 exe를 installer에 포함 |

### 6.3 Linux (후보 — 후속 구현)

| 항목 | 내용 |
|------|------|
| backend 후보 | libfuse2 / libfuse3 기반 FUSE |
| 라이선스 | GPL (시스템 라이브러리) |
| 마운트 방식 | 디렉토리 경로 |
| 테스트 상태 | 미테스트 |
| 예상 리스크 | 낮음 — FUSE는 안정된 기술 |

### 6.4 macOS (연구 — 병행 조사)

| 항목 | 내용 |
|------|------|
| backend 후보 | macFUSE, FSKit (macOS 15+), 또는 대안 |
| 라이선스 | macFUSE: 별도 라이선스 (재배포 시 확인 필요) |
| 테스트 상태 | 미테스트 (Mac 장비 필요) |
| 예상 리스크 | 높음 |

### 6.5 플랫폼 우선순위

| 순서 | 플랫폼 | 단계 | 이유 |
|------|--------|------|------|
| 1 | **Windows** | 구현 | 개발 환경, PoC 완료, Dokan 검증됨 |
| 1 | **macOS** | 연구 병행 | 주요 타겟이지만 backend 선택지 불확실 |
| 2 | **Linux / WSL** | 후속 | backend 후보(libfuse)는 안정적이나 우선순위 낮음 |

### 6.6 프로젝트 구조

```
core/              ← 공통 인터페이스, 모델, 정책 (플랫폼 무관)
backend/           ← 앱 오케스트레이션, 서비스 (플랫폼 무관)
  ├── windows/     ← Windows backend (Dokan, PTY, helper)
  ├── linux/       ← Linux backend (FUSE, PTY)
  ├── mac/         ← macOS backend (연구)
  └── wsl/         ← WSL 전용
desktop/           ← 프론트엔드 (Tauri + React)
rust/              ← Rust 코드 (참고/유지)
tests/             ← 테스트
docs/              ← 문서
prototypes/        ← PoC
```

## 7. Success Criteria

### 7.1 MVP

- Open a project folder in the app.
- Mark files/folders as protected in the file tree.
- Open a managed terminal — protected files are visible but access-denied.
- Run an AI CLI tool (e.g., Claude Code) in the terminal — it sees the file exists but cannot read it.
- Edit an unprotected file in the terminal — the change appears immediately in the file tree.
- Basic audit logging of policy changes and terminal sessions.

### 7.2 Post-MVP

- Multiple managed terminals with independent sessions.
- Optional staged review workflow.
- Terminal-to-terminal messaging.
- Agent lifecycle management (start, stop, monitor).
- Advanced policy: per-operation rules, per-agent overrides, temporary approvals.
- Network egress control (optional, via OpenSandbox or container backend).
