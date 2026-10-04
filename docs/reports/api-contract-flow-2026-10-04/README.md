# API 통합 계약 검증 결과

2026-10-04 기준 코드 `2f3b0f6`에 진단 전용 클라이언트를 추가하여 로그인부터 실제 Executor 완료, 후속 입력까지 검증했다. **통합 14개 항목과 API 회귀 81개가 통과했다.** 실제 Executor에서 두 분석을 실행했다. 서버 연계는 확인했지만 모든 함수 인자를 편집할 수 있다는 요구와 현재 계획의 편집 범위 사이에는 차이가 남아 있다. 보고서 파일 등록도 완료 기능이 아니다.

[검증 결과 JSON](result.json), [실행 도구](../../../scripts/diagnostics/verify_api_contract_flow.py), [작업 기록](../../improvements/085-api-contract-flow-verification.md)을 함께 참고한다. 모델 판단의 정확도나 실제 프론트 화면을 검증한 결과로 해석하지 않는다.

## 실행 조건과 변경 범위

| 구성 | 실제 실행 또는 대체 범위 |
|---|---|
| API | loopback 18095 Uvicorn, 실제 create_app과 lifespan |
| 로그인 | 사내 SDK의 직원 검증 결과만 fixture, 실제 Redis 로그인 세션·쿠키·CSRF·권한 검사 |
| DB | 임시 PostgreSQL17, loopback53600. agentic_runtime_test와 agentic_checkpoint_test에 실제 Alembic 적용 |
| API와 이벤트 Worker | 같은 애플리케이션 DB, Agent 동시성2·이벤트 수신 동시성2. 실제 점유·checkpoint·Streams 재개 |
| Redis | 기존 로컬 Executor Redis6379. 시험 전용 consumer group과 로그인 namespace |
| 모델 | 호출당300ms 고정 응답 transport. 실제 create_agent·middleware·graph는 유지, 외부 LLM/Phoenix 호출 없음 |
| Executor | 기존 localhost8000과 Jupyter default kernel. 실제 INLINE 함수 제출·노트북 실행·이벤트·관찰·MULTI finalize |
| 데이터 | /workspace/pv/default_data/df_nce_long_format.parquet. 원천 파일 수정 없음 |
| UI | HTTP 클라이언트로 typed form·SSE 확인. 브라우저/실제 프론트 미실행 |

운영 코드·API schema·Worker·등록 Tool·Executor 구현은 변경하지 않았다. 전용 진단 도구와 계약 설명·결과 문서만 추가했다. 기본 실행 계획의 load/profile/statistics를 실제 수행했고, 두 번째 분석에서는 outliers.method=zscore도 실제 실행했다. 새로운 Executor notebook/execution 이력은 검증 근거로 남겼다.

## 시나리오별 결과

| 순서 | 검증 | 실제 결과 |
|---|---|---|
| 1 | 미로그인과 최초 로그인 | 401, 로그인302, 일반 사용자와 기본 프로젝트 생성, 관리자 목록403·CSRF 누락403 |
| 2 | 프로젝트·세션 조회 | 프로젝트5필드 요약과 상세 분리, 지침 PATCH, default kernel 세션2개 생성·목록 조회 |
| 3 | 일반 답변 | success/answer, HITL과 Executor 제출 없이 종료 |
| 4 | 계획 SSE와 입력 잠금 | 실시간 interaction.opened 수신 후 연결 종료, HITL에서는 respond_to_interaction만 허용, 새 입력409 |
| 5 | 선언되지 않은 인자 편집 | statistics.columns 수정422. 기존 interrupt/resume_token 변화 없음 |
| 6 | 파라미터·도구 편집 | method=zscore로 수정 후 outliers 제외. 계획 revision1→2→3, 공개 run_id 유지 |
| 7 | 로그아웃 후 재로그인 | 동일 Run/interrupt/resume_token 복원, 과거 토큰409 |
| 8 | Executor 대기 | 같은 세션 busy·새 입력409, 별도 세션의 일반 답변은 완료 |
| 9 | 실제 분석과 승인 중복 전송 | load/profile/statistics 성공, finalize 후 Executor SUCCEEDED·runtime.session_id=null. 같은 승인 키 재전송은 같은 Run 반환·추가 제출 없음 |
| 10 | SSE 재접속 | 저장 이벤트26개 sequence 중복 없음·오름차순, Last-Event-ID7 이후 정확한 suffix 재생·공개 Run 일치·Tool 소스 표본 비노출 |
| 11 | 완료 결과 설명 | 새 Run에서 이전 실제 분석 근거를 모델에 제공, Executor 추가 제출 없음 |
| 12 | 보고서 표현 변경 요청 | 새 Run의 answer로 처리, Executor 추가 제출 없음. 실제 모델의 재작성 품질 검증은 아님 |
| 13 | 완료 후 입력과 파일 경계 | active_run=null·send_message 허용. report.ready/content는 있으나 artifact_registration=deferred·Artifact POST 없음 |
| 14 | 새 분석 | 새 Run·새 Execution, outliers의 실제 관찰에서 zscore 확인. Operation 추가와 finalize 제출 |

첫 분석은 Execution `c4f15dff-fcf9-4bba-9191-96cc9847e02b`, 두 번째는 `025cc430-9a34-4809-a038-0b73e2e52265`다. 실제 성공한 POST는 Execution 생성2회·Operation 추가1회·Finalize2회다. 두 번째 Execution의 terminal과 outliers 성공은 Agent가 받은 실제 결과로 확인했고, 첫 Execution은 별도 Executor 상세 GET으로 kernel 해제까지 확인했다.

## 요구사항과 현재 동작의 차이

### 파라미터 편집 범위

현재 typed form은 계획의 arguments에 선언된 인자를 보여준다. literal 값은 parameter_controls의 editable=true와 value_schema를 갖춰야 수정할 수 있다. Agent 판단 인자는 output_schema를 기본 범위로 사용한다. Python signature/docstring에 인자가 있다고 해서 모든 선택 인자와 기본값을 자동 노출하지 않는다. quality-review 계획의 statistics에는 columns 인자가 없어 이번 수정 요청이422였다.

현재 [prepare_review](../../../src/service_contracts/plan_review.py)의 계약대로 동작했다. 다만 처음 논의한 ‘사용자가 Tool의 확정값도 조정’이라는 범위를 모든 선택 인자까지 기대한다면 추가 구현이 필요하다. 다음 작업은 등록 인자 정보와 계획 편집 가능 항목의 관계를 명시하고, 사용자에게 필요한 선택 인자를 계획에 넣도록 생성·검증 규칙을 정하는 것이다. 모든 내부 참조까지 무조건 편집 가능하게 여는 것은 권장하지 않는다. 이번 검증에서 권한을 임의로 확대하지 않았다.

### 사용자 제외와 실행 중 건너뛰기는 다른 목록

사용자가 제외한 outliers는 승인 snapshot의 excluded_step_ids에 남으며 실행 단계에서 제거된다. 최종 skipped_steps는 남은 단계 중 조건/의존성 때문에 실행 시 건너뛴 단계다. 이번 최종 skipped_steps=[]이지만 outliers는 실행되지 않았다. UI는 승인 계획과 interaction.resolved의 계획 화면을 이용해 사용자 제외를 표시해야 한다. [freeze_approval](../../../src/service_contracts/plan_review.py)과 [최종 응답 생성](../../../src/agent_service/agents/analysis/execution/nodes.py)을 기준으로 문서화했다. 별도 최종 통합 목록을 추가할지는 후속 API 선택이다.

### 보고서 표시와 파일 보존

첫 분석의 보고서 content3713자는 Run 결과로 제공됐으나 artifact_registration은 deferred였다. Executor Artifact POST·Markdown 파일 등록·노트북 마지막 보고서 셀 저장은 수행되지 않았다. UI가 ‘다운로드 가능한 파일로 저장됨’이라고 표시해서는 안 된다. 기존 사용자 결정대로 호출 시점과 terminal 허용 여부는 별도 확정이 필요하다.

## 회귀 검증과 재현

실제 PostgreSQL에서 SSO·프로젝트 CRUD·세션 activity/settings·Run 읽기 회귀를 실행하여77 passed/1 skipped를 얻었다. planning DB가 필요한 cookie Run/POST-SSE 케이스는 별도 실행에서3개 계획 회귀와 함께 통과하여4 passed다. 합계81 passed이며 첫 실행의 skip은 두 번째 실행에서 검증했다. POST SSE는 이 별도 ASGI/PG 회귀이고, 실제 Executor 통합에서는 POST 접수+GET SSE를 사용했다.

```sh
PYTHONPATH=src .venv/bin/python scripts/diagnostics/verify_api_contract_flow.py \
  --settings-file /tmp/private-api-flow-settings.json \
  --output /tmp/private-api-flow-result.json
```

settings-file은0600 private flat JSON이며 DATABASE_URL은 loopback agentic_runtime_test, CHECKPOINT_DB_URI는 loopback agentic_checkpoint_test, REDIS_URL/EXECUTOR_BASE_URL은 실제 로컬 서비스만 허용한다. EXECUTOR_SHARED_RESULT_ROOT에 로컬 Executor shared_dir, ANALYSIS_DATASETS.default-nce에 위 Jupyter 데이터 경로를 제공한다. EW_DATABASE_URL은 DATABASE_URL과 같게 파생하고 별도 endpoint 별칭을 중복 주입하지 않는다. 모델과 SSO fixture 설정은 진단 프로세스에만 설치한다. 운영 서버 실행에 이 도구를 사용하지 않는다. DB 초기화 pytest와 동시에 실행하지 않는다.

개발 중 다섯 번의 미완료 시도가 있었다. 처음에는 비어 있는 명시적 모델 catalog로 설정 검증이 거절됐다. 다음에는 클라이언트가 interaction.required라는 잘못된 이벤트명을 기다렸다. 세 번째의422는 위 편집 범위 확인에 사용했다. 네 번째에는 클라이언트가 사용자 제외를 최종 skipped_steps로 잘못 해석했고, 다섯 번째에는 Executor GET의 state.status 대신 최상위 status를 조회했다. 각각 진단 설정/클라이언트만 정정했다. 이를 서비스 실행 실패율이나 성능 수치에 합산하지 않는다. 일부 시도의 실제 Executor 성공 이력도 삭제하지 않았다.

## 제한과 후속 판단

최종 통합13.771초는 두 분석과 고정 모델300ms·시험용50ms 폴링·HTTP 확인을 포함한 기능 검증 경과 시간이다. 처리량, 실제 LLM 지연 또는 종전 대비 성능 개선값이 아니다. 모델 역할 호출11회도 이번 고정 시나리오의 사실이며 호출 최적화는 기존 후순위다.

실제 사내 SDK/브라우저 왕복, 자연어 의도 분류·계획/보고서 품질, 실제 프론트 UX, SINGLE, 대용량/1주 실행, 다중 Pod/Worker 재시작, 전처리 데이터 registry, 첨부/VLM, Workflow CRUD는 검증하지 않았다. 이번 결과로 이 기능들이 완료됐다고 판단하지 않는다. 후속 우선순위는 파라미터 화면 범위 정리와 UI 계약 연결이다. 보고서 Artifact·데이터 registry는 미확정/외부 구현 의존성을 유지하고, 최종 성능 시험은 기능 검증 이후 진행한다.

진단 API와 모델 HTTP client를 종료했고, 시험 전용 Redis group·로그인 키만 정리했다. 임시 PostgreSQL도 회귀 검증 후 종료했다. 기존 Compose 서비스·공유 Stream·원천 데이터는 유지했다. 베이스 미병합·미푸시·미배포다.
