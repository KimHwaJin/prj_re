# 111 — 남아 있던 미사용 계약 선언 삭제

## 요청과 작업 기준

110에서 자동 승인 검토 때문에 남겼던 삭제 후보에 대해 사용자가
“삭제해야하는건 다 삭제해줘”라고 추가로 지시했다.
`feature/refactor-base`의 `3cb1588`에서
`feature/unused-code-removal`을 만들고 사용처를 다시 확인했다.

소스·테스트·개발 도구·문서의 참조, 서비스 조립 코드와 공개 OpenAPI를
대조했다. import가 없는 등록 Tool은 Executor에 제출할 코드 자산이므로
삭제 대상으로 판단하지 않는다. 실제 사용 중인 내부 DTO와 오프라인
계약 검증용 DTO도 구분했다.

## 삭제한 구현

| 위치 | 삭제 항목 | 확인한 실제 경로 |
| --- | --- | --- |
| `contracts/executor.py` | `ExecutorArtifactInlineSource`, `ExecutorArtifactPathSource`, `ExecutorArtifactRequestBody` | `submit_execution_artifact()`는 payload dict를 HTTP로 제출한다. 삭제한 DTO를 사용하는 호출자나 OpenAPI 참조가 없다. |
| `contracts/workflow_retrieval.py` | `WorkflowRetriever` Protocol | container가 실제 검색 callable을 `PlanningRuntime.workflow_retriever`에 주입한다. Protocol을 import하거나 구현 대상으로 사용하는 코드가 없다. |
| `contracts/dataset_registry_draft.py` | `DatasetRegistry` Protocol | 선언만 있던 미래 provider 인터페이스다. 현재 provider와 호출자가 없다. |

위 세 Artifact DTO는 서로만 참조하고 외부 호출자가 없던 묶음이었다.
이들을 제거하면서 사용되지 않는 Protocol import도 제거했다.
새 adapter나 대체 wrapper를 추가하지 않았다.

Dataset Registry 초안 자체는 Executor 개발 협의에 필요한 산출물이며,
진단 스크립트와 오프라인 회귀 테스트가 실제로 사용한다. 해당 DTO·검증 규칙·
JSON Schema·예시는 유지했다. 실제 API가 구현되어 있는 것으로 설명하지
않도록 문서의 provider 설명과 오래된 경로를 수정했다. 같은 문서의 인증
문맥 설명도 현재 SSO 로그인 기준으로 정정했다.

## 추가 삭제 여부 검토

production Python 모듈의 최상위 함수·클래스 이름을 소스·테스트·개발 도구와
대조했다. 후보의 모듈 내부 참조, Pydantic 검증 및 FastAPI decorator 등록,
문서·동적 자산 로딩을 확인한 결과 이번 범위에서 추가 미사용 선언은
확인되지 않았다. 이는 모든 동적 호출의 완전한 도달성 증명을 의미하지 않는다.

Executor HTTP 함수·경로, Worker, Run/HITL, 실제 추천 DTO, 등록된
Skill/Tool/Workflow 실행 내용, DB schema와 사용자 데이터는 변경하지 않았다.
불필요하다는 근거가 없는 테이블이나 운영 데이터는 삭제하지 않았다.

## 검증

- 관련 회귀: **177 통과**, 7 warnings, 5.52초.
  Workflow 추천·선택·승인, Workflow 표준, Dataset 초안,
  Executor 실제 로컬 HTTP 소켓 및 Artifact 제출, SSO 테스트를 실행했다.
- 삭제 전후 **전체 OpenAPI JSON 동일**. description까지 포함해 비교했다.
- Dataset `ContractExamples.model_json_schema()` **동일**.
- Dataset 계약 진단 스크립트 통과: schema definitions16개.
  실제 Executor 호출·PVC 파일 읽기는0이며 오프라인 검증이다.
- `uv run --locked --no-sync ruff format --no-cache --check .`:
  797파일 포맷 통과.
- `uv run --locked --no-sync ruff check --no-cache .` 실행:
  전체3,079→3,076, `src/dtest`632→629 진단. 새 진단 없음.
- `uv run --locked --no-sync ty check` 실행:
  기존759진단 유지. 위치 이동을 제외한 새 진단 없음.
- `git diff --check` 통과.

실행 환경에 설치된 고정 버전 도구를 `--no-sync`로 사용했으며,
ty의 Python 환경은 기존 Python3.11 검증 환경으로 지정했다.
전체 lint/type 검사가 통과한 것은 아니다. 경고는 checkpointer가 없는
추천 테스트에서 기존 durability 설정이 무효라는 LangGraph 경고다.
사내 SSO SDK·외부 LLM·실제 운영 Executor E2E나 부하 테스트를
새로 수행한 결과로 해석하지 않는다.

## 결과와 후속

사용하지 않던 선언5개와 그에 딸린 import를 제거했다. 기존 삭제 보류는
이번 작업에서 해소했으며 공개 API와 동작 경로의 변경은 없다.
성능 개선 수치를 주장하는 작업은 아니다. 남은 품질 작업은 Agent 상태와
반환 타입, application 조회 결과 타입 및 기존 lint 진단 정리다.
