# AI IDE 아키텍처 가드레일

## 목적

이 문서는 구현이 커지더라도 핵심 경계가 무너지지 않도록 강제하는 설계 규칙이다. 새 기능을 추가할 때는 이 규칙을 먼저 확인해야 한다.

## 핵심 규칙

### 1. 정책 강제는 UI가 아니라 코어 런타임이 담당한다.

- 렌더러나 프론트엔드는 보안 경계가 아니다.
- 파일 가시성, 세션 유효성, 실행 권한은 코어 런타임에서만 판정한다.

### 2. 정책 모듈은 가능한 한 순수해야 한다.

- `PolicyEngine`은 UI, 터미널, 네트워크 세부 구현을 몰라야 한다.
- 정책 규칙은 입력과 출력이 명확해야 하고, 테스트가 쉬워야 한다.

### 3. 세션 모듈은 정책 버전과 독립적으로 검사 가능해야 한다.

- 세션 교체 규칙은 다른 상태와 섞이지 않게 유지한다.
- stale session 판정은 향후 runner와 index에도 재사용 가능해야 한다.
- execution session, agent session, terminal session은 책임이 다르므로 하나의 타입으로 뭉개지지 않게 유지한다.

### 4. 보안이 필요한 경로는 host filesystem을 직접 노출하지 않는다.

- production 방향에서는 agent CLI가 host workspace를 직접 보면 안 된다.
- projected workspace 또는 별도 runner 경계 안에서만 파일을 보게 한다.
- projected workspace는 가능하면 프로젝트 루트 밖의 runtime 영역에 만들어 단순 상대 경로 탈출을 줄인다.

### 5. adapter는 통합을 담당하고, 정책을 소유하면 안 된다.

- Claude Code, Codex CLI, Gemini CLI, OpenCode adapter는 실행과 이벤트 정규화만 담당한다.
- 보안 정책 차단 여부는 adapter가 아니라 코어 런타임이 결정한다.

### 6. 터미널은 raw stream과 structured control을 분리한다.

- PTY 출력은 그대로 유지한다.
- 터미널 간 메시징이나 에이전트 제어는 별도 구조화 채널로 처리한다.
- terminal session은 execution session에 연결될 수 있지만, agent session과 동일한 생명주기를 강제하면 안 된다.

### 7. 감사 로그는 우회 없이 남아야 한다.

- 차단 이벤트는 reason code와 함께 기록한다.
- 정책 위반, path escape, session rotation은 모두 추적 가능해야 한다.

## 현재 상태 점검

- 현재 Python 기준 모듈 분리는 시작되었다.
- projected workspace runner와 platform adapter 경계는 추가되었다.
- platform adapter가 strict backend 준비 상태를 보고하도록 probe 경계도 추가되었다.
- probe 결과가 단독으로 보안 결정을 내려서는 안 되며, backend invocation 경로가 ready 상태에서만 활성화되어야 한다.
- probe 결과는 stable한 `status_code`를 통해 보고되어야 하고, `runner info` 같은 명령으로 UI에 노출할 수 있어야 한다.
- strict backend invocation은 host의 subprocess cwd가 아니라 backend 측의 working directory를 기준으로 동작해야 한다.
- Linux `bwrap` backend에서 추가된 network unshare, env clearenv, shell 제한 같은 기능은 backend invocation 계약에 포함되어야 한다.
- real backend launcher가 존재하면 strict path를 우선 사용하고, guarded preview는 fallback으로만 사용하되 runner 선택은 명시적으로 이루어져야 한다.
- session rotation 시 stale projection 정리도 app 경계에서 수행된다.
- execution session이 교체되면 기존 runner terminal은 stale 처리되어야 하고, 새 session에 대해 새로 생성되어야 한다.
- stale execution session의 terminal은 재사용할 수 없으며, runner boundary에서 session id를 검증할 수 있어야 한다.
- strict mode는 backend 준비 여부에 따라 real backend 또는 preview fallback을 고르되, 이 선택 로직은 runner/adapter 경계에 있어야 한다.
- raw shell command 실행은 여전히 강한 보안 경계가 아니다.
- strict preview는 obvious network command, nested shell, interpreter launch, secret env 노출을 차단하지만, full OS sandbox는 아니다.
- projected runner는 direct/encoded host-path 접근을 제한하지만, stronger process isolation 없이는 완전한 strict sandbox가 아니다.
- `term run` 같은 명령은 runner transport를 통해야 하며, host shell은 `term new --host` 같은 explicit unsafe 경로로만 허용해야 한다.
- 따라서 `SEC-003`이 해결되기 전까지는 projected mode가 "더 안전한 실행 모드"일 뿐, strict sandbox로 간주하지 않는다.

## 코드 리뷰 체크리스트

- 이 변경이 policy/session/terminal/app 경계를 흐리게 만들지 않는가
- 새 기능이 차단 이벤트와 메트릭을 남기는가
- 보안 요구사항이 늘었다면 `docs/spec-log.ko.md`가 같이 갱신되었는가
- 테스트가 구현과 1:1에 가깝게 추가되었는가
- 현재 한계를 가리는 대신 명시하고 있는가

### 1. 에이전트 어댑터 및 실행

- 에이전트별 launcher 감지와 launch-plan 로직은 `ai_ide/agents.py`에 유지한다. PATH 탐색을 policy, runner, terminal 모듈로 옮기지 않는다.
- adapter probe는 신뢰할 수 없는 host capability 확인으로 취급한다. ready probe만으로 sandbox나 policy 경계를 완화하지 않는다.
- agent kind는 세션 교체 전 app 경계에서 검증한다. agent session 상태와 adapter readiness는 구분을 유지한다.
- 구체적 에이전트 실행은 `AgentRuntimeManager`를 통해 라우팅한다. app 코드가 직접 subprocess 호출을 조립하지 않는다.
- 에이전트 실행은 projected/strict runner 모드만 사용한다. host mode는 일반 에이전트 실행 경로 밖에 둔다.
- 세션 메타데이터는 implicit global state가 아니라 명시적 env overlay로 자식 프로세스에 전달한다.
- 장기 실행 에이전트 프로세스 감독은 `AgentRuntimeManager` 안에 둔다. 활성 에이전트 프로세스는 terminal session이 아니라 execution session에 바인딩한다.
- 프로세스 출력은 projected workspace 밖의 runtime 관리 파일에 저장한다. adapter는 argv 기반 실행 경로를 우선한다.
- 대화형 에이전트 입력은 runtime 관리 run id를 통해 라우팅한다. stdin 쓰기는 활성 run에만 허용하고, stale run에는 거부한다.
- adapter transport 선호도는 registry 메타데이터에 명시한다. `pty_required` adapter는 PTY transport가 준비될 때까지 fail closed 상태를 유지한다.
- transport preflight 검사는 실제 CLI를 실행하지 않고 수행한다. pipe fallback 사용 시 degradation을 launch 전에 표시한다.

### 2. 이벤트 시스템

- 이벤트 채널은 구조화되고 append-only로 유지한다. downstream 기능이 terminal 텍스트를 파싱하는 것을 primary contract로 삼지 않는다.
- 이벤트 발행은 소스에 가까운 runtime 모듈(`AgentRuntimeManager`, `TerminalManager`)에서 수행한다. 이벤트 id는 향후 증분 UI 동기화의 안정적 커서로 사용한다.
- 이벤트 지속성은 append-only, cursor 친화적으로 유지한다. 지속된 이벤트는 projected workspace가 아니라 runtime root에 바인딩한다.
- 소비자 checkpoint는 이벤트 스트림과 분리한다. cursor 전진은 과거 이벤트 레코드를 변경하지 않는다.
- 이벤트 로그와 cursor store는 공유 runtime 리소스로 취급한다. 쓰기 시 advisory lock을 획득하고, `evt-` 번호를 단조 유지한다.
- compaction 시 최신 유지 윈도우와 cursor 고정 앵커 이벤트는 절대 제거하지 않는다. compacted 이벤트 로그는 lock 하에서 원자적으로 다시 쓴다.
- stale cursor는 `updated_at` 메타데이터로 관리한다. 정리는 명시적으로만 수행하고, cursor checkpoint 제거와 고정 이벤트 트리밍은 별도 단계로 분리한다.
- filtering 로직은 `EventBus.pull_events()`에 두어 모든 소비자가 동일한 cursor 전진 시맨틱을 공유한다. source 인식 필터를 사용한다.
- EventBus payload 형성은 event publisher seam 뒤에 둔다. runtime 오케스트레이션과 subprocess 감독이 각각 payload를 직접 만들지 않는다.
- 현재 in-memory 이벤트 버스는 PoC 경계이다. cross-process 전달, 복제, 보존 정책은 이후 단계에 속한다.

### 3. 터미널 관리

- terminal-to-agent 바인딩은 명시적으로 유지한다. 사용자나 UI가 명시적으로 연결하지 않는 한 terminal이 에이전트 run에 자동 전달하지 않는다.
- execution-session 경계를 유지하여 terminal을 에이전트 run에 연결한다. 세션 불일치 시 거부한다.
- terminal 입력은 run id와 runtime API를 통해 전달한다. shell command를 합성하여 에이전트를 대상으로 하지 않는다.
- terminal inbox 전달은 runtime event cursor에서 파생한다. 새 terminal watch는 명시적 replay 요청이 없는 한 현재 tail에서 시작한다.
- terminal 세션 메타데이터와 watch 정의는 runtime root에 지속한다. 이전 execution session의 runner terminal은 명시적 재바인딩 없이 active로 복원하지 않는다.
- terminal 지속성과 이벤트 지속성은 분리한다. terminal 레코드는 로컬 UI 상태를, 이벤트 버스는 미확인 구조화 이벤트의 source of truth를 저장한다.
- terminal/session watch 지속성, JSON 스키마 호환, 원자적 파일 쓰기, advisory locking은 전용 terminal state store seam 뒤에 둔다.
- terminal event 구독, bridge-watch 바인딩, inbox 동기화, stale-watch 정리는 전용 terminal watch manager seam 뒤에 둔다.
- blocked-command 포맷팅, runner-vs-host dispatch, terminal command 메트릭, `terminal.command.*` 이벤트 발행은 전용 terminal command execution seam 뒤에 둔다.
- watch 정의는 leased runtime 상태로 취급한다. stale watch 정리 시 event-bus consumer cursor도 함께 정리한다. PTY 멀티플렉싱은 이후 플랫폼별 단계에 속한다.

### 4. 런너 실행

- runner mode 검증, runner 선택, stale execution-session 차단은 전용 runner dispatch seam 뒤에 둔다. `RunnerManager`는 runner를 빌드하고 실행 dispatch를 위임한다.
- platform capability/probe 형성, runner 상태 payload 조립, CLI 상태 텍스트는 전용 runner status seam 뒤에 둔다. runner 상태 코드는 mode 라우팅, command guard 결정, 프로젝션 실체화를 소유하지 않는다.
- strict guard 평가, backend invocation 시도, backend-launch fallback, guarded preview env 형성은 전용 strict execution seam 뒤에 둔다. `StrictRunner`는 thin wrapper로 유지한다.
- 프로젝션 실체화, projected-workspace command 실행, env-overlay subprocess 실행은 전용 projected execution seam 뒤에 둔다. `ProjectedRunner`는 thin wrapper로 유지한다.
- host-mode command 실행, host argv 프로세스 실행, host artifact-root 정책은 전용 host runner seam 뒤에 둔다. `HostRunner`는 thin wrapper로 유지한다.
- 공유 subprocess 실행, background-process artifact/log 생성, runner process-handle 구성은 전용 runner process seam 뒤에 둔다. 각 concrete runner가 `subprocess.run`/`subprocess.Popen`을 inline하지 않는다.
- strict-preview 환경 정리, env overlay 병합, argv-to-command 정규화는 전용 runner environment seam 뒤에 둔다.
- 각 seam은 향후 Rust runtime 대체 경계이다. dispatch, strict/projected 정책, 프로세스 감독, 환경 형성을 독립적으로 교체할 수 있어야 한다.

### 5. 감사 및 상태 저장

- broker 메트릭/감사 지속성은 전용 broker state store seam 뒤에 둔다. `ToolBroker`가 JSON 스냅샷 포맷이나 lock 처리를 inline하지 않는다.
- 지속된 broker 상태는 runtime root 아래에 둔다. agent 가시 프로젝트 파일 아래에 두지 않아 감사 보존이 model 가시 사이드 채널을 만들지 않게 한다.
- 감사 CLI 포맷팅과 필터링은 command seam에 둔다. broker 코드는 canonical 이벤트를 기록하고 표시 로직은 enforcement 경계 밖에 둔다.
- broker 상태는 차단된 작업 포함 각 AI 파일 작업 경로 후에 지속한다.
- project-local 정책 지속성은 전용 policy state store seam 뒤에 둔다. 지속된 정책은 execution/agent 세션 구성 전에 로드한다.
- `.ai-ide/` 프로젝트 메타데이터는 내부 runtime 메타데이터로 취급한다. broker listing/read/grep과 projected workspace는 지속된 정책 파일을 model에 노출하지 않는다.
- command 경계 정책 동기화는 app/control-plane 레이어에 둔다. 외부 정책 변경은 agent kind를 유지하면서 execution-session 갱신을 강제한다.
- staged write proposal 지속성은 전용 review state store seam 뒤에 둔다. review proposal 생명주기는 review manager seam에 속하며 `ToolBroker`에 속하지 않는다.
- review apply는 쓰기 전 현재 host 파일 상태를 staged base snapshot과 비교한다. 충돌 시 `conflict`로 표시한다.
- AI 기본 write 명령은 staged review 경로를 먼저 대상으로 한다. 직접 host-file 변경은 explicit unsafe override로만 허용한다. `ai` command namespace 밖에 둔다.
- trusted-caller 확인은 전용 command-access seam 뒤에 둔다. review 이벤트 발행도 전용 seam 뒤에 둔다.
- 감사 추적은 공유 runtime audit trail을 재사용하며 ad-hoc per-command 감사 파일을 만들지 않는다.

### 6. 아키텍처 경계

- `AIIdeApp`은 composition root와 최상위 dispatcher로 유지한다. 모든 domain command를 inline 구현하는 곳이 아니다.
- command 모듈은 app wiring 객체에 의존할 수 있지만, 파싱과 포맷팅 로직은 `app.py`에서 밖으로 이동한다. 공유 CLI 파싱 규칙은 공통 helper 모듈에 둔다.
- transport 정책 매핑은 `AgentRuntimeManager` 밖에 둔다. runtime-local 상태 지속성은 store/repository seam 뒤에 둔다.
- 활성 subprocess 생명주기 관리는 supervision seam 뒤에 둔다. supervisor는 live process handle만 소유한다. 지속된 run 레코드와 복원 이력 조정은 runtime 책임이다.
- watch 생성, replay 시맨틱, event-bus cursor 갱신, stale lease 정리는 전용 watch manager seam 뒤에 둔다.
- 레코드 생성, 상태 매핑, 이력 필터링, 복원 run 조정은 ledger seam 뒤에 둔다. result-to-status 매핑을 중앙화하여 일관된 CLI 동작을 유지한다.
- launch coordination(runner-mode 검증, adapter 가용성 확인, transport 차단, argv 기본값, agent-session env 조립)은 전용 seam 뒤에 둔다.
- machine-readable 상태 형성은 전용 service seam 뒤에 둔다. 상태 payload는 안정적 control-plane 사실을 노출하고 CLI 텍스트 파싱에 의존하지 않는다.
- runtime transport-capability 결정은 전용 provider seam 뒤에 둔다. 현재 pipe-only provider는 PTY 격차를 machine-readable 필드로 명시한다.
- Rust 마이그레이션은 안정적이고 순수한 도메인부터 시작한다. `rust/crates/ai-ide-protocol`은 typed data shape로 제한하고, `rust/crates/ai-ide-core`는 순수 도메인 로직(세션 교체, staged review 상태 전환, 상태 형성)으로 제한한다.
- Python runtime을 cutover 중 reference spec으로 취급한다. 새 Rust 모듈은 집중된 테스트로 parity를 입증한다.
