# Frodo의 양쪽 DB 체크포인트 공존 검증

기준: 2026-09-23 17:03 KST 읽기 전용 조회, `feature/load_test_v1` / `dad1d6c` 작업 트리.

## 결론

체크포인트 테이블이 `chat_app`과 `agent`에 모두 존재한다는 사실 자체는 오류 원인이 아니다. 그러나 이번 Frodo 표본에서는 **동일 세션의 정상 상태가 한 DB에 있고, 다른 DB에는 `user_request` 없이 처음부터 실행하다 실패한 재개 기록이 존재**했다. 로컬에서는 저장소를 실제로 바꿔 재개할 때 같은 오류를 양방향으로 재현했다.

따라서 이 표본은 단순히 사용하지 않는 과거 테이블이 남아 있는 상황으로 설명되지 않는다. 체크포인트 저장소 불일치가 오류를 만드는 메커니즘은 검증됐다. 다만 어떤 프로세스·실행 버전·설정 경로가 각 DB를 선택했는지는 아직 확정하지 못했다. 예전 설정의 Worker가 있다는 전제는 사용하지 않는다.

## Frodo 읽기 전용 증거

모든 연결에 `default_transaction_read_only=on`을 적용하고 읽기 전용 transaction에서 조회했다. 원문 질문·모델 출력·접속 비밀번호를 보고서에 저장하지 않았다.

| 항목 | chat_app | agent |
|---|---:|---:|
| checkpoint 행 수 | 1,581 | 4,187 |
| thread 수 | 238 | 311 |
| 조회 시 최신 checkpoint 시각(KST) | 17:03:28 | 17:03:26 |

최근 조회 직전까지 양쪽에 생성된 기록이 있었다. 이것만으로 앞으로도 계속 쓰이고 있다고 단정하거나, 현재 접속한 SQL 편집기를 기록 작성자로 지목하지 않는다.

조회 시 `agent_runs.failure.message = 'user_request is required'`인 행은 121개였으며 모두 command 기반 재개 요청이었다(input JSON null, command object 84개/string 37개). 이 숫자는 재시도 중 오류가 정리된 Run 등을 포함하지 않으므로 전체 장애 건수나 오류율이 아니다.

최신 오류 Run 12개는 서로 다른 세션이었다. 각 세션에 대해 DB별 최신 checkpoint 최대 8개와 해당 pending writes를 조회했다.

| 표본의 정상 상태 위치 | 동일 세션의 오류 상태 위치 | 세션 수 |
|---|---|---:|
| chat_app | agent | 11 |
| agent | chat_app | 1 |

여기서 정상 상태는 사용자 요청이 존재하고 HITL 등 진행 단계가 저장된 상태를 뜻한다. 전체 작업 완료를 뜻하지 않는다. 오류 상태는 step 0, `branch:to:receive_request`만 있는 상태, `__resume__` 및 해당 메시지를 포함한 `__error__` write로 확인했다. PostgreSQL JSON과 원시 error bytes를 확인했으며 외부 직렬화 객체를 실행하거나 역직렬화하지 않았다.

### 대표 사례: chat_app에서 시작하고 agent에 재개 오류가 남음

세션 `cf82f3a4-8752-4253-9913-8822cc0cd971`:

| 시각(KST) | 관측 |
|---|---|
| 16:45:00.832 | 최초 Run `2a2c3d91-81cc-4771-a7b8-161b26ab96c3` 생성 |
| 16:45:01.471 | chat_app step 0에 user_request 존재 |
| 16:45:02.126 | chat_app step 4, 데이터 선택 HITL, user_request 길이 4 |
| 16:45:04.106 | 재개 Run `bdcd2dd9-632d-44ac-af8d-ba55adb0f855` 생성. resume_run_id는 위 최초 Run |
| 16:45:05.009 | agent step 0, user_request 없음. resume 및 `user_request is required` 오류 write 존재 |
| 16:45:13.821 | 재개 Run 최종 error, attempt_count=4 |

checkpoint의 timestamp는 checkpoint 생성 시각이며 error write 자체의 별도 생성 시각은 아니다. 같은 session/thread, 올바른 resume_run_id, 요청 순서 및 오류 메시지가 일치하지만 checkpoint metadata에는 실행 프로세스나 API run_id가 없어 각 attempt와 작성자를 완전히 연결하지 못했다.

반대 방향 사례도 확인했다. 세션 `f025041e-e6aa-4745-843b-1ac471cdd29c`는 16:12:15에 agent에서 데이터 선택 대기 상태가 있었고, 16:12:18에 chat_app의 빈 step 0에서 같은 오류가 기록됐다. 따라서 특정 DB 이름 자체가 잘못된 것이 아니다.

## 로컬 통제 재현

기존 로컬 PostgreSQL에 고유 이름의 임시 DB 두 개를 만들어 테스트했다. 기존 chat_app·agent 데이터는 건드리지 않았으며 임시 DB는 finally에서 제거했다.

실제 `ainvoke_user_turn()`·`ainvoke_resume()`·Graph·PostgreSQL saver를 사용했다. 모델 응답, CRUD 메시지 기록, Worker bridge 및 Workflow 외부 저장은 테스트 대역으로 대체했다. HTTP API·부하·실제 모델·Executor 검증은 이번 테스트 범위가 아니다.

| 조건 | 결과 |
|---|---|
| 양쪽에 checkpoint 테이블과 데이터가 존재, A에서 최초 실행·재개 | 성공 |
| 처음부터 끝까지 B에서 실행·재개 | 성공 |
| A 최초 실행 → B 재개 | 정확히 같은 ValueError 재현 |
| 위 실패 후 A로 돌아가 재개 | 성공 |
| B 최초 실행 → A 재개 | 정확히 같은 ValueError 재현 |
| 위 실패 후 B로 돌아가 재개 | 성공 |
| 같은 DB지만 없는 thread로 재개 | 동일 오류 재현 |

전체 12개 실행 단계가 예상 결과와 일치했다. 재현 traceback의 raise 지점은 `src/app/graphs/nodes/routing.py:70`, `receive_request()`였다. 테스트에서 이 지점이 확인된 것이며 Frodo 프로세스의 원본 traceback을 확보한 것은 아니다.

## 왜 아직 작성 프로세스는 확정할 수 없는가

- 현재 소스의 최초 실행과 재개는 동일한 `runtime.open_graph()` 경로에서 `load_agent_settings().checkpoint_db_uri`를 사용한다. 같은 설정의 단일 프로세스가 호출 종류에 따라 자동으로 DB를 바꾸는 분기는 찾지 못했다.
- `claim_one()`은 CRUD 큐에서 pending Run을 점유하며 실행 환경 구분 필터가 없다. 따라서 요청을 받은 API와 실행 주체를 동일하다고 가정할 수 없다. 이는 코드의 가능성 설명이지 이번 장애의 특정 실행 주체를 입증한 것은 아니다.
- 표본의 DB 로그는 `agent_run/started` 등이며 checkpoint 연결 대상·프로세스 ID가 저장되지 않았다. checkpoint metadata도 step/source/parents만 갖고 있었다.
- 기존 8000 포트 API의 PID 66140은 14:51:12에 시작됐지만, 시작 환경 조회에는 DB 변수가 노출되지 않았다. 이는 `.env`를 런타임에 읽는 프로세스의 최종 설정을 보여주지 않는다. 이 프로세스를 원인으로 지목할 근거는 없다.
- 프로세스 stdout은 터미널에 연결돼 있었고 이 작업에 연결된 앱 터미널은 없어 원본 오류 traceback을 조회하지 못했다. 기존 프로세스를 중지하거나 재설정하지 않았다.

다음으로 필요한 증거는 **각 Run attempt 시작 시 실제 checkpoint host/DB/schema, session/thread, PID/hostname, 코드 revision, 복원된 상태의 존재 여부**다. Frodo를 쓰는 실행 주체에서 이 로그를 수집해 실패 Run과 연결해야 DB가 나뉜 이유까지 확정할 수 있다. 현재 단계에서 테이블 삭제나 일괄 데이터 이동을 해결책으로 삼지 않는다.

## 재실행과 산출물

```bash
PYTHONPATH=src .venv/bin/python scripts/diagnostics/checkpoint_readonly.py
PYTHONPATH=src:. .venv/bin/python scripts/diagnostics/checkpoint_coexistence.py
```

첫 명령은 현재 실행 환경의 `.env` 설정을 따라 DB를 읽기 전용 조회한다. 두 번째는 `.env.local`의 로컬 관리 계정으로 `127.0.0.1:15432`의 임시 DB에서만 실행한다.

- `var/diagnostics/checkpoint-readonly.json`: 제한된 오류 표본의 메타데이터
- `var/diagnostics/checkpoint-coexistence.json`: 통제 실험 결과와 제거된 임시 DB 이름

앱 소스 및 실행 설정은 변경하지 않았고 기존 로컬 Docker API는 계속 실행 중이다.
