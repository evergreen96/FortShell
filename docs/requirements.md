# AI IDE Requirements

## 1. Product Definition

### 1.1 Problem

Existing IDEs treat AI as an assistant attached to the editor. This product is different: it is a **terminal-centric IDE** where multiple AI CLI tools run simultaneously, and the user controls what each terminal can see at the filesystem level.

The core requirement is not "good autocomplete" or "AI code editor". It is:

- **Terminal is the main surface.** Users run Claude Code, Codex CLI, Gemini CLI, Cursor CLI, and similar tools in managed terminals.
- **File visibility control.** Users select files/folders to hide from the file tree. Hidden files become truly invisible to all terminal processes — `ls`, `find`, `rg`, `cat`, `grep`, direct path access, and any other filesystem operation.
- **Live workspace.** Changes made by CLI tools in the terminal are immediately reflected in the file tree and editor. No copy, no sync, no approval step required.
- **Editor is secondary.** A code editor exists for viewing and editing files, but it is not the main workflow surface.

### 1.2 Product Thesis

The main screen is:

```
┌─────────────────────────────────────────────┐
│  File Tree (full view)  │  Terminal (filtered view)  │
│  - see all files        │  - hidden files don't exist │
│  - checkboxes to hide   │  - multiple CLI tabs        │
├─────────────────────────┴──────────────────────────────┤
│  Code Editor (secondary, read/edit visible files)      │
└────────────────────────────────────────────────────────┘
```

- File tree shows ALL files, with controls to mark files/folders as hidden
- Terminals run inside a **filtered filesystem view** where hidden files are absent
- Editor opens files from the original project for viewing/editing

### 1.3 Architecture: Filtered Filesystem

The filtered view is implemented using a **userspace filesystem driver** that mirrors the project directory while hiding denied paths:

```
Original project (C:/projects/myapp/):     Filtered view (Z:/ or mount point):
├── notes/todo.txt                         ├── notes/todo.txt    ← original file
├── secrets/token.txt                      (absent)
├── .env                                   (absent)
└── src/main.py                            └── src/main.py       ← original file
```

- **No file copies.** The filtered view reads/writes through to original files.
- **No sync needed.** Changes in the terminal are immediately visible in the file tree and vice versa.
- Hiding is enforced at the OS filesystem level — all programs see the same filtered view.

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
- **Visibility control, not write control.** The product controls what AI tools can *see*, not what they can *do*. Writes go directly to the original files.
- **Filesystem-level enforcement.** Hidden files are invisible to all processes — not just specific tools.
- **No copies, no sync.** The filtered view is a live proxy to the original project.
- **Editor is a companion.** The editor exists for convenience, not as the main surface.
- **Policy is simple.** Select files/folders in the tree → they disappear from the terminal view. That's it.
- **Review is optional.** Staged writeback/approval is available but not the default workflow.
- **Observability is a first-class surface.** Audit logs, metrics, and session tracking are always available.

## 3. Functional Requirements

### 3.1 File Visibility Policy

- Users mark files/folders as hidden via the file tree UI (checkboxes or toggle).
- Hidden files must be invisible at the filesystem level to all processes running in managed terminals.
- This means `ls`, `find`, `rg`, `grep`, `cat`, `python`, `node`, and any other process cannot discover or access hidden files through the filtered view.
- Policy changes (hide/unhide) must update the filtered filesystem view. Existing terminals may require restart or re-mount.
- Policy is stored per-project (e.g., `.ai-ide/policy.json`).

### 3.2 Managed Multi-Terminal

- The IDE must support many terminals at once with independent state.
- Each terminal runs with its working directory set to the filtered filesystem view.
- Terminals must support PTY semantics: resize, ANSI colors, interactive prompts.
- Terminal types:
  - **Managed terminal:** CWD is the filtered view. AI CLI tools run here.
  - **Unrestricted terminal (optional):** CWD is the original project. For user's own use, marked as "unfiltered".

### 3.3 File Tree

- Shows ALL files in the original project directory.
- Provides controls (checkboxes, toggles) to mark files/folders as hidden.
- Reflects filesystem changes in real-time (file watcher).
- Hidden files are visually distinguished (dimmed, icon, strikethrough) but still visible in the tree for management purposes.

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

- Hidden files must be enforced at the filesystem level, not by tool cooperation.
- The filtered filesystem view must prevent path traversal (`../secrets/`) and symlink escape.
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
  PolicyEngine       → deny/allow 규칙 관리
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
| 테스트 상태 | PoC 12/12 통과 |
| 배포 | Dokan 드라이버 + 앱 exe를 installer에 포함 |

설치 흐름:
```
앱 설치 프로그램 실행
  → Dokan 드라이버 설치 여부 확인
  → 미설치 시 Dokan MSI 자동 설치 (UAC 1회)
  → 앱 설치
  → 이후 실행은 일반 권한
```

### 6.3 Linux (후보 — 후속 구현)

| 항목 | 내용 |
|------|------|
| backend 후보 | libfuse2 / libfuse3 기반 FUSE |
| 라이선스 | GPL (시스템 라이브러리) |
| 마운트 방식 | 디렉토리 경로 |
| 테스트 상태 | 미테스트 |
| 예상 리스크 | 낮음 — FUSE는 안정된 기술 |

주의사항:
- libfuse2 vs libfuse3 API 차이 존재
- WSL2에서는 FUSE 커널 모듈 미포함 가능
- 구현 바인딩은 Windows backend 확정 후 결정

### 6.4 macOS (연구 — 병행 조사)

| 항목 | 내용 |
|------|------|
| backend 후보 | macFUSE, FSKit (macOS 15+), 또는 대안 |
| 라이선스 | macFUSE: 별도 라이선스 (재배포 시 확인 필요) |
| 테스트 상태 | 미테스트 (Mac 장비 필요) |
| 예상 리스크 | 높음 |

리스크:
- macOS 시스템 확장 정책이 점점 엄격해짐 (SIP, KEXT → SystemExtension 전환)
- macFUSE 설치 UX가 사용자에게 부담 (시스템 환경설정 승인, 재시동)
- macFUSE 4.x부터 FSKit 백엔드 전환 중 — API 안정성 미확인
- Apple Silicon / Intel 모두 지원 필요
- **backend 선택지가 아직 확정되지 않음** — Windows Dokan과 같은 수준의 검증이 필요

### 6.5 플랫폼 우선순위

| 순서 | 플랫폼 | 단계 | 이유 |
|------|--------|------|------|
| 1 | **Windows** | 구현 | 개발 환경, PoC 완료, Dokan 검증됨 |
| 1 | **macOS** | 연구 병행 | 주요 타겟이지만 backend 선택지 불확실 (macFUSE 설치 UX, 라이선스, SIP 정책) |
| 2 | **Linux / WSL** | 후속 | backend 후보(libfuse)는 안정적이나 우선순위 낮음 |

### 6.6 공통 코드 vs 플랫폼별 코드

```
공통 (플랫폼 분기 없음):
  filtered_fs_backend.py     ← FilteredFSBackend 인터페이스 정의
  policy_engine.py           ← deny/allow 규칙
  file_watcher.py            ← 파일트리 실시간 반영
  audit.py                   ← 감사 로그
  앱 UI 전체                 ← Tauri + React

플랫폼별 backend 구현:
  backends/
    ├── dokan_backend.py     ← Windows (Dokan, 검증됨)
    ├── fuse_backend.py      ← Linux (libfuse, 후보)
    └── macos_backend.py     ← macOS (미정, 연구 필요)

플랫폼별 유틸리티:
  mount_manager.py           ← 마운트 포인트 관리 (드라이브 문자 vs 디렉토리)
  driver_check.py            ← 드라이버 설치 여부 확인
```

## 7. Success Criteria

### 7.1 MVP

- Open a project folder in the app.
- Mark files/folders as hidden in the file tree.
- Open a managed terminal — hidden files are invisible to all commands.
- Run an AI CLI tool (e.g., Claude Code) in the terminal — it cannot find hidden files.
- Edit a file in the terminal — the change appears immediately in the file tree.
- Basic audit logging of policy changes and terminal sessions.

### 7.2 Post-MVP

- Multiple managed terminals with independent sessions.
- Optional staged review workflow.
- Terminal-to-terminal messaging.
- Agent lifecycle management (start, stop, monitor).
- Advanced policy: per-operation rules, per-agent overrides, temporary approvals.
- Network egress control (optional, via OpenSandbox or container backend).
