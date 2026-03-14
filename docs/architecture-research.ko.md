# AI IDE 아키텍처 및 조사 문서

## 1. 권장안 요약

권장 아키텍처는 다음과 같다.

- 데스크톱 셸: Tauri v2
- 코어 런타임: Rust
- UI: React + TypeScript
- 터미널 UI: xterm.js
- 로컬 영속 저장소: SQLite WAL 모드
- 에이전트 통합: adapter process와 정규화된 control protocol
- 실행 모델: host control plane + isolated runner data plane

중요한 결정은 "Rust냐 TypeScript냐"가 아니다. 핵심은 경계다.

- UI는 보안을 강제하는 주체가 아니다.
- 코어 런타임이 신뢰의 기준점이다.
- 외부 AI CLI는 격리된 runner 안에서 실행된다.
- 정책은 에이전트가 워크스페이스를 보기 전에 적용된다.

## 2. 이 구조가 제품에 맞는 이유

이 제품은 전통적인 에디터보다 로컬 오케스트레이터에 가깝다. 따라서 설계 중심도 달라진다.

- 여러 개의 장수명 subprocess
- 정책 민감한 파일시스템 뷰
- PTY 관리
- 감사 저장소
- 크래시 격리
- 결정적인 session rotation

이 요구사항은 신뢰할 수 있는 코어를 작게 유지하고, 모듈 경계를 분명히 하는 구조를 요구한다.

## 3. UI 레이아웃 결정

### 3.1 레이아웃 원칙

이 제품의 UI는 일반 IDE와 비슷한 경험을 제공하되, 레이아웃의 중심이 코드 에디터가 아니라 터미널이다.

- 왼쪽: 파일 트리 (체크박스로 AI 노출 여부 토글)
- 중앙: 멀티 터미널 (주 작업 공간)
- 오른쪽: 코드 편집기 (파일 클릭 시 열림, 보조 역할)

### 3.2 파일 트리와 정책 연동

파일 트리의 체크박스는 워크스페이스 정책과 직결된다. 체크 해제 시 해당 항목은 에이전트의 모든 접근에서 즉시 제외되며, 이는 정책 변경으로 취급되어 활성 AI 세션이 무효화된다.

사용자 자신은 체크 해제 여부와 관계없이 모든 파일을 열람할 수 있다.

### 3.3 터미널 중심 설계 이유

AI 에이전트 CLI가 주 작업 도구이므로 터미널이 화면 중앙을 차지한다. 코드 편집기는 결과 확인이나 수동 수정용 보조 도구로, 오른쪽 패널에서 필요할 때만 열린다.

## 4. 선택지 분석

### 4.1 데스크톱 셸

#### 선택지 A: Tauri + Rust

장점:

- 신뢰해야 하는 백엔드 표면적이 작다.
- Rust 기반 네이티브 코어는 정책, PTY, 저장소, 샌드박스 오케스트레이션에 잘 맞는다.
- Tauri v2는 capabilities 모델과 scoped shell permissions를 제공해 최소 권한 원칙과 잘 맞는다.
- sidecar 지원이 명시적이어서 helper binary 패키징에 유리하다.

단점:

- Electron보다 데스크톱 생태계가 작다.
- 네이티브 패키징과 서명 작업이 더 엄격하다.

판단:

- 이 제품에는 가장 잘 맞는 선택이다.

#### 선택지 B: Electron + Node

장점:

- 생태계가 성숙했다.
- 참고할 데스크톱 예제가 많다.
- 터미널 관련 생태계가 좋다.

단점:

- 신뢰해야 하는 컴퓨팅 기반이 더 크다.
- 데스크톱 셸 자체를 더 강하게 하드닝해야 한다.
- renderer나 preload 경계를 통해 host API가 새어 나가기 쉽다.

판단:

- 가능은 하지만, 로컬 보안이 제품의 중심이라면 1순위는 아니다.

#### 선택지 C: VS Code 확장만으로 구현

장점:

- 초기 출시 속도가 빠르다.
- 익숙한 에디터 표면을 활용할 수 있다.

단점:

- 강한 OS 수준 격리와는 맞지 않는다.
- 제품 정체성이 "에디터 안의 AI"로 남는다.
- 보안의 많은 부분을 호스트 에디터 모델에 의존하게 된다.

판단:

- 나중에 호환성 레이어로는 유용하지만, 코어 제품에는 적합하지 않다.

## 5. 핵심 아키텍처 결정

control-plane과 data-plane을 분리한다.

### 5.1 Control Plane

호스트에서 실행되며 다음을 담당한다.

- project config
- policy engine
- session manager
- runner supervisor
- terminal gateway
- audit store
- UI event distribution

이 부분이 신뢰할 수 있는 코어다.

session manager 안에서는 최소한 다음 단위를 분리해서 다뤄야 한다.

- execution session: policy version, projection, runner, env와 연결된 실행 경계
- agent session: 실제 AI 대화/컨텍스트 경계
- terminal session: PTY와 사용자 상호작용 경계

### 5.2 Data Plane

실제 agent CLI와 도구는 이 격리된 runner 안에서 실행한다.

runner는 다음을 전달받는다.

- policy snapshot
- execution profile
- projected workspace view
- bounded environment

이 부분이 신뢰하지 않는 실행 구역이다.

## 6. 권장 모듈 구성

권장되는 상위 구조는 다음과 같다.

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

각 모듈 책임은 다음과 같다.

- `policy-core`: 경로 규칙, 작업 규칙, policy version, reason code
- `session-core`: session ID, rotation, stale-session invalidation
- `runner-supervisor`: runner 생성/중지, health check, resource limit
- `runner-protocol`: core와 runner 사이의 정규화된 API
- `workspace-projection`: 보이는 워크스페이스를 실제로 구성
- `terminal-gateway`: PTY 생명주기, resize, backpressure, message bus
- `audit-store`: 이벤트, 조회, 보존 정책, export
- `metrics-core`: counter, span, latency histogram
- `agent-adapters`: Claude Code, Codex CLI, Gemini CLI, OpenCode, MCP용 래퍼
- `review-writeback`: diff, 승인, 적용, rollback hook
- `project-config`: `.ai-ide` 스키마와 검증
- `security-tests`: 탈출 시도 fixture와 회귀 테스트 스위트

## 7. Runner 전략

이 시스템에서 가장 어려운 부분이다. 요구사항이 "CLI가 다른 명령을 시도해도 파일 접근을 막아야 한다"라면 broker만으로는 충분하지 않다. 실행 환경 자체가 제약되어야 한다.

### 7.1 권장 실행 모드

#### Mode 1: Broker Mode

- 로컬 개발에는 빠르다.
- 진짜 보안 경계는 아니다.
- 프로토타이핑 용도로만 적합하다.

#### Mode 2: Projected Workspace Mode

- runner는 허용된 파일만 포함하는 projected workspace를 본다.
- 차단된 파일은 관례상 금지가 아니라 실제로 존재하지 않는다.
- host에 대한 직접 write는 비활성화하거나 엄격히 제한한다.

이 모드를 기본값으로 두는 것이 적절하다.

#### Mode 3: Strict Sandbox Mode

- projected workspace에 더해 OS 또는 VM 수준의 격리
- network policy
- process allowlist
- resource limit

이 모드가 프로덕션 보안 모드다.

### 7.2 크로스 플랫폼 권장안

가능하면 일관된 "managed Linux runner" 전략을 쓰는 것이 좋다. 그래야 플랫폼별 동작 차이를 줄일 수 있다.

- Windows host: WSL2 또는 managed dev-container 스타일 runner를 우선 고려하고, AppContainer나 restricted token은 host 측 helper에 한정해 보조적으로 사용
- macOS host: strict mode에서는 managed Linux VM을 우선 고려하고, native app sandbox는 데스크톱 셸과 helper 패키징에 사용하되 임의의 agent CLI에 대한 유일한 경계로 삼지 않음
- Linux host: rootless container를 우선 사용하고, 가능한 환경에서는 Landlock으로 defense in depth를 추가

이유는 다음과 같다.

- 하나의 runner 모델이 테스트하기 쉽다.
- 많은 CLI 도구가 이미 Unix 계열 환경을 기대한다.
- Windows, macOS, Linux의 host-native sandbox는 서로 차이가 너무 크다.
- 에이전트가 host workspace를 직접 보지 못하게 하는 편이 강한 로컬 보장을 만들기 쉽다.

## 8. Workspace Projection

이 모듈이 핵심이다.

### 8.1 설계

host repository를 runner에 직접 넘기지 말고 projection을 만든다.

- 허용된 파일과 디렉터리만 포함
- 차단된 경로는 완전히 제외
- 허용된 내용에 대해서는 상대 경로 구조 유지
- 기본은 read-only projection
- write는 별도로 stage
- 가능하면 projection 자체는 프로젝트 루트 밖의 별도 runtime/cache 영역에 둔다

구현 방식은 다음 선택지를 가질 수 있다.

- 작은 프로젝트에는 copy-on-open cache
- 중대형 프로젝트에는 synced mirror
- 플랫폼이 안전하게 지원하는 경우 overlay 또는 bind mount 스타일 projection

### 8.2 Projection이 중요한 이유

다음 문제를 한 번에 해결해 준다.

- `ls`, `find`, `grep`, `git`, `python`, `node`, 셸 내장 명령이 runner 내부에서 실제로 존재하는 파일만 보게 된다.
- index가 runner 내부에 있으면 stale index가 차단된 파일을 누출하지 않는다.
- 로그와 진단도 보이는 경로만 다루게 된다.
- projection을 프로젝트 바깥에 두면 단순 `..` 상대 경로 탈출 시도로 host workspace에 닿는 위험을 줄일 수 있다.

## 9. Policy Engine

### 9.1 요구사항

- 결정적인 우선순위 규칙
- 명시적인 작업 범위: read, write, search, execute, network, secret
- policy versioning
- denial에 대한 reason code
- 빠른 평가를 위한 compiled representation

### 9.2 권장 규칙 우선순위

1. explicit deny
2. explicit allow
3. inherited project default
4. runtime profile default deny

### 9.3 정책 표현

프로젝트 파일과 로컬 override를 분리한다.

```text
.ai-ide/
  policy.json
  agents.json
  benchmarks.json
```

로컬 사용자 override가 프로젝트 정책을 조용히 약화시키면 안 된다.

## 10. Agent Adapter 모델

외부 도구들은 모두 다르므로, 코어가 그들과 직접 대화하면 안 된다.

각 adapter는 다음을 정규화해야 한다.

- startup command
- environment contract
- working directory
- 필요한 경우 prompt injection guardrail
- 가능한 경우 token과 cost 추출
- streaming output parsing
- structured event

adapter의 책임은 process integration까지다. 보안 정책을 adapter가 소유하게 하면 안 된다.

## 11. IPC와 프로토콜

### 11.1 권장 선택

- UI와 core 사이: Tauri commands/events
- core와 runner/adapter 사이: stdio 또는 local socket 위의 JSON-RPC
- MCP 호환성: 우선 stdio 지원

### 11.2 이유

Model Context Protocol은 JSON-RPC와 stdio 같은 표준 transport를 사용하며, 가능하면 stdio 지원을 권장한다. 로컬 child-process 경계에서는 이것이 gRPC를 전면 도입하는 것보다 단순하다.

원격 runner나 분산 실행이 필요해질 때만 gRPC를 추가하는 것이 적절하다.

## 12. 터미널 아키텍처

권장 분리는 다음과 같다.

- 프론트엔드 렌더링: xterm.js
- 백엔드 PTY 관리: Rust
- Windows PTY: ConPTY
- Unix PTY: native PTY

핵심 요구사항은 다음과 같다.

- 출력 버퍼 상한과 backpressure
- 터미널별 소유권과 권한
- raw PTY stream과 별도로 구조화된 control channel

이 구조화된 채널이 있어야 터미널 간 또는 터미널과 에이전트 간 조율을 셸 텍스트 파싱 없이 처리할 수 있다.

## 13. 영속 저장소와 감사

로컬 감사와 메트릭 저장소로는 SQLite가 가장 현실적이다.

권장 데이터 클래스는 다음과 같다.

- sessions
- policy_versions
- audit_events
- terminal_events
- write_intents
- benchmarks

SQLite를 권장하는 이유는 다음과 같다.

- 임베디드 방식
- 신뢰성 있는 로컬 저장
- 단순한 운영 모델
- WAL 모드는 단일 호스트 환경에서 읽기/쓰기를 더 잘 병행하게 해 준다.

필요할 때만 큰 raw log를 파일로 분리하고, 인덱스는 SQLite에서 관리하는 편이 낫다.

## 14. 보안 모델

### 14.1 신뢰 경계

- renderer/UI: 낮은 신뢰
- desktop shell과 Rust core: 신뢰
- adapter와 agent CLI: 비신뢰
- projected workspace: 통제된 데이터 표면
- host workspace: 보호 대상 자산

### 14.2 필수 통제

- 기본 차단 process launch 정책
- strict mode에서는 host workspace 직접 mount 금지
- network는 기본 차단하고 명시적 allow profile만 허용
- secret은 승인된 정책 채널을 통해서만 주입
- policy version이 다르면 stale-session invalidation
- symlink와 path canonicalization 검사
- host 변경은 writeback approval 경로를 통하게 함

### 14.3 보안 테스트 매트릭스

제품에는 다음 같은 탈출 시도 자동 테스트가 포함되어야 한다.

- `ls`, `find`, `fd`, `grep`, `rg`, `cat`, `type`
- `python`, `node`, `ruby`, 셸 내장 명령
- `git show`, `git ls-files`, `git grep`
- symlink traversal
- 허용 경로 내부로의 archive extraction
- cache, index, temp file을 통한 읽기 시도
- child-process spawn 시도
- localhost 및 외부 network egress 시도

## 15. 유지보수 전략

### 15.1 신뢰 코어를 작게 유지

UI 코드나 agent 전용 코드가 policy evaluation이나 runner supervision으로 스며들지 않게 해야 한다.

### 15.2 순수 모듈 선호

`policy-core`, `session-core`, `project-config`의 상당 부분은 side effect가 거의 없는 라이브러리여야 한다. 그래야 fuzz, unit test, reasoning이 쉬워진다.

### 15.3 OS 어댑터 분리

Windows, macOS, Linux의 샌드박스 상세 구현은 명확한 trait 또는 interface 뒤에 둔다. control plane은 능력을 요청해야지, 플랫폼 API를 곳곳에서 직접 호출하면 안 된다.

## 16. 테스트 전략

### 16.1 Unit Test

- policy matching
- precedence와 reason code
- session rotation 규칙
- config schema parsing
- audit serialization

### 16.2 Integration Test

- projected workspace 생성
- runner boot와 teardown
- adapter 생명주기
- PTY 생명주기
- writeback review flow

### 16.3 보안 회귀 테스트

- 알려진 탈출 벡터 전체를 실행 가능한 fixture로 관리
- 플랫폼별 deny assertion
- 정책 변경 후 stale-session 테스트

### 16.4 Chaos 및 신뢰성 테스트

- write 도중 agent 강제 종료
- terminal host 크래시
- audit append 중 disk full
- projection sync 일부 실패

## 17. 벤치마크 계획

벤치마크는 나중에 붙이는 것이 아니라 설계의 일부여야 한다.

### 17.1 핵심 지표

- cold start time
- warm start time
- 정책 변경 후 session rotation latency
- runner launch latency
- terminal first-output latency
- projected workspace 검색 throughput
- idle terminal 당 메모리 사용량
- active agent session 당 메모리 사용량
- audit ingest throughput
- writeback apply latency

### 17.2 저장소 규모 구간

- small: 5k files
- medium: 50k to 100k files
- large: 250k to 500k files
- monorepo stress: 1M files 또는 그에 준하는 메타데이터 부하

### 17.3 보안 벤치마크

- 새로운 deny rule 적용과 기존 session 무효화까지 걸리는 시간
- blocked-path detection의 false negative 비율
- stale index leakage 점검
- network egress denial 검증

### 17.4 UX 벤치마크

- 정책 토글 후 새 세션이 화면에 반영되기까지 걸리는 시간
- 대량 출력 시 terminal scroll 부드러움
- 큰 패치에 대한 diff review latency

### 17.5 권장 벤치마크 하네스

- 별도 benchmark corpus에 fixture repo 보관
- 재현 가능한 runner profile
- agent별 고정 command set
- p50, p95, p99 기준 보고
- startup, runner launch, policy-apply latency 회귀에 대한 CI gate

### 17.6 기준선 비교

각 기능은 절대 숫자 하나로만 보지 말고, 관련 기준선과 비교해야 한다.

- desktop shell footprint: Tauri shell build와 Electron shell build 비교
- terminal latency: native system terminal과 xterm.js + PTY gateway 비교
- workspace search: host에서 직접 `rg`를 실행한 경우와 projected workspace의 `rg` 비교
- runner startup: host-direct CLI launch와 projected runner, strict sandbox runner 비교
- remote-style isolation: dev-container 스타일 워크플로우와 managed runner 워크플로우 비교

가장 유용한 비교는 대개 "우리 앱 대 다른 브랜드 IDE"가 아니라 "안전하지 않은 직접 실행 경로에 비해 격리와 관측성 때문에 얼마나 오버헤드가 추가되었는가"다.

### 17.7 초기 성능 예산

다음 값은 MVP 시작점으로 두고 실제 측정 결과에 따라 조정한다.

- warm shell start: 중간급 개발자 노트북에서 1.5초 이하
- warm runner launch: projected mode에서 2초 이하
- 정책 토글 후 새 세션 준비: full reprojection이 없으면 500ms 이하, medium repo에서 reprojection이 있더라도 2초 이하
- terminal first output after launch: 150ms 이하
- projected workspace search overhead: medium repo에서 host direct `rg` 대비 15% 이내
- idle memory: active agent run이 없는 shell 기준 300MB 이하
- per active agent session overhead, excluding model process: 150MB 이하

## 18. 권장 구현 단계

### Phase 1

- Tauri shell
- Rust policy engine
- session manager
- SQLite audit store
- xterm.js + PTY gateway
- broker mode prototype

### Phase 2

- projected workspace
- staged writeback
- 최소 두 개 이상의 AI CLI adapter 정규화
- benchmark harness

### Phase 3

- strict sandbox mode
- network policy
- secret policy
- 전체 보안 회귀 테스트 스위트

### Phase 4

- remote runner
- 팀 정책 공유
- 더 풍부한 승인 워크플로우

## 19. 현재 프로토타입과의 차이

이 저장소의 현재 Python 프로토타입은 policy-gated tool call, session rotation, metrics, 간단한 terminal handling에 더해 projected workspace runner mode, strict preview mode, 명시적인 platform adapter 경계까지 보여준다. 하지만 host shell execution은 여전히 정책을 우회할 수 있고, strict preview도 아직 full OS sandbox는 아니다. 단순한 상대 경로 탈출과 obvious host-path 참조, 일부 네트워크 명령은 줄였지만 computed absolute path 접근과 process/network 차원의 완전한 제약은 아직 남아 있다. 이것은 현재 단계에서 예상된 한계이며, 그래서 프로덕션 설계의 중심은 여전히 더 강한 process/network 제약을 가진 isolated runner에 두어야 한다.

현재 프로토타입은 여기에 더해 platform adapter가 strict backend 준비 상태를 probe 하고, projection manifest와 stale projection cleanup도 수행한다. 다만 이것이 stale runner/index 전체 invalidation이나 실제 OS-level sandbox를 대체하지는 않는다.

추가로 strict mode는 backend 준비 상태가 확인되면 해당 backend 실행 경로를 선택하고, 그렇지 않으면 guarded preview로 fallback 한다. 이 구조는 향후 WSL, bwrap, VM helper 같은 실제 backend를 붙일 자리를 미리 고정해 두는 역할을 한다.

## 20. 참고 자료

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
