# 103. Executor API 경로를 연동 규격으로 통합

## 문제

Executor v1 REST의 고정 경로가 Agent 설정8개와 Event Worker 설정1개, 환경별 YAML·Compose에 흩어져 있었다. 이벤트 이력 경로를 제출 상세 경로에서 다시 유도하는 설정 조립도 필요했다. 같은 환경에서 /api/v1을 base와 경로에 각각 붙이거나 별도 이벤트 경로를 지정할 수 있어 연동 규격과 배포 설정이 섞였다.

## 변경

- `src/dtest/infrastructure/executor/routes.py`의 ExecutorRoute 한 곳에서9개 경로를 정의한다.
- Executor HTTP client와 이벤트 이력 Worker가 같은 규격을 사용한다.
- AgentSettings의8개 경로 필드·URL property, EventWorkerSettings의 경로 필드·template validator, loader의 경로 파생 코드를 삭제했다.
- 추적되는 YAML 예시·Compose의 개별 API PATH 설정과 현재 진단 실행기의 API-prefixed 중복 base를 제거했다.
- 현재 버전의 비교 벤치마크에는 root base만 전달한다. 이전 Git snapshot의 조건을 재현하는 historical 설정 분기는 보존한다. 기존 측정 보고서는 변경하지 않는다.
- 과거 경로 키가 YAML/env에 남아 있으면 값을 노출하지 않는 이행 오류로 삭제를 안내한다. 별도 endpoint override는 제공하지 않는다.

## 고정 API 규격

| 작업 | 경로 |
|---|---|
| 실행 제출 | /api/v1/executions |
| 실행 상세 | /api/v1/executions/{execution_id} |
| operation 제출 | /api/v1/executions/{execution_id}/operations |
| 결과 조회 | /api/v1/executions/{execution_id}/result |
| Notebook 조회 | /api/v1/executions/{execution_id}/notebook |
| 종료 | /api/v1/executions/{execution_id}/finalize |
| 취소 | /api/v1/executions/{execution_id}/cancel |
| Artifact 등록 | /api/v1/executions/{execution_id}/artifacts |
| 이벤트 이력 조회 | /api/v1/executions/{execution_id}/events |

## 배포 설정 이행

`EXECUTOR_BASE_URL`에는 서버 root 또는 reverse proxy의 서비스 root를 넣는다. `/api/v1`을 포함하지 않는다. 예: `http://executor:8080`, `https://gateway.example/executor`. 코드가 API prefix를 붙인다.

`EXECUTOR_EXECUTIONS_PATH`, `EXECUTOR_EXECUTION_PATH`, `EXECUTOR_OPERATIONS_PATH`, `EXECUTOR_RESULT_PATH`, `EXECUTOR_NOTEBOOK_PATH`, `EXECUTOR_FINALIZE_PATH`, `EXECUTOR_CANCEL_PATH`, `EXECUTOR_ARTIFACTS_PATH`, `EXECUTOR_EVENTS_PATH`, 구 별칭 `EXECUTOR_JOBS_PATH`, `EW_EXECUTOR_EVENTS_PATH`를 삭제한다. 기존 base가 /api/v1로 끝나면 그 suffix도 제거한다. 파일 제출 방식 PATH/INLINE, PV 입력·결과 root, timeout·TLS·pool 설정은 이 API 경로 설정과 별개로 유지한다.

## 검증

- 실제 로컬 HTTP 소켓으로 제출·operation·finalize·cancel·artifact·상세·result·notebook 경로와 연결 재사용을 확인했다. root 주소와 두 종류의 프록시 prefix를 검증했다.
- EventRouter의 실제 이력 보충 호출을 HTTP MockTransport에서 실행해 동일 규격·prefix·after_sequence·limit 전달과 catch-up 완료를 확인했다.
- 관련 API HTTP·설정·YAML·계층 경계·이벤트 알림174개, Agent 전체377개가 통과했다.
- 새 wheel을 만들고 checkout import 없이 설치 검증을 통과했다. API32개 경로·역할 Agent5개·프롬프트·Skill/Tool·공개 schema·demo 자산 로딩을 확인했다.
- 현재 src의 Executor v1 경로 문자열은 routes.py에만 있으며, 설정 클래스의 path/url property와 추적되는 YAML·Compose의 경로 override는 없다. git diff --check 통과.

실제 외부 Executor 작업을 생성하는 성능 측정이 아니라 HTTP·Agent·설정 회귀 및 설치 검증이다. PostgreSQL 테이블·Redis 이벤트 계약·Executor 제출 body·파일 제출 방식은 변경하지 않았다.
