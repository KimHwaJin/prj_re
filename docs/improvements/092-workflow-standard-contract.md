# 092 Workflow 표준 계약·초안 대비 추적

날짜: 2026-10-05. 브랜치 `feature/workflow-standard-contract`, 기준 `4ecc416`. 구현·격리 검증 완료. 베이스 미병합·미푸시·운영 미배포.

구현 커밋: `441420a` (`feat: standardize workflow authoring with tracked draft migration`).

## 요청과 문서 구분

받은 Workflow 1.0 초안을 일반화하여 구현하되, 원본의 어느 내용을 바꿨는지 추적하고 별도 확정 문서를 제공한다.

- [보존 원본](../contracts/workflow-standard/original-1.0/): ZIP의 4개 파일을 byte 그대로 보존하고 ZIP/파일 SHA256을 기록했다. 원본 CRLF도 보존하며 `.gitattributes`로 Git 줄바꿈 변환을 제외했다.
- [변경 추적표](../review/workflow-standard-changes.md): W01~W17별 원본 필드·명세 줄 번호, 변경 내용, 이유, 구현과 검증 위치, 이행 방법을 기록했다.
- [확정 규격 2.0](../workflow-standard.md): 작성자가 사용할 필드·기본값·조건·참조·승인·API·지원 경계를 정의했다.
- [Schema·예제 묶음](../contracts/workflow-standard/README.md): 공식 JSON Schema와 static/adaptive JSON, 한국어 주석 JSONC를 제공한다. 받은 예제를 조용히 덮어쓰지 않았다.

공개 포맷 2.0과 내부 승인 계획 2.0-draft는 서로 다른 계약이다. 원본의 index 참조·입력 식별·조건과 승인 표시를 바꾸므로 호환되는 1.0 수정으로 표현하지 않는다.

## 구현

`service_contracts/workflow_standard.py`가 공개 정의를 기존 공통 계획으로 정규화한다. API/Agent 각각 별도의 실행기를 만들지 않았다. 등록 Skill/Tool 소속, 실제 함수 인자, 명명 입력과 출력, 선행 참조, 결과 기반 판단, 조건부 소비와 실행 정책은 기존 공통 validator로 검증한다. 공개 정의에는 자유 Python 코드를 넣지 않는다.

출력은 등록 별칭→dict/list selector로 연결한다. 등록 정책이 우선이며 기존 AST 반환 힌트는 보조다. 함수 본문을 바꾸지 않고 실제 반환값 전체·중첩 key/index를 연결한다. `ROC AUC` 같은 실제 key는 공개 alias와 구분하고 `X`, `_Factor` 같은 실제 함수 인자도 그대로 허용한다. 업무 Tool의 이름/인자/데이터 형식에 맞춘 분기는 추가하지 않았다.

초안의 배열 실행 순서는 `ordered_call_ids`로 보존한다. 순서 장벽과 실제 데이터 의존성을 구분하여 조건부 호출을 생략해도 독립 후속 호출은 실행한다. 미확정 판단을 건너뛰고 후속 호출을 먼저 실행하지 않는다. 사용자 입력/Tool 파라미터 편집·호출 제외·최종 승인·source/metadata pin·복구·Executor 제출·Finalize는 기존 경로를 사용한다.

Workflow API는 공개 2.0을 Run 없이 직접 등록하고 수정·복제·승격할 수 있다. 본문 수정은 최신 SHA 비교와 DB 행 잠금을 사용하며 경쟁 요청은 409로 거절한다. UUID+SHA별 파일을 유지하여 이전 내용을 보존하고 DB 실패 시 새로 만든 파일만 정리한다. 과거 내용으로 되돌릴 때 기존 파일을 재사용하며 rollback으로 그 파일을 삭제하지 않는다. 이름/설명과 JSON 본문을 동기화하고 수정 시 기존 embedding을 비활성화한다. 공개 2.0 승격에는 Executor 실행 성공이 필요하지 않다. 기존 1.3 호환 경로는 별도로 유지한다.

DB DDL/환경변수/서비스 배포/Executor HTTP 규격/Redis 이벤트 규격/모델 호출 수는 바꾸지 않았다. 원래 작업 디렉터리의 사용자 변경과 실행 중 콘솔·Executor·DB·Redis는 건드리지 않았다.

## 검증 결과

| 검증 | 최종 결과 | 범위 |
|---|---|---|
| Agent 전체 + API 경계/파일/그래프/종료/배포 설정 회귀 | 485 passed, 23.66초 | 표준 신규 28개 포함. 기존 checkpointer 없는 test double의 durability 경고 68개 |
| 격리 PostgreSQL Workflow + 기존 계획 API | 8 passed, 14.19초 | 신규 5개: 직접 등록/권한/수정/복제/승격/soft delete, 오류 거절, 동시 수정, DB 실패, 과거 파일 재사용 |
| 받은 원본·Schema·JSONC 동등성 | 위 회귀에 포함 | 원본 SHA, 구현/배포/문서 Schema 일치, strict JSON/주석 예제 일치 |
| 독립 inventory/billing 자산 | 위 회귀에 포함 | 실제 등록 함수 실행. 결정적 모델·LocalExecutor double로 승인→판단→후속 실행→Finalize→terminal/report |
| offline wheel + checkout 없는 설치 smoke | PASS | 신규 Schema/정규화/출력 매핑 포함, 30개 OpenAPI path, create_agent 역할 5개, 폐기 패키지 미포함 |

검증 명령은 프로젝트 venv Python, `PYTHONPATH=src`, pytest `-p no:cacheprovider`를 사용했다. 회귀 대상은 `src/agent_service/agents/analysis/tests`와 API의 `test_package_boundaries.py`, `test_workflow_persistence.py`, `test_graph_invocation_boundary.py`, `test_graceful_shutdown.py`, `test_deployment_configuration.py`다. PostgreSQL은 `test_workflow_standard_postgres.py`와 `test_planning_api_postgres.py`를 opt-in 임시 설정으로 실행했다. 패키지는 깨끗한 소스 사본의 offline wheel과 `scripts/diagnostics/validate_agent_package.py`로 검사했다.

PostgreSQL 테스트용 컨테이너 `dtest-workflow-standard-092`는 localhost:53606에 별도로 생성한 postgres:17이며 종료 후 제거했다. 실 서비스 DB나 기존 테스트 콘솔을 재설정하지 않았다. 실제 사내 LLM·Executor HTTP/Redis·Pod를 통한 새 규격 연계 및 처리량 측정은 이번 검증 범위가 아니다.

원본의 CRLF를 기본 diff 검사가 trailing whitespace로 표시하여, 원본 경로에만 `whitespace=cr-at-eol`을 선언했다. 원본 byte를 바꾸어 경고를 없애지 않았다. 그 규칙으로 전체 diff check를 통과했다.

## 발견·보완한 내용

- 실제 PG 수정 응답에서 updated_at lazy load의 MissingGreenlet이 발생하여 명시 refresh로 보완했다.
- 동일 태그 재저장 시 unique 충돌 가능성을 기존 Tag 객체 재사용으로 제거했다.
- 새 파일 덮어쓰기·DB rollback 및 과거 revision 재사용 시 삭제 문제를 immutable 파일과 생성 여부로 구분했다.
- 공개 ID 규칙을 실제 반환 key/함수 인자에 잘못 강제할 가능성을 제거했다. 별칭과 Python 이름을 구분한 회귀를 추가했다.
- 첫 회귀의 실제 SIGTERM 테스트는 sandbox의 localhost bind 제한으로 실패했다. 같은 테스트를 소켓 사용이 허용된 실행에서 통과했고, 최종 전체 485개도 같은 조건에서 통과했다.
- 신규 uppercase 인자 테스트의 dict/scalar 기대값을 각각 맞춰 검산했다. 생산 함수 실행의 실패가 아니라 테스트 assertion 문제였다.

## 후속

pgvector 실제 검색/재색인, 등록 template의 대화 추천 풀 연결, 내부 Agent 계획→공개 JSON exporter, 1.0/1.3 일괄 이행, HTML/Artifact/Dataset 외부 연계는 미완료다. 확정 규격은 작성·검증 계약의 완료이며 이 기능들의 완료를 뜻하지 않는다. 실제 현업 Skill/Tool과 자연어 요청으로 추천·승인·실행까지 확인하고 배포 전 기존 HITL/실행 중 자산 revision 이행을 검토한다.
