# AI IDE 작업 계획

## 현재 단계: 제품 재설계 (터미널 중심 + protected filesystem)

---

## 완료된 작업 (이전 구현)

### 백엔드 인프라 ✅
- PolicyEngine (deny-list 규칙 관리, 스레드 안전)
- SessionManager (세션 회전, 스레드 안전)
- EventBus (구조화 이벤트, auto-compaction, cursor)
- ToolBroker (감사 로그, 메트릭, 크기 제한)
- ReviewManager (선택적 staged writeback)
- AgentRuntimeManager (에이전트 실행/감시)
- TerminalManager (멀티 터미널, PTY, 메시징)
- 운영성: state store 캐시, trim/cap 상한, load/restore 검증, 로깅

### 프론트엔드 쉘 ✅
- Tauri 2.0 + React + TypeScript
- Monaco Editor, xterm.js, 파일 트리 (virtual scrolling)
- 3패널 레이아웃, appState reducer
- Tauri IPC + Python sidecar (JSON-line protocol)

### PoC 완료 ✅
- **Dokan filtered passthrough**: PoC 통과 (Windows, 숨김 모델 기준)
  - LGPL 라이선스 (무료 상용)
  - 읽기/쓰기/생성/삭제 → 원본에 즉시 반영
  - 주의: PoC는 이전 숨김(ENOENT) 모델 기준. 보호(EACCES) 모델로 재검증 필요

### 프로젝트 구조 리팩토링 ✅
- `ai_ide/` flat 구조 → `core/`, `backend/`, `backend/windows/` 등으로 분리
- 578 passed, 4 skipped

---

## 스펙 변경: 숨김 → 보호

이전: protected 파일을 **숨김** (ls에서 안 보임, ENOENT)
현재: protected 파일을 **보호** (ls에서 보임, 읽기/쓰기 시 Permission Denied)

| 연산 | 이전 (숨김) | 현재 (보호) |
|------|-------------|-------------|
| `ls`, `find`, `readdir` | 안 보임 | **보임** |
| `stat`, `getattr` | ENOENT | **정상 반환 (권한 없음 표시)** |
| `cat`, `read`, `open` | ENOENT | **EACCES (Permission Denied)** |
| `write`, `create` | ENOENT | **EACCES** |
| `chmod`, `chown` | N/A | **EACCES (우회 불가)** |
| `rm`, `unlink` | ENOENT | **EACCES** |

---

## 기존 구현에서 변경되는 부분

### 폐기 대상
- `backend/projection.py` — copy-based projected workspace. FilteredFSBackend으로 대체.
- `backend/runner_projected_service.py` — 복사본 안에서 명령 실행. 필터된 마운트에서 직접 실행으로 변경.
- 에디터 stage → apply/reject 기본 흐름 — 기본은 직접 저장, review는 선택 옵션.

### 수정 대상
- `backend/windows/dokan_backend.py` — ENOENT → EACCES 변경, readdir에 protected 파일 포함
- `core/filtered_fs_backend.py` — 인터페이스에 protection 모드 반영

### 축소/폐기 추가 대상
- `SessionManager`의 세션 회전 로직 — 정책 변경이 filesystem에서 즉시 반영되므로 터미널 재시작/stale 마킹 불필요. 단순 세션 ID 관리로 축소 가능.
- `TerminalManager`의 `mark_execution_session_stale()` — 같은 이유로 불필요.

### 유지 대상
- `PolicyEngine` — deny 규칙 관리. "deny = protected"로 의미만 변경, 구조 동일.
- `TerminalManager` + PTY — CWD를 필터된 마운트 포인트로 설정. (stale 로직은 제거)
- `EventBus`, `ToolBroker` — 감사/메트릭 유지.

---

## 새 계획

### 1단계: core 인터페이스 확정 (OS 공통)
- [ ] `FilteredFSBackend` 인터페이스를 protection 모델로 업데이트
- [ ] backend 계약 테스트 정의 (readdir 포함, open EACCES, chmod EACCES 등)
- [ ] PolicyEngine의 deny = protected 의미 정리

### 2단계: Windows backend 수정 (Dokan)
- [ ] `dokan_backend.py` — readdir에 protected 파일 포함
- [ ] `dokan_backend.py` — open/read/write → EACCES
- [ ] `dokan_backend.py` — getattr → 권한 0 반환
- [ ] `dokan_backend.py` — chmod/chown/rm → EACCES
- [ ] Dokan E2E 테스트 업데이트

### 3단계: UI 업데이트
- [ ] 파일트리에서 "숨김" → "보호" 용어/아이콘 변경
- [ ] 터미널에서 protected 파일이 보이지만 접근 거부되는 UX 확인

### 4단계: 크로스 플랫폼
- [ ] macOS backend 연구 (macFUSE, FSKit, 또는 대안)
- [ ] Linux FUSE backend 구현/테스트
- [ ] 플랫폼별 드라이버 설치 확인 + 안내 UX

플랫폼 우선순위:

| 순서 | 플랫폼 | 단계 |
|------|--------|------|
| 1 | Windows | 구현 (Dokan) |
| 1 | macOS | 연구 병행 |
| 2 | Linux / WSL | 후속 구현 |

### 5단계: 운영성/안정성
- [ ] 감사 로그, 메트릭 (기존 인프라 재활용)
- [ ] 정책 변경 이력
- [ ] 에러 복구 (마운트 실패, 드라이버 미설치, 터미널 크래시)
- [x] review workflow를 선택 옵션으로 제공

---

## 현재 상태 요약

**제품 방향**: 터미널 중심 IDE. 파일트리에서 보호 설정 → 터미널에서 파일은 보이지만 읽기/쓰기 거부. 복사본 없음, 동기화 없음.

**핵심 아키텍처**: FilteredFSBackend 인터페이스 → 플랫폼별 backend (Dokan/FUSE/macFUSE 등).

**테스트 기준선**: Python 백엔드 578 passed, 4 skipped.
