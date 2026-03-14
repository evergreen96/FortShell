# AI IDE 작업 계획

## 현재 단계: 제품 재설계 (터미널 중심 + filtered filesystem)

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
- **Dokan filtered passthrough**: 12/12 테스트 통과 (Windows)
  - ls/find/rg/grep/cat 전부에서 숨긴 파일 안 보임
  - 읽기/쓰기/생성/삭제 → 원본에 즉시 반영
  - 파일 단위 + 폴더 단위 숨김 모두 동작
  - LGPL 라이선스 (무료 상용)
- **ProjFS**: write-through 구조적으로 부적합 → 탈락
- **WinFsp**: 동작하지만 GPLv3 ($6K/3yr) → 탈락

---

## 기존 구현에서 변경되는 부분

### 폐기 대상
- `ai_ide/projection.py` — copy-based projected workspace (shutil.copy2). FilteredFSBackend으로 대체.
- `ai_ide/runner_projected_service.py` — 복사본 안에서 명령 실행. 필터된 마운트 포인트에서 직접 실행으로 변경.
- 에디터의 stage → apply/reject 기본 흐름 — 기본은 직접 저장, review는 선택 옵션으로 내림.

### 축소/변경 대상
- `ai_ide/editor_service.py` — stage/apply를 기본이 아닌 옵션으로. 기본 편집은 원본 직접 저장.
- `ai_ide/review_manager.py` — 삭제하지 않지만 핵심 경로에서 선택 경로로 이동.
- `desktop/src/App.tsx` 프론트 — stage/apply/reject UI를 기본 흐름에서 내림.

### 유지 대상
- `PolicyEngine` — deny 규칙 관리. FilteredFSBackend에 규칙을 전달하는 역할로 유지.
- `TerminalManager` + PTY — 멀티 터미널 관리. CWD만 필터된 마운트 포인트로 변경.
- `EventBus`, `SessionManager`, `ToolBroker` — 감사/세션/메트릭은 그대로 유지.
- `AgentRuntimeManager` — 에이전트 실행/감시 유지.

---

## 새 계획

### 1단계: FilteredFSBackend 인터페이스 + Dokan 기반 Windows backend 구현
- [x] `FilteredFSBackend` 공통 인터페이스 정의 (mount, unmount, update_policy)
- [x] Dokan backend 구현 (PoC 코드를 backend 인터페이스로 정리)
- [x] PolicyEngine 변경 → backend에 deny 목록 동적 전달
- [x] 앱 시작 시 마운트 자동화, 종료 시 unmount
- [x] 마운트 포인트를 터미널 CWD로 설정

### 2단계: UI 레이아웃 재설계
- [x] 터미널을 주 패널로 (상단 우측)
- [x] 파일 트리를 좌측 패널 (원본 전체 보기 + 숨김 토글)
- [x] 에디터를 하단 패널 (보조)
- [x] 파일트리 숨김 토글 → PolicyEngine → backend 반영

### 3단계: 멀티 터미널 강화
- [x] 관리형 터미널: CWD = 필터된 마운트 포인트
- [x] PTY 기반 실제 터미널 (claude code, codex 등 실행)
- [x] 터미널 탭 관리
- [x] 비관리형 터미널 (선택 옵션, 원본 CWD, "unfiltered" 표시)

### 4단계: 크로스 플랫폼
- [ ] macOS backend 연구 (macFUSE, FSKit, 또는 대안)
- [ ] Linux FUSE backend 구현/테스트
- [ ] 플랫폼별 드라이버 설치 확인 + 안내 UX

플랫폼 우선순위:

| 순서 | 플랫폼 | 단계 |
|------|--------|------|
| 1 | Windows | 구현 (Dokan 검증됨) |
| 1 | macOS | 연구 병행 (backend 후보 조사) |
| 2 | Linux / WSL | 후속 구현 |

### 5단계: 운영성/안정성
- [ ] 감사 로그, 메트릭 (기존 인프라 재활용)
- [ ] 정책 변경 이력
- [ ] 에러 복구 (마운트 실패, 드라이버 미설치, 터미널 크래시)
- [x] review workflow를 선택 옵션으로 제공

---

## 현재 상태 요약

**제품 방향**: 터미널 중심 IDE. 파일트리에서 숨김 설정 → 터미널에서 파일시스템 수준으로 안 보임. 복사본 없음, 동기화 없음.

**핵심 아키텍처**: FilteredFSBackend 인터페이스 → 플랫폼별 backend (Dokan/FUSE/macFUSE 등).

**테스트 기준선**: Python 백엔드 578 passed, 4 skipped. Dokan PoC 12/12 통과.
