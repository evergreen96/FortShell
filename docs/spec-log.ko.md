# AI IDE 스펙 로그

## 목적

이 문서는 스펙이 계속 늘어나는 상황을 관리하기 위한 작업 기록이다. 새 요구사항이 나오면 먼저 이 문서에 추가하고, 구현 상태와 테스트 연결을 같이 갱신한다.

운영 원칙:

- 요구사항은 고유 ID를 가진다.
- 각 요구사항은 현재 상태를 가진다: `planned`, `in_progress`, `implemented`, `validated`
- 구현이 들어가면 관련 테스트 파일을 같이 적는다.
- 보안 관련 요구사항은 우회 경로와 현재 한계를 반드시 함께 기록한다.

## 요구사항 목록

| ID | 분류 | 요구사항 | 상태 | 구현 메모 | 테스트 |
| --- | --- | --- | --- | --- | --- |
| SEC-001 | 보안 | 사용자가 제한한 파일은 `list`, `search`, `read` 결과에 나타나면 안 된다. | validated | 현재는 broker 경로에서만 보장된다. shell 실행 전체를 강제하지는 못한다. | `tests/test_broker.py`, `tests/test_app.py` |
| SEC-002 | 보안 | 프로젝트 루트 밖 경로 접근은 차단되어야 한다. | validated | `ToolBroker`가 경로 escape를 기록하고 차단한다. | `tests/test_broker.py` |
| SEC-003 | 보안 | CLI를 통한 shell에서의 모든 명령실행이 정책에 의해 제어되고 감사 추적이 가능해야 한다. | in_progress | projected/strict preview는 direct host-path와 encoded host-path reconstruction을 차단하며, `term run` 같은 명령은 runner boundary를 통해야 한다. Linux `bwrap` invocation에서는 network unshare와 clearenv가 적용되지만, stronger process isolation과 real OS sandbox coverage는 아직 남아 있다. | `tests/test_runner.py`, `tests/test_command_guard.py`, `tests/test_terminal.py`, `tests/test_platforms.py`, 추가 security fixture |
| SEC-004 | 보안 | 정책 변경 후 stale session이 재사용되어 접근이 허용되면 안 된다. | in_progress | stale projection cleanup, stale runner terminal 처리, runner boundary에서 stale execution session 차단이 적용되어 있다. runner/index 전체 invalidation은 아직 남아 있다. | `tests/test_session.py`, `tests/test_projection.py`, `tests/test_runner.py`, `tests/test_terminal.py`, `tests/test_app.py`, 추가 integration/security test |
| SEC-005 | 보안 | projected workspace는 프로젝트 루트 밖의 별도 runtime 영역에 있어야 한다. | validated | runtime root를 외부 cache/temp 영역으로 이동해 단순 `..` 탈출 경로를 줄였다. | `tests/test_projection.py`, `tests/test_runner.py` |
| SES-001 | 세션 | 정책 변경 시 새 세션을 자동 발급해야 한다. | validated | policy version이 바뀌면 session rotation이 발생한다. | `tests/test_session.py`, `tests/test_app.py` |
| SES-002 | 세션 | execution session과 agent session은 분리된 개념으로 관리해야 한다. | validated | policy 변경은 execution+agent를 같이 회전시키고, `agent rotate`는 execution을 유지한 채 AI 세션만 교체한다. | `tests/test_session.py`, `tests/test_app.py` |
| OBS-001 | 관측성 | read/write/search/blocked/terminal 실행 수를 집계해야 한다. | validated | `UsageMetrics`와 audit log 기반 | `tests/test_broker.py`, `tests/test_terminal.py` |
| OBS-002 | 관측성 | staged write와 writeback 결정이 audit trail과 structured event stream에 가시적이어야 한다. | validated | `ai write`, `review stage/apply/reject`, conflict 결과가 broker audit와 `review.proposal.*` 이벤트로 기록된다. | `tests/test_broker.py`, `tests/test_review_event_publisher.py`, `tests/test_app.py` |
| TERM-001 | 터미널 | 여러 터미널을 동시에 열고 독립적으로 운영할 수 있어야 한다. | validated | runner-managed terminal과 explicit host terminal로 분리되어 있으며, inbox 기반으로 이벤트를 전달한다. | `tests/test_terminal.py`, `tests/test_app.py` |
| TERM-002 | 터미널 | terminal session은 current execution session에 연결되어야 하며, agent session과 독립적일 수 있다. | validated | runner terminal은 current execution session에 연결되고, host terminal은 execution session 없이 explicit unsafe 경로로 운영된다. | `tests/test_terminal.py`, `tests/test_app.py` |
| TERM-003 | 터미널 | `term run` 같은 명령은 runner boundary를 통해야 하며, host shell은 explicit opt-in으로만 허용해야 한다. | validated | `term new`는 기본적으로 projected/strict runner terminal을 생성하며, `term new --host`만 host shell을 허용한다. | `tests/test_terminal.py`, `tests/test_app.py` |
| TERM-004 | 터미널 | execution session이 교체되면 기존 runner terminal은 stale 처리되고 재사용할 수 없다. | validated | policy rotation 시 이전 execution session의 모든 runner terminal은 stale로 표시되고, 새로 `term run`을 해야 한다. | `tests/test_terminal.py`, `tests/test_app.py` |
| TERM-005 | 터미널 | terminal이 active agent run에 bind되어 runtime boundary를 통해 input을 전달할 수 있어야 한다. | validated | `term attach`와 `term input`으로 agent run에 바인딩하고 event bus를 통해 입력을 전달한다. | `tests/test_terminal.py`, `tests/test_app.py` |
| TERM-006 | 터미널 | terminal event watch는 in-memory callback 대신 persisted pull cursor로 소비해야 한다. | validated | per-watch consumer id로 `EventBus.pull_events()`를 사용한다. | `tests/test_terminal.py`, `tests/test_app.py` |
| TERM-007 | 터미널 | terminal session과 watch 정의는 같은 runtime root 재사용 시 app restart를 넘겨야 한다. | validated | runtime-backed terminal state persistence 적용. | `tests/test_terminal.py`, `tests/test_app.py` |
| TERM-008 | 터미널 | persisted terminal watch에 lease cleanup과 stale bridge unbinding을 지원해야 한다. | validated | watch heartbeat timestamp와 `term gc` 적용. | `tests/test_terminal.py`, `tests/test_app.py` |
| RUN-001 | 실행 | agent 실행용 projected workspace runner를 제공해야 한다. | validated | 기본 runner mode는 projected이며 필터된 workspace에서 명령을 실행한다. | `tests/test_projection.py`, `tests/test_runner.py`, `tests/test_app.py` |
| RUN-002 | 실행 | strict preview mode는 obvious network command, nested shell, interpreter launch, secret env 등을 차단해야 한다. | validated | command guard와 env scrubbing이 적용되어, backend가 없는 경우 interpreter launch를 차단한다. 다만 full OS sandbox는 아니다. | `tests/test_runner.py`, `tests/test_app.py`, `tests/test_command_guard.py` |
| RUN-003 | 실행 | strict mode는 platform backend가 존재하면 해당 backend를 사용하고, 없으면 guarded preview로 fallback해야 한다. | validated | backend invocation 경로를 통해 real backend를 실행하고 backend 측의 working directory를 사용하며, Linux `bwrap`에서는 network unshare와 clearenv/setenv가 invocation 계약에 포함되어 있다. launcher가 없으면 `strict-preview`로 fallback한다. `runner info`로 이 backend 상태를 확인할 수 있다. | `tests/test_runner.py`, `tests/test_platforms.py`, `tests/test_app.py` |
| ARCH-001 | 아키텍처 | 정책, 세션, 터미널, 앱 오케스트레이션은 분리된 모듈이어야 한다. | implemented | Python 기준 1차 분리 완료 | 모듈 구조 점검 |
| ARCH-002 | 아키텍처 | 공통 로직은 코어로 두고, OS별 차이는 platform adapter로 분리해야 한다. | validated | `platforms.py`에 Windows/Linux/macOS/generic adapter를 분리했다. | `tests/test_platforms.py` |
| ARCH-003 | 아키텍처 | platform adapter는 strict sandbox backend의 존재 여부를 probe할 수 있어야 한다. | validated | `runner probe`와 `strict_probe()`를 통해 invocation에 필요한 backend가 ready인지 확인하며, `status_code`와 detail로 보고한다. `runner info`로 이 결과를 UI에서 확인할 수 있다. | `tests/test_platforms.py`, `tests/test_app.py`, `tests/test_runner.py` |
| RVW-001 | 쓰기 반영 | AI 변경은 staged writeback과 review를 거쳐 host에 반영되어야 한다. | validated | 기본 `ai write`는 review proposal을 stage하고, host 직접 쓰기는 별도 `unsafe write` 명령으로만 허용한다. | `tests/test_review_manager.py`, `tests/test_app.py` |
| RVW-002 | 쓰기 반영 | runtime은 diff inspection, apply/reject, persistence, conflict-safe apply를 갖춘 staged write-review 흐름을 지원해야 한다. | validated | `review stage/list/show/apply/reject` 지원. conflict 시 현재 파일 hash와 비교. | `tests/test_review_manager.py`, `tests/test_review_state_store.py`, `tests/test_app.py` |
| RVW-003 | 쓰기 반영 | Rust에서 staged review persistence와 policy-aware apply/reject를 지원해야 한다. | validated | `ReviewController`가 Python 흐름을 미러링. | `rust/crates/ai-ide-control/tests/review_control.rs` |
| AGT-001 | 에이전트 | concrete agent CLI는 registry와 probe contract 뒤에 정규화되어야 한다. | validated | `AgentRegistry`, CLI adapter probe, launch-plan reporting 구현. | `tests/test_agents.py`, `tests/test_app.py` |
| AGT-002 | 에이전트 | current agent session kind가 runner boundary를 통해 실행 가능해야 한다. | validated | `AgentRuntimeManager`, `agent exec`, `agent history` 구현. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-003 | 에이전트 | agent run은 poll/stop 제어와 execution-session invalidation을 갖춘 long-lived process session을 지원해야 한다. | validated | `agent start/poll/stop` 구현. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-004 | 에이전트 | long-running agent run은 runtime boundary를 통한 interactive stdin write를 지원해야 한다. | validated | `agent send` 구현. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-005 | 에이전트 | agent run은 restart 후에도 resume 가능한 persisted per-run watcher를 지원해야 한다. | validated | `agent watch/watches/inbox/unwatch` 구현. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-006 | 에이전트 | persisted agent watch에 lease cleanup을 지원해야 한다. | validated | `agent gc` 구현. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-007 | 에이전트 | watch/event consumer가 explicit `agent poll` 없이 active run output을 관찰할 수 있어야 한다. | validated | `refresh_active_runs()` 구현. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-008 | 에이전트 | agent run history가 runtime restart를 넘겨야 한다. | validated | persisted `AgentRunRecord`, restored `running -> interrupted` 처리. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-009 | 에이전트 | adapter transport needs를 명시적으로 모델링해야 한다 (PTY-preferred/required 구분). | validated | adapter-level `io_mode_preference` 구현. | `tests/test_agents.py`, `tests/test_agent_runtime.py`, `tests/test_app.py` |
| AGT-010 | 에이전트 | control plane이 adapter를 launch하지 않고 transport decision을 inspect할 수 있어야 한다. | validated | `agent transport` 구현. | `tests/test_agent_runtime.py`, `tests/test_app.py` |
| EVT-001 | 이벤트 | agent lifecycle, agent output, terminal messaging이 structured event channel을 통해 관찰 가능해야 한다. | validated | `EventBus`, `events list` 구현. | `tests/test_events.py`, `tests/test_agent_runtime.py`, `tests/test_terminal.py`, `tests/test_app.py` |
| EVT-002 | 이벤트 | event channel이 incremental cursor read와 terminal subscription을 지원해야 한다. | validated | `events tail`, `term watch`, `term inbox` 구현. | `tests/test_events.py`, `tests/test_terminal.py`, `tests/test_app.py` |
| EVT-003 | 이벤트 | event cursor가 같은 runtime root 재사용 시 runtime restart를 넘겨야 한다. | validated | append-only JSONL log 구현. | `tests/test_events.py`, `tests/test_app.py` |
| EVT-004 | 이벤트 | UI/daemon consumer가 runtime 내부에 자체 incremental event cursor를 persist할 수 있어야 한다. | validated | consumer cursor checkpoint 구현. | `tests/test_events.py`, `tests/test_app.py` |
| EVT-005 | 이벤트 | 같은 runtime root를 공유하는 여러 app instance가 event id를 monotonic하게 유지해야 한다. | validated | advisory lock file 구현. | `tests/test_events.py`, `tests/test_app.py` |
| EVT-006 | 이벤트 | persisted event log가 consumer cursor를 깨뜨리지 않는 bounded compaction을 지원해야 한다. | validated | cursor-aware `compact()` 구현. | `tests/test_events.py`, `tests/test_app.py` |
| EVT-007 | 이벤트 | abandoned consumer cursor를 제거할 수 있어야 한다. | validated | `events gc` 구현. | `tests/test_events.py`, `tests/test_app.py` |
| EVT-008 | 이벤트 | pull-based consumer가 exact source로 필터링하면서 cursor를 advance할 수 있어야 한다. | validated | source-aware filtered event pull 구현. | `tests/test_events.py`, `tests/test_agent_runtime.py`, `tests/test_terminal.py` |
| POL-001 | 정책 | project-level deny rule이 app restart를 넘겨야 한다. | validated | `.ai-ide/policy.json` persistence 구현. | `tests/test_policy_state_store.py`, `tests/test_policy.py`, `tests/test_app.py` |
| POL-002 | 정책 | 공유 project policy 변경이 command boundary에서 sync되어야 한다. | validated | 외부 policy 변경 감지 시 session rotation과 agent kind 보존. | `tests/test_session.py`, `tests/test_app.py` |
| POL-003 | 정책 | Rust에서 project-local deny rule을 load/save할 수 있어야 한다. | validated | `PolicyStateStore` (Rust) 구현. | `rust/crates/ai-ide-persistence/tests/policy_state_store.rs` |
| POL-004 | 정책 | Rust에서 Python의 external-policy sync behavior를 보존해야 한다. | validated | `ControlPlane::sync_from_store()` 구현. | `rust/crates/ai-ide-control/tests/policy_control.rs` |
| AUD-001 | 감사 | broker usage metrics와 audit history가 app restart를 넘겨야 한다. | validated | runtime-local broker persistence 구현. | `tests/test_broker_state_store.py`, `tests/test_broker.py`, `tests/test_app.py` |
| ARC-001 ~ ARC-025 | 아키텍처 | Python 모듈 분리 (commands, agent seams, terminal seams, runner seams, review, bootstrap) 및 Rust workspace 초기화. | validated | 모든 seam extraction 완료. | 각 seam별 전용 테스트 |
| ARC-026 ~ ARC-035 | 아키텍처 | Rust policy/review persistence, control plane, host adapter, Python bridge, session sync. | validated | Rust sidecar 기반 incremental cutover 완료. | `rust/crates/*/tests/*.rs`, `tests/test_rust_host_*.py` |
| ARC-036 ~ ARC-070 | 아키텍처 | Rust runner/terminal/event/agent command modules, projection, CLI dispatch, config/env layering. | validated | Rust CLI에서 Python 기능 대부분을 parity로 구현. | `rust/crates/*/tests/*.rs` |
| ARC-071 ~ ARC-100 | 아키텍처 | Windows strict helper contract (validation, stdio-proxy, process control, path policy, filesystem boundary). | validated | Windows restricted host helper stub 및 실제 helper 기반 strict backend 구현. | `tests/test_windows_*.py`, `rust/crates/ai-ide-windows-helper/tests/*.rs` |
| ARC-101 ~ ARC-150 | 아키텍처 | Windows helper 심화 (integrity labels, reparse guards, projected workspace aliases, mutable-root policy, env validation). | validated | Windows 보안 경계 심화 구현. | `rust/crates/ai-ide-windows-helper/tests/*.rs` |
| ARC-151 ~ ARC-208 | 아키텍처/보안 | Windows helper read boundary, blocked-read roots, internal metadata hiding, boundary tier split, workspace-only scope. | validated | workspace-only scope와 내부 메타데이터 은닉 경로 구현 완료. | `rust/crates/ai-ide-windows-helper/tests/*.rs` |

## 구현 현황 요약

### 보안 (SEC)
- **SEC-001** (validated): broker 경로 기반 파일 접근 차단
- **SEC-002** (validated): 프로젝트 루트 밖 경로 차단
- **SEC-003** (in_progress): shell 명령 정책 제어 및 감사 추적 - projected/strict preview 적용, Linux bwrap 적용, stronger isolation 미완
- **SEC-004** (in_progress): stale session 재사용 차단 - stale projection/terminal 처리 적용, runner/index 전체 invalidation 미완
- **SEC-005** (validated): projected workspace 외부 runtime 영역 분리

### 세션 (SES)
- **SES-001**, **SES-002** (validated): policy-driven session rotation, execution/agent session 분리

### 관측성 (OBS)
- **OBS-001**, **OBS-002** (validated): usage metrics, audit log, review event stream

### 터미널 (TERM)
- **TERM-001 ~ TERM-008** (validated): 다중 터미널, session binding, runner boundary, stale 처리, agent bridge, pull-based watch, persistence, lease GC

### 실행 (RUN)
- **RUN-001** (validated): projected workspace runner
- **RUN-002** (validated): strict preview mode command guard 및 env scrubbing
- **RUN-003** (validated): strict backend 선택 및 preview fallback

### 쓰기 반영 (RVW)
- **RVW-001 ~ RVW-003** (validated): staged writeback, review flow, Rust parity

### 에이전트 (AGT)
- **AGT-001 ~ AGT-010** (validated): registry, runtime execution, streaming, stdin, watch/inbox, history persistence, transport resolution

### 이벤트 (EVT)
- **EVT-001 ~ EVT-008** (validated): event bus, cursor, persistence, multi-instance locking, compaction, GC, filtered pull

### 정책 (POL)
- **POL-001 ~ POL-004** (validated): policy persistence, multi-instance sync, Rust parity

### 감사 (AUD)
- **AUD-001** (validated): broker state persistence

### 아키텍처 (ARC/ARCH)
- **ARCH-001 ~ ARCH-003** (validated/implemented): 기본 모듈 분리, platform adapter, probe
- **ARC-001 ~ ARC-035** (validated): Python seam extraction + Rust workspace/control/adapter/bridge
- **ARC-036 ~ ARC-070** (validated): Rust CLI parity (runner, terminal, event, agent, config)
- **ARC-071 ~ ARC-208** (validated): Windows strict helper 전체 (validation, stdio-proxy, integrity labels, filesystem boundary, read isolation, scope split)

## 최근 변경

### 2026-03-11 (최종 상태)

- Windows helper boundary tier split 완료: workspace-only scope 적용
- workspace-only runtime proof 및 모듈 분리 완료
- runner status에 `strict_boundary_scope` 노출
- 테스트 현황: Python 414개 (symlink 2개 조건부 skip), Rust 325개 이상

### 2026-03-09 ~ 2026-03-10 주요 진행

- Windows helper read boundary 슬라이스 (blocked-read roots, hidden-internal read guard, write boundary)
- Filesystem boundary seed logic 및 integrity label 적용
- Reparse-point guard 및 mutable-root alias policy
- Projected workspace alias blocking
- Shell-wrapper path-argument blocking

### 2026-03-08 주요 진행

- Windows restricted host helper stub 및 strict backend validator
- Helper stdio-proxy process contract
- Path policy, execution guard, child environment validation
- Runner/platform Windows strict helper integration

### 2026-03-07 주요 진행

- Agent adapter registry, runtime execution, streaming, stdin, watch/inbox, history persistence
- Event bus 전체 (persistence, cursor, compaction, GC, filtered pull, multi-instance locking)
- Terminal watch/bridge/persistence/GC seam extraction
- Runner seam extraction (status, dispatch, strict, projected, process, environment, host)
- Broker/policy/review persistence 및 Rust cutover
- Rust workspace 초기화 및 host adapter/bridge/session sync
- Command layer modularization

### 2026-03-06 초기 구축

- Python 프로토타입 모듈 구조 분리
- `PolicyEngine`, `SessionManager`, `ToolBroker`, `TerminalManager`, `AIIdeApp` 개별 모듈화
- projected workspace, runner, platform adapter 계층 추가
- strict preview mode에서 command guard, secret env scrubbing, preview interpreter guard를 추가했다.
- execution/agent/terminal session 역할 분리
- 초기 테스트 55개가 통과하며, known failure 없이 완료되었다.
- default terminal path를 runner boundary로 전환했고, explicit host terminal만 unsafe 경로로 남겼다.

## 다음 우선순위

1. `SEC-003`: strict sandbox의 stronger process isolation 추가
2. `SEC-004`: session rotation 시 stale runner/index invalidation 완성
3. Windows helper: workspace-only 계약 정리 및 남은 dead/legacy 경로 축소
