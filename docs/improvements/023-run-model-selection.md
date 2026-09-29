# 023 — Run 단위 LLM 선택 고정

상태: 구현·전체 회귀·패키지 검증 완료 (2026-09-29)
브랜치: `feature/run-model-selection`, 기준: `403c77d` (022 베이스 병합 완료)
커밋: 구현 커밋 생성 후 기록에 연결한다. 원격 push·배포 미수행.

## 문제와 범위

기존 모든 역할 Agent가 프로세스 기본 MODEL_*만 사용했다. 최초 요청의
main_model_name을 처리하지 않으므로 여러 모델을 선택할 수 없으며 배포 후
기본 설정 변경이 장기 Run의 후속 추론 모델을 바꿀 수 있었다.

최초 접수 시 등록 alias와 비밀정보 없는 설정 hash를 고정한다. 사용자 resume,
자동 재시도, Executor 이벤트 모두 이 선택을 사용한다. 모델별 역할 Agent 묶음만
공유하며 그래프·Worker·DB 풀을 모델별로 생성하지 않는다.
Workflow 관리, Message CUD, project_memory는 이번 범위에서 제외했다.

## 실제 변경

- `service_settings.py`, `agent_service/model_selection.py`: 중앙 MODEL_CATALOG/DEFAULT_MODEL
  설정 검증, 기존 평면 설정의 default 별칭 호환, immutable catalog, alias/revision 참조.
- `run_schema.py`, `run_service.py`, `public_run_service.py`: 최초 main_model_name 접수,
  서버 전용 메타데이터 저장, 공개 조회 반영, unknown 422 및 재개 불일치 409.
  기본 모델이 바뀌어도 동일 원본 요청의 Idempotency-Key 재전송은 기존 Run을 반환한다.
  기존 모델 미지정 요청의 digest도 유지한다. DB 스키마 변경은 없다.
- `agent_graph_service.py`, `agent_project_context.py`: 최초 입력에 모델 선택 저장,
  resume에서 저장된 Run 선택과 checkpoint 선택 비교. 기존 프로젝트 snapshot 조회를
  재사용하므로 새 검증을 위해 checkpoint를 중복 조회하지 않는다.
- `state.py`, `context.py`, `dependencies.py`: state → context → 모델별 역할 Agent 연결.
  routing/intent/skill/workflow/FAQ/report/conditional 전체 역할 적용.
  create_agent·프로젝트 프롬프트 미들웨어·비동기 호출·모델별 구조화 출력 방식을 유지한다.
- `langgraph_adapter.py`, `worker_main.py`: Executor 이벤트도 저장된 선택을 검증한 뒤
  그래프 재개. 기존 이벤트 receipt 처리와 API 종료 상태 반영은 유지한다.
- 모델 참조가 없거나 설정이 바뀐 큐 작업은 `RUN_MODEL_UNAVAILABLE`로 종료한다.
  같은 설정 오류를 자동 재시도하지 않는다. Executor 대기 상태는 임의 해제하지 않는다.
- 상태 조회 projection은 모델 참조 한 컬럼만 추가해 24 → 25 컬럼이다.
  요청 본문·전체 메타데이터·로그 등 불필요한 데이터 로딩 제거 정책은 유지한다.

## 검증과 관측

LLM은 로컬 httpx.MockTransport, API는 실제 ASGI, DB는 새 임시 PostgreSQL 17의
`identity_test`를 사용했다. 기존 서비스·앱 DB는 수정하지 않았다.

1. 모델 관련 단위 21건 통과: config 우선순위/잘못된 설정, immutable catalog,
   키 회전·모델 변경 식별, 기존 idempotency digest, 재개 모델 변경 차단,
   7개 실제 create_agent 역할의 두 모델 동시 호출과 프로젝트 프롬프트 격리.
2. 모델 관련 PostgreSQL 통합 7건 통과: 모델 기본값/명시값 접수, 거절 시 Run·Task 0건,
   기본 변경 후 replay·retry·resume 고정, 설정 제거/변경/과거 참조 누락 시 부작용 없이 거절,
   큐 접수 후 모델 제거 시 LLM 호출 0회·재시도 0회·명시적인 오류 종료.
3. PostgreSQL 체크포인트와 실제 create_agent 결합: 최초 LLM 호출 후 HITL,
   기본 모델 변경 및 런타임 재생성 후 resume, 다시 런타임 재생성 후 Executor 이벤트/report.
   세 번의 실제 클라이언트 요청 body.model이 모두 최초 model-b였다.
   이벤트 중복 전달 후 보고서 LLM 호출이 증가하지 않았다.
4. 기존 짧은 트랜잭션 회귀까지 포함한 대상 검증: 19건 통과.
5. 전체 회귀 결과: **523 passed, 2 subtests passed**, 189.73초, skip/failure 0건.
   1차 검증에서 과거 프로젝트 prompt snapshot을 흉내 낸
   테스트의 모델 참조 누락 1건을 발견하고, 해당 fixture에 참조를 추가했다.
   모델 참조 없는 재개 거절 자체는 별도 신규 테스트로 검증했다.
6. wheel 격리 검증 통과: 소스 체크아웃 import 없이 API 34경로, 7역할 create_agent 구성,
   역할별 checkpoint 비활성, 리소스 포함, mock graph 6단계 실행.

실행 명령은 `PYTHONPATH=src DTEST_IDENTITY_TEST_DATABASE_URL=<격리 로컬 identity_test DSN>
python -m pytest src/app/test src/agent_service/agents/analysis/tests -q --disable-warnings`이다.

## 제한과 운영 조건

- 실제 외부 LLM·Executor·Redis 전달 경로 및 Kubernetes 배포는 검증하지 않았다.
  Executor 이벤트 adapter/세션 소유권/API 완료 반영은 실제 로컬 구현으로 검증했다.
- 모델 기록이 없는 과거 중단 Run의 모델은 추정하지 않는다. 조회/취소/기존 요청 replay는
  유지하되 재개는 명시적 복구 없이는 차단한다. 자동 backfill 마이그레이션을 만들지 않았다.
- 장기 작업이 끝날 때까지 같은 alias의 같은 설정을 API/이벤트 Worker에 유지해야 한다.
  새 모델은 새 alias로 추가한 뒤 기본값만 변경한다. 설정 복원·이벤트 재처리 및 관리자
  복구 API는 후속 운영 항목이다.
- hash는 서버 설정을 식별한다. 제공자가 같은 endpoint/model ID의 가중치를 교체하는
  것까지 감지하지 못한다. 필요하면 제공 모델 버전도 고정한다.
- 이번 변경은 모델 선택/연속성 기능이며 처리시간 개선 수치를 주장하지 않는다.

[API·등록 설정·Agent 개발자 가이드](../run-model-selection.md)

[검증 결과 요약](../reports/run-model-selection-validation-2026-09-29.json)
