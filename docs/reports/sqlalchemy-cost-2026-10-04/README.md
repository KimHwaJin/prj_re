# 074 SQLAlchemy 비용 분해 — 2026-10-04

잔여 비용에서 먼저 검토할 후보는 **공통 조회문을 매번 만드는 비용**이다. ORM 행 생성만을 큰 병목으로 지목할 근거는 없다. 동시에 DB 실행·결과 처리와 transaction 관리 비용도 분산되어 있어, 한 군데 수정으로 전체 지연이 크게 줄어든다고 판단하지 않는다.

이번 단계는 **진단 완료**다. 서비스/Agent 소스·API·설정·실행 한도는 변경하지 않았다. profiler를 명시적으로 켠 benchmark에서만 함수 caller 메타데이터를 추가했다. 073 runtime을 유지하고 보류된066/067/071/072 구현을 되가져오지 않았다. 브랜치 `feature/sqlalchemy-cost-diagnosis`, 측정 소스 `522e52a`, 기준 `f1ca885`. 베이스 미병합·원격 미푸시·미배포다.

## 질문과 방법

SQL 준비와 ORM 결과 변환 중 무엇을 먼저 검토할지, 어떤 실제 호출 경로에서 반복하는지를 확인했다. `time.thread_time` 기반 cProfile의 **exclusive self CPU**를 파일 단위로 서로 겹치지 않게 분류했다. nested cumulative CPU나 concurrent SQL wall 시간을 더하여 총 지연으로 표현하지 않았다.

실제 API/CRUD PostgreSQL/checkpoint PostgreSQL/Redis/공통 Worker/HITL/SSE를 실행했다. 모델은 고정 응답·0초, Executor는 localhost HTTP/Streams/manifest 합성 fixture이며 제출 Python을 실행하지 않았다. 한도20, CRUD pool10/checkpoint4/event4, overflow0, SSE0.5초, 취소 확인0.25초, notify on을 유지했다. 사내 서비스나 실제 Executor에 부하를 제출하지 않았다.

표준은4 Tool/2 Operation/4 모델 호출/사용자 입력3회다. 큰 결과는20 Tool/20 Operation/23 모델 호출/64KiB 합성 출력이다. 큰 결과 시나리오는 Operation 수·모델 호출 수·출력 크기가 함께 달라지므로 JSON 크기 하나의 효과를 분리한 시험이 아니다.

3회 CPU 진단(표준1명·표준10명·큰 결과1명)과 profiler off 표준10명 대조1회, **4시도·22흐름 전부 완료·오류0**다. 50명 부하시험과 전후 성능 비교는 하지 않았다. 소스/계산/결과 검산은 [verify.py](verify.py), 원본은 [attempts.json](attempts.json)과 `raw/`를 따른다.

## 10명 표준의 CPU 분해

메인 thread CPU는 6.783초, 그 안에서 SQLAlchemy 함수에 직접 계상된 self CPU는 2.417초다. 아래 비율의 분모는 **메인 thread CPU 6.783초**이며 SQLAlchemy만의 비율이나 wall 시간 비율이 아니다.

| 분류 | exclusive self CPU | 메인 thread CPU 대비 |
|---|---:|---:|
| SQL 객체 구성·캐시 키 등 sql 모듈 | 0.734초 | 10.8% |
| DB 실행·결과·pool·async bridge 등 | 0.888초 | 13.1% |
| ORM 행 로딩 모듈 | 0.104초 | 1.5% |
| ORM session·context·transaction 모듈 | 0.258초 | 3.8% |
| SQL compiler 모듈 | 0.078초 | 1.2% |
| 그 밖의 ORM 상태·flush 등 | 0.343초 | 5.0% |
| 그 밖의 SQLAlchemy | 0.012초 | 0.2% |

SQL 객체 구성/캐시 모듈과 compiler 모듈 합은 0.812초다. `orm/loading.py`는 0.104초이며 표준1명과 큰 결과1명에서도 앞의 두 모듈 합보다 작았다. 이에 따라 조회문 구조 재사용을 먼저 검토하는 방향을 권장한다.

이 분류는 **함수가 정의된 모듈의 self CPU**다. `orm/loading.py` 수치를 JSON 디코딩·driver/C extension·ORM state 준비까지 포함한 전체 읽기 비용이라고 해석하면 안 된다. engine/result와 JSON/builtin 비용이 다른 분류에 있으며, write에도 ORM state/flush 비용이 쓰인다. SQLAlchemy 밖의 shared builtin CPU를 임의로 SQL 준비나 ORM 읽기에 배분하지 않았다. 모듈 비용의 비율을 예상 성능 개선율이나 제거 가능한 비용의 상한으로 환산하지 않는다.

[measurements.json](measurements.json)에 세 진단의 분류·함수·caller와 한 번의 off 대조를 보존했다. offload thread는 별도 시계로 측정하며 process CPU와 메인/측정 offload 합은 범위·경계·미측정 thread 때문에 정확히 같지 않다.

## 반복되는 실제 경로

표준10명에서 목적별로 실제 실행된 SELECT:

| 경로 | 전체 SELECT | 사용자 흐름당 | 의미 |
|---|---:|---:|---|
| Log와 TaskEvent 연결 확인 | 410 | 41 | 이미 저장된 쌍을 재사용하고 미완성 연결을 복구 |
| Graph Task와 CRUD Task 연결 확인 | 230 | 23 | 상태 투영 때 graph_task_id 연결을 확인 |

`graph_crud_persistence._persist_state` → `_link_graph_task` → `TaskService.attach_graph_task_for_run`, `plan_event_persistence.persist_plan_events` → `AgentRunLogService.create` 경로를 소스로 대조했다. 큰 결과1명은 각각206회와114회로 늘었다. 이것은 경로별 조회 횟수이며, 같은 parameter·transaction의 불필요 중복을 입증한 수치가 아니다.

동기 `select()` builder의 직접 caller에는 `AgentRunLogService.create`417회, `UserRepository.get_active`270회, `TaskService.attach_graph_task_for_run`233회, `resource_lifecycle.lock_session`140회 등이 기록됐다. 함수 안 여러 SELECT 분기 및 계측 경계를 포함하므로 builder 횟수와 위 특정 SELECT 실행 횟수를 같은 지표로 맞추지 않는다. async 함수의 profiler 재진입 횟수도 논리 사용자 요청 수로 해석하지 않는다. caller inclusive CPU는 경로 확인에만 사용하고 서로 더하지 않는다.

같은 측정에서 `_compile_w_cache`5486회, cache key root 생성4729회, SQLCompiler 생성200회, `visit_insert`200회, `visit_select` 기록 없음이다. 두 `__init__` frame은 같은 compiler 생성 경로이므로200+200=400개로 세지 않는다. 반복 SELECT 전체가 매번 SQL 문자열로 재컴파일된다는 주장은 맞지 않는다. 기존 compiled/prepared cache가 작동하는 상태에서도, 새 expression을 만들고 cache key를 탐색하는 Python 비용은 남는다. 이미 적용한069 claim/070 공개 상태 조회문 재사용과 같은 방향의 후보가 남아 있는 것이다.

실측에 쓰인 SQLAlchemy2.0.52의 `_compile_w_cache`, ORM `instances`, Python pstats caller tuple 의미는 [설치 소스 발췌](installed-source-excerpts.txt)에 남겼다. [source-audit.json](source-audit.json)으로 서비스 소스 불변을 확인했다.

## 선택한 다음 후보와 보존할 것

다음 후보는 Log/Event 연결 조회와 공통 User/Session 조회의 **불변 SQL 구조 재사용을 한 묶음으로 검토**하는 것이다. 매번 run_id/event_key/user_id/session_id를 새로 바인딩하고 실제 DB SELECT를 수행한다. 결과·권한·ORM 객체·DB session을 프로세스 메모리에 캐시하는 변경은 아니다.

- 권한과 삭제 상태의 최신 재확인, User/Project/Session 잠금 순서와 `populate_existing`을 유지한다.
- Log/Event dedup·미완성 연결 복구·첫 payload 보존·commit 경계를 유지한다.
- Task 연결 확인 자체를 무조건 생략하거나 replay를 없애지 않는다.
- 071 batch 후보·072 CTE 후보를 다시 채택하지 않는다. 쿼리 수 감소만으로 시간 개선을 판정하지 않는다.
- candidate가 생기면 profiler off의 동일 조건 전후 시험으로 실제 시간·CPU를 비교하고, 효과가 없으면 보류한다. 이번 자료로 예상 개선율을 약속하지 않는다.

큰 Run JSON의 불필요 필드 읽기를 줄이는 변경은 아직 선택하지 않았다. 특정 컬럼의 bytes·driver 처리·실제 소비 필드를 대조하지 않았으므로 이번 자료로 큰 JSON이 주원인이라는 결론은 내릴 수 없다. 모델 호출 수·Registry·Workflow CRUD·광범위 운영 신규 작업의 후순위도 유지한다.

## 검증과 실제 시간의 구분

메타데이터만 수집함을 확인하는 diagnostic 테스트3개가 통과했다. recursive caller count·primitive count, offload의 별도 CPU 시계, 측정 밖 비수집과 인자값 비수집을 검증했다. [regression.log](regression.log). 서비스 소스가 바뀌지 않았으므로073의158회귀를 다시 실행했다고 주장하지 않는다.

4흐름군의 CRUD201/201→Run/HITL202/202/202, invocation 상태2 interrupted+1 success/사용자, attempt1, Operation/관찰/리포트 근거, command/outbox/inbox/DB 연결과 session owner 반환을 독립 검산했다. 원본 hash·source hash·CPU 분류의 합·분모·caller 메타데이터도 확인했다. 검산 check 수에는 수만 개의 개별 메타데이터 검사도 포함되므로 그 수를 회귀 테스트 수로 표현하지 않는다.

profiler on 표준10명 완료7.147초, off 대조4.038초다. profiler 자체의 비용이 크다는 것을 보여주며, 하나의 off 대조만으로 안정적인 처리량이나 개선율을 주장하지 않는다. 메인/오프로드 CPU·export CPU 및 API process 시계는 원본에 별도 보존했다.

임시 PostgreSQL/Redis와 부속 volume은 제거했다. 기존18개 컨테이너가 계속 실행 중임을 확인했다. 원래 checkout·.env·서비스 설정은 변경하지 않았다. [cleanup.json](cleanup.json), [environment.json](environment.json).

## 재현

원본 검산은 DB 없이 가능하다:

```sh
/Users/a10054/SKAX_PROJECT/dtest-agent/.venv/bin/python docs/reports/sqlalchemy-cost-2026-10-04/verify.py
```

재측정은522e52a 소스에서 localhost63372의 폐기 가능한 PostgreSQL, localhost63373/0의 전용 Redis를 새로 준비한 뒤 다음 형태를 사용한다. 기존 데이터베이스/공유 Streams에 실행하지 않는다. `run.py`는 각 attempt용 fresh CRUD/checkpoint DB를 만들고 종료 후 제거하며 private 설정을 결과 증거에 복사하지 않는다.

```sh
python scripts/benchmarks/worker_e2e/run.py \
  --database-url postgresql+asyncpg://postgres:sql-cost-test-only@127.0.0.1:63372/postgres \
  --redis-url redis://127.0.0.1:63373/0 \
  --source-commit 522e52a --output /private/tmp/sql-cost-new-attempt \
  --users 10 --concurrency 20 --delay-ms 0 --cpu-profile
```

표준1명은 `--users 1`, 큰 결과는 `--users 1 --observation-profile large20`, off 대조는 `--cpu-profile`을 생략한다. 한 output 디렉토리를 재사용하지 않는다. 이번 raw는 실제 모델·Executor 계산·Pod 제한·여러 replica·지속 유입·HPA 검증을 대신하지 않는다.
