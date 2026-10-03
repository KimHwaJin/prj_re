# 구조 리뷰 반영 구현 계획

- 기준일: 2026-10-03
- 기준 소스: `353f7a8` (운영 소스는 직전 성능 개선 상태와 동일)
- 근거: [D-01~D-12 합의 및 C-01~C-04 보완](../reviews/2026-10-03-decisions.md)
- 상태: 1단계 [058](../improvements/058-deployment-config-unification.md), 2단계 [059](../improvements/059-run-execution-boundaries.md), 3단계 [060](../improvements/060-unified-agent-command-worker.md)는 구현·로컬 검증했다(베이스 미병합·미배포). 다음은 4단계 깨우기 신호이며 전체 작업 완료 기록은 아니다.
- 우선순위: 실행 구성 정합성 → 공통 실행 구조/처리량 → 기능·성능 검증 → 측정에 근거한 저장/구조 정리. 모델 호출 수·prompt 최적화와 광범위 운영 기능은 기존 보류 유지.

## 목표와 구현 단위

동일 프로세스의 실행 자리를 사용자 요청과 Executor 결과 재개가 함께 사용하고, DB가 명령 상태·소유권·순서를 관리하게 한다. 신호는 대기 중인 Worker를 깨우며 신호 없이도 DB 원장에서 작업을 찾을 수 있게 한다.

한 번의 graph 호출이 다음 HITL/Executor 대기 지점에 도달해 checkpoint·API 상태 반영과 소유권 반환을 마치면 내부 명령은 완료된다. 공개 Run은 계속 유지되고 WAITING_EXECUTOR의 동일 세션 입력 잠금도 유지한다. 일주일짜리 외부 실행 동안 Agent 실행 자리나 체크아웃한 DB 연결을 점유하지 않는다.

아래 단계마다 feature 브랜치와 improvements 작업 기록을 만든다. 번호는 실제 착수 시 다음 가용 번호를 배정한다. 한꺼번에 모든 단계를 착수/완료로 등록하지 않는다. 각 단계의 문제·변경·검증·제한·배포 여부를 기록하고 확인된 변경 단위로 베이스에 통합한다.

| 단계 | 구현 단위 | 근거 | 핵심 완료 조건 |
|---|---|---|---|
| 1 | 배포·설정 정본 | D-01/02 | 단일 컨테이너·app.py 진입점으로 API/Worker 기동과 종료 일치 |
| 2 | Run 책임 분리·공통 GraphInvocation | D-03/04/05 | 접수/실행/취소 분리, 두 경로의 실행·결과 반영 공통화, 현재 동작 보존 |
| 3 | 공통 명령 원장·스케줄러와 입력 전환 | D-06/08/09 | 입력 둘·실행 한 경로, session 내부 명령 순서와 공통 총한도 보장 |
| 4 | 깨우기 신호·대기 조회 비용 정리 | D-07/09 | 신호 유실에도 진행, SSE와 독립된 수명, 불필요 조회 제한 |
| 5 | 기능 대조·동일 총한도 성능 검증 | D-09 | 1/10/30/50명 및 결과 폭주에서 지연·처리량·비용을 전후 비교 |
| 6 | checkpoint 실측·근거 있는 상태/구조 정리 | D-10/11/12 | 실제 저장량/시간을 측정하고 효과 있는 최적화만 선택 |

## 1. 배포·설정 정본 — 058 구현·로컬 검증

[058 기록](../improvements/058-deployment-config-unification.md): 단일 기동·공통 설정을 구현했다. 사내 SDK/PV/CI·배포 검증은 별도다.

### 범위

- app.py → service_bootstrap를 실행 정본으로 맞춘다. 단일 컨테이너·기본 1프로세스다.
- deploy/cicd/Compose의 직접 uvicorn 덮어쓰기, 별도 Worker/sidecar 예시, 중복 실행 명령을 정본과 정합화한다. 플랫폼 고정 필드나 제공 라이브러리 코드는 임의 변경하지 않는다.
- readiness/liveness, port/Service/probe, 종료 유예 시간을 실제 bootstrap 설정과 맞춘다. 플랫폼 5000·로컬 8000이 필요하면 프로필에 명시하고 각 프로필 내부를 일치시킨다.
- checkpoint 별칭 충돌, loadtest APP_ENV, Dockerfile 문법, manifest 오타, 설치 기준과 직접 의존성 선언의 불일치를 수정한다.
- 기존 YAML > env > 기본값 정책을 유지한다. 설정 모델 전체 재작성과 패키지 이동은 이 단계에 넣지 않는다.
- API 내장 이벤트 수신의 활성화 설정을 명시한다. 현재 stg/prd에서 EVENT_WORKER_ENABLED가 false이므로 별도 Worker만 제거하면 결과를 수신하지 못한다. 통합 실행 프로필의 Agent/이벤트/정리 작업 활성값을 함께 검증한다.
- EW_INSTANCE_ID 고정 예제를 제거하고 process/startup UUID가 반영되게 한다. 추가 프로세스/Pod가 실행 자리와 DB 연결 예산을 곱한다는 점을 명시한다.
- 의존성은 pyproject/lock을 기준으로 한다. 폐쇄망에서 uv 사용이 불가능하면 해당 lock에서 생성한 requirements/사내 설치 경로를 사용한다.

### 검증과 완료 산출물

- dev/stg/prd/테스트용 설정의 실효 포트·Worker 활성값·체크포인트 설정·별칭 검증.
- manifest schema 검사와 로컬 이미지 빌드/기동 smoke. 사내 이미지/플랫폼 접근이 없으면 해당 검증은 미확인으로 구분한다.
- API 준비 상태, 실제 내장 Worker 시작, SIGTERM에서 신규 claim 중지·진행 작업 drain·SSE 종료 확인.
- 이번 변경에 영향을 받는 bootstrap/settings/종료 테스트와 필요한 회귀만 실행한다.
- 기동 명령·실효 설정 표·프로세스별 자원 예산·검증 결과를 문서화한다. 문서/로컬 검증을 실제 폐쇄망 배포 완료로 표시하지 않는다.

## 2. Run 책임 분리·공통 실행부 — 059 구현·로컬 검증

[059 기록](../improvements/059-run-execution-boundaries.md)과 [인수인계](../run-execution-architecture.md)를 따른다. 기존 dispatcher 둘은 유지하며, 총 실행 한도 통합은 3단계다.

- Run 접수, 이미 접수된 명령 실행, 취소를 공개 서비스 인터페이스로 나눈다. `_execute_existing`와 외부의 RunService private method 호출을 제거한다.
- 모델 pin 검증, 프로젝트 context, submission_scope, checkpoint receipt와 API 결과 반영을 공통 GraphInvocation 경계에 모은다.
- DB transaction은 접수·claim·결과 반영 각각의 짧은 구간이다. graph/LLM/HTTP 대기에 연결을 유지하지 않는다.
- 우선 두 기존 입력/dispatch 경로를 공통 실행부에 연결해 동작을 보존한다. 공통화 과정에서 공개 Run/API/SSE/승인·재개 의미가 바뀌지 않게 한다.
- 검증: 새 요청·HITL 편집/승인·Executor 이벤트·decision/repair HITL·보고서·중복 재개·모델 pin·결과 저장 재시도. checkpoint 재개와 SQL/연결 수명 대조를 포함한다.

## 3. 공통 명령 원장·스케줄러 — 060 구현·격리 검증

[060 구현](../improvements/060-unified-agent-command-worker.md), [명령 원장·Worker·이행 안내](../agent-command-worker.md). 아래는 인수 기준이며 현재 runtime은 신규 agent_commands를 정본으로 사용한다. 이전 Redis 실행 모듈 파일 삭제는 자동 승인 거절로 보류했다.

### 설계 기준

- 공개 Run과 내부 명령의 식별·상태·순서·재시도 의미를 분리한다. 기존 ew_commands 확장/재사용 또는 내부 원장 신규 테이블 중 더 작은 정합성 경계로 결정한다.
- 명령 종류는 새 요청, 사용자 resume, Executor event resume다. 취소/운영 복구를 일반 FIFO 뒤에 넣어 진행을 막지 않는다.
- 같은 session의 앞선 미종료 내부 명령을 추월하지 않는다. HITL/Executor 대기에 도달한 이전 호출은 내부 명령 완료이며 공개 Run 완료를 기다리지 않는다.
- API·Inbox·내부 원장을 같은 DB에 두는 정본을 설계한다. 같은 connection/transaction으로 사용자 접수+명령, 이벤트 처리+명령을 기록한다.
- 실제 분리 DB의 권한·진행 중 명령·이관 필요성을 먼저 확인한다. checkpoint/Store와 Executor 자체 DB 이전까지 범위를 확대하지 않는다.

### 구현

- 사용자 접수와 Executor 이벤트를 내부 명령으로 기록한다. 이벤트 수신부는 중복/sequence/binding 검증과 영속화에 집중한다.
- 공통 Worker가 총 실행 한도 안에서 slot이 있을 때만 claim한다. 사용자 요청과 결과 처리 어느 한쪽이 계속 밀리지 않도록 배분한다. 정확한 가중치·예약 비율은 측정 없이 고정하지 않는다.
- eligibility에 session 순서·checkpoint 준비·기존 소유권·재시도 시각을 반영한다.
- 내부 명령 완료/무시/거절과 공개 Run 상태를 분리하고, receipt·Executor idempotency를 유지한다.
- 전환 후 중복 graph dispatch와 필요 없어진 Redis 실행 lease/장기 PEL 처리를 제거한다. 외부 Executor 이벤트 수신의 전달 보장은 유지한다.

### 전환·검증

- migration은 현재 Run/checkpoint를 식별하고 이어갈 수 있도록 설계한다. 기존 대기 Run마다 임의의 새 실행을 만들지 않는다.
- 구 실행기와 신 실행기가 같은 명령을 중복 소비하지 않도록 전환 절차를 정한다. 롤링 배포 중 혼재를 지원한다고 미리 가정하지 않는다. 혼재 검증 또는 구 실행기 drain/종료 확인이 필요하다.
- claim/소유권/명령 순서, 재예약 선행 명령, 중복 이벤트, 처리 완료 후 재전달, 빠른 Executor 결과, 프로세스 종료를 격리 DB·Redis로 확인한다.
- claim 직후 crash에서 기존 writer의 안전한 종료 확인 없이 자동 탈취하지 않는다. 현재 recovery_required 보호를 유지하며 광범위 관리자 복구 API 개발은 별도다.

## 4. 깨우기 신호

- 같은 DB를 전제로 LISTEN/NOTIFY를 우선 구현 후보로 검토한다. Worker lifespan이 수명을 소유하며 SSE 구독자 유무에 영향받지 않는다.
- 시작/재연결/명령 완료/재예약 기한/제한된 주기 scan을 유지해 알림 없이도 진행한다. 알림은 여러 개를 합치고 slot이 없을 때 불필요한 claim을 하지 않는다.
- 여러 프로세스의 fan-out, 빈 조회, 재연결 시 놓친 명령을 계측한다. 기존 전용 LISTEN 연결과 공유할 수 있는지는 수명 설계 후 결정한다.
- Redis 신호를 선택하면 내부 신호 ACK가 graph 수명에 묶이지 않게 한다. 실행할 명령이 없거나 다른 Pod가 claim한 신호를 영구 pending으로 남기지 않는다.
- 동기화 구조가 먼저 완성돼야 하므로 3단계의 제한된 polling 기반 정확성을 확보한 뒤 신호 최적화를 붙일 수 있다. Redis/NOTIFY 비교는 측정상 선택 필요성이 있을 때 수행한다.

## 5. 기능·성능 검증

### 비교 조건

- 변경 직전 기준 commit과 새 구현을 같은 데이터·DB pool·프로세스 수·자원 제한으로 재측정한다.
- 총 실행 한도를 맞춘다. 예를 들어 기존 API32+Event4와 새 공통36을 비교한다. 공통32를 평가하면 대조군도 합계32로 새로 측정한다. 한도36→32 변화와 구조 효과를 섞지 않는다.
- 1/10/30/50명. 주 비교인 50명은 각 조건 최소 3회 측정하고 평균·분산을 남긴다. 100명은 현재 범위에 넣지 않는다.
- 모델은 모든 호출 역할의 응답을 5초 fixture로 고정한다. 최초 계획뿐 아니라 review/repair/report에도 적용하며 역할별 실제 호출 수를 기록한다. 모델 호출 수·prompt 변경은 하지 않는다.
- 단독 새 요청, Executor 결과 집중, 양쪽 혼합을 비교한다. 후속 설명/보고서와 project_memory manual/auto_context의 서비스 비용도 별도 시나리오로 포함한다.
- Executor 부하용 fixture는 실제 HTTP/Redis/manifest 경로를 제공하되 제출 Python을 실행하지 않는다. 실제 로컬 Executor는 소수 기능 대조로 구분한다.

### 지표와 판정

- 전체 완료 시간 평균/p95, 완료 처리량, 명령 종류별 queue 대기/실행 시간.
- 동시 실행 수와 slot 사용률, SQL 호출·DB 대기·checked-out 연결, CPU·RSS·event loop 지연.
- 불필요 wakeup/빈 claim, retry/defer 수, 중복 실행·누락·순서 위반 여부.
- HITL/Executor 대기 중 실행 자리와 DB 연결 반환, 다른 session의 독립 진행.
- 균형 부하에서 통합만으로 큰 개선을 보장하지 않는다. 한쪽 대기열에 일이 몰릴 때 빈 자리 공유 효과와 비용을 주로 확인한다.
- 총한도 확대 효과, 알림 지연 단축, 코드/SQL 비용 감소를 따로 보고한다. 유한 burst 결과를 지속 유입 한계나 실제 Kubernetes HPA 성능으로 환산하지 않는다.

성공 조건은 의미 보존·중복/누락 방지·총한도 준수와 함께, 주 시나리오의 처리량/지연 및 자원 비용을 실측으로 설명할 수 있는 것이다. 회귀가 있으면 원인과 적용 조건을 확인한 뒤 배포 후보를 결정한다.

## 6. 후속 저장량·구조 정리

- F-01 대표 시나리오에서 checkpoint_blobs/checkpoints/checkpoint_writes를 thread/channel/실행 구간별로 측정한다. 상태 reset과 과거 버전 누적을 구분한다.
- 저장 시간 비중이 큰 channel부터 범위 제한·근거 외부화·지원되는 delta 방식·보존 정책을 검토한다. reducer 전환만으로 용량이 줄어든다고 가정하지 않는다.
- 상태 타입/이름, 설정 중복 기본값, Agent 조립 의존 방향, 실제 미사용 compiler/stub, 테스트 fixture 결합을 필요한 변경 단위로 정리한다.
- analysis/workflow 자산·역할별 agent.py/prompt·공개 계약·Dataset Registry draft는 보존한다. 원문 hash 참조는 불변 저장소 계약 확보 후에만 적용한다.
- 코드 정리 자체의 효과를 성능 향상으로 보고하지 않는다. 기존 benchmark 원본 이관과 광범위 운영 보완은 별도 후속이다.

## 단계별 보고 형식

매 작업이 끝날 때 `기존 문제 → 실제 변경 → 사용자/실행 동작 변화 → 검증 결과 → 남은 제한 → 다음 작업`을 보고한다. 성능 숫자는 동일 조건의 측정 근거가 있을 때만 제시한다. 문서 작성·구현·검증·베이스 통합·실제 배포의 상태를 각각 구분한다.
