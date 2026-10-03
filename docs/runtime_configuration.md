# Executor 실행·결과 설정 가이드

## 1. 목적

이 문서는 Agent가 Executor에 Notebook 셀과 리포트를 전달하는 방식, 실행 결과를
읽는 방식, Redis와 PostgreSQL 연결 설정을 설명한다. 환경변수를 변경한 뒤에는
공통 설정을 읽는 `app.py` 서비스를 재시작해야 한다. 운영 재기동·DB 이행은 별도 절차다.

## 2. 권장 기본 설정

로컬 개발에서는 기존 API 결과 조회 방식을 기본으로 사용한다.

```env
EXECUTOR_SOURCE_TYPE=INLINE
EXECUTOR_REPORT_SOURCE_TYPE=INLINE
EXECUTOR_RESULT_READ_MODE=API
EXECUTOR_SHARED_INPUT_ROOT=/workspace/pv
EXECUTOR_SHARED_RESULT_ROOT=/workspace/pv
```

Agent와 Executor가 동일한 PV를 마운트한 배포 환경에서는 다음 조합을 사용할 수
있다.

```env
EXECUTOR_SOURCE_TYPE=PATH
EXECUTOR_REPORT_SOURCE_TYPE=PATH
EXECUTOR_RESULT_READ_MODE=MANIFEST
EXECUTOR_SHARED_INPUT_ROOT=/workspace/pv
EXECUTOR_SHARED_RESULT_ROOT=/workspace/pv
```

## 3. Notebook 셀 전달 방식

`EXECUTOR_SOURCE_TYPE`은 Executor에서 실행할 Python 셀 코드의 전달 방식을 정한다.

| 값 | 동작 | 요구사항 |
|---|---|---|
| `INLINE` | 실행 요청 JSON에 Python 코드를 직접 넣는다. | 공유 PV가 없어도 된다. |
| `PATH` | 공유 PV에 `.py` 파일을 저장하고 상대경로와 SHA-256을 보낸다. | Agent와 Executor가 같은 PV를 봐야 한다. |

`PATH`에서 파일은 `EXECUTOR_SHARED_INPUT_ROOT` 아래에 적재된다. API 요청에는 절대
경로가 아니라 공유 루트 기준 상대경로가 들어간다.

## 4. 최종 리포트 전달 방식

`EXECUTOR_REPORT_SOURCE_TYPE`은 생성된 Markdown 리포트를 artifact API에 전달하는
방식을 정한다.

| 값 | 동작 |
|---|---|
| `INLINE` | Markdown 본문을 artifact POST body에 직접 넣는다. |
| `PATH` | 공유 PV에 `final-report.md`를 적재하고 상대경로와 SHA-256을 보낸다. |

두 방식 모두 최종적으로 다음 API를 호출한다.

```http
POST /api/v1/executions/{execution_id}/artifacts
```

요청 artifact type은 `REPORT`, 이름은 `final-report.md`다.
`EXECUTOR_REPORT_APPEND_TO_NOTEBOOK=true`이면 Executor가 리포트를 실행 Notebook에도
추가한다. 최종 artifact 저장 위치는 Executor가 관리하며 일반적으로 해당 실행의
`artifacts/reports/` 아래다.

## 5. 실행 결과 읽기 방식

`EXECUTOR_RESULT_READ_MODE`은 Operation 완료 후 함수 실행 결과를 수집하는 방식을
정한다. 허용값은 `API`, `MANIFEST`다.

### 5.1 API

기존 구현이며 기본값이다.

```text
GET /api/v1/executions/{execution_id}/result
GET /api/v1/executions/{execution_id}/notebook?view=FULL
→ Step 상태와 Notebook output 결합
```

```env
EXECUTOR_RESULT_READ_MODE=API
```

### 5.2 MANIFEST

`execution.operation_completed` 이벤트의
`payload.step_results[].result_ref.relative_path`를 이용한다.

```text
operation_completed 이벤트
→ result_ref가 가리키는 manifest.json
→ manifest의 outputs[].representations[]
→ 실제 텍스트·JSON output 파일
→ 기존 executor_result_history 형식으로 정규화
```

```env
EXECUTOR_RESULT_READ_MODE=MANIFEST
EXECUTOR_SHARED_RESULT_ROOT=/workspace/pv
```

`execution.completed` 이벤트는 Step별 `result_ref`를 제공하지 않는다. 이 이벤트는
최종 실행 완료 및 리포트 생성 시작 신호로 사용하고, Step 결과는 앞서 처리한
`execution.operation_completed`에서 수집한다.

Manifest Reader는 다음을 검증한다.

- 공유 루트 이탈, 절대경로 및 `..` 차단
- manifest와 output 파일의 크기 및 SHA-256
- Manifest schema version `1.0`
- execution ID, Step ID, sequence, attempt ID
- 제공된 경우 fencing token
- UTF-8 텍스트와 JSON 본문 로드
- 이미지 등 바이너리는 본문 대신 파일 참조 정보 유지

Manifest 원문이 크더라도 Report LLM에는 압축된 결과를 전달한다. 이 압축은 API와
MANIFEST 모드 모두에 적용된다.

## 6. 공유 PV 설정

| 환경변수 | 역할 |
|---|---|
| `EXECUTOR_SHARED_INPUT_ROOT` | Agent가 셀 코드와 PATH 방식 리포트 원본을 적재하는 루트 |
| `EXECUTOR_SHARED_RESULT_ROOT` | Agent가 Executor의 Step manifest와 output을 읽는 루트 |

두 값은 보통 모두 `/workspace/pv`지만 역할이 다르므로 별도 설정으로 관리한다.
컨테이너 내부의 Agent와 Executor에서 동일한 파일이 동일한 의미의 경로로 보여야
한다.

## 7. 기타 Executor 설정

| 환경변수 | 설명 |
|---|---|
| `EXECUTOR_BASE_URL` | Executor API 기본 URL |
| `EXECUTOR_*_PATH` | 실행 생성, Operation, 결과, Notebook, finalize, cancel, artifact endpoint |
| `EXECUTOR_RUNTIME_PROFILE` | Executor Runtime profile |
| `EXECUTOR_TLS_VERIFY` | TLS 인증서 검증 여부. 운영에서는 `true` 권장 |
| `EXECUTOR_TIMEOUT_SECONDS` | HTTP 요청 timeout |
| `EXECUTOR_OPERATION_TIMEOUT_SECONDS` | Operation 실행 timeout |
| `EXECUTOR_OPERATION_WAIT_TIMEOUT_SECONDS` | 다음 Operation 대기 timeout |
| `EXECUTOR_SUBMIT_ENABLED` | 실제 Executor 요청 전송 여부 |
| `DATA_MOCK` | `true`이면 데이터 선택은 유지하되 wide mock parquet을 직접 로드 |

## 8. Redis 설정

현재 Redis는 Executor 원본 이벤트 수신과 SSO 로그인 세션에 사용한다. 내부 graph 명령은 PostgreSQL agent_commands에 기록한다.

| 환경변수 | 설명 |
|---|---|
| `REDIS_URL` | 공통 Redis 주소. 로그인·Streams는 용도별 pool/key/group 사용 |
| `EW_REDIS_URL` | REDIS_URL의 구 별칭, 동일값만 허용 |
| `EW_EXECUTOR_EVENT_STREAM` | Executor 원본 이벤트 Stream, 기본 executor.events |
| `EW_EVENT_GROUP_NAME` | 원본 이벤트 ingress consumer group |
| `EW_NAMESPACE` | 이벤트 DB·consumer group 기본 이름의 서비스 구분자 |
| `EW_INSTANCE_ID` | 기동 UUID가 붙는 consumer 이름 prefix |
| `EW_INGRESS_CONCURRENCY` | 원본 이벤트 수신·routing 한도 |
| `AGENT_WORKER_CONCURRENCY` | 사용자·승인·Executor 결과의 공통 graph 총한도 |

```text
Executor Outbox → executor.events
→ ingress → PostgreSQL Inbox commit → 원본 메시지 ACK
→ sequence/binding routing + agent_commands 원자 기록
→ 공통 Agent Worker → LangGraph 재개 → 결과/명령 상태 기록
```

원본 Redis 전달은 at-least-once이며 event ID·DB command ID·checkpoint receipt·Executor idempotency로 중복을 제어한다. 내부 command stream/group, 별도 dispatch 동시성, publish lease 설정은 삭제했다. 해당 EW_* 설정이 YAML/env에 남으면 오류로 중단하므로 제거해야 한다.

## 9. PostgreSQL 설정 요약

| 환경변수 | 사용 주체 | 역할 |
|---|---|---|
| `DATABASE_URL` | API·공통 Agent Worker | CRUD·내부 명령 원장·세션 실행 소유권·project_memory Store |
| `EW_DATABASE_URL` | 이벤트 입력·제출 bridge | DATABASE_URL에서 psycopg 표기로 파생하는 같은 DB의 Inbox/binding. 실행 활성 시 다른 정본 거절 |
| `WORKFLOW_DATABASE_URL` | Workflow 저장소 | 별도 지정 가능, 해당 schema 준비 필요 |
| `CHECKPOINT_DB_URI` | 공용 graph runtime | LangGraph checkpoint DB·pool. API DB와 분리 가능 |
| `AGENT_CHECKPOINT_DATABASE_URL` | 구 별칭 | CHECKPOINT_DB_URI와 같은 값만 허용 |

모든 입력의 실제 graph 호출은 같은 runtime/checkpoint 정본을 사용한다. API·Inbox·명령은 같은 database에 기록하되 라이브러리별 pool은 별도다. 데이터·설정 이행과 종료/기동은 [공통 Worker 안내](agent-command-worker.md)를 따른다.

DB 생성과 마이그레이션은
[database_migrations.md](./database_migrations.md)를 참고한다.

