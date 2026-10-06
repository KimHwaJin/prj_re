# 112 — 오래된 문서·복제 테스트 정리

## 요청과 기준

사용자가 현재와 다른 문서 및 불필요한 옛 테스트 삭제 검토안을 승인했다.
`feature/refactor-base`의 `dbdadbc`에서 `feature/docs-test-cleanup`으로 작업했다.
현재 정본·실행 가능한 회귀·과거 증거를 구분하고 복제 테스트의 고유 단언을
현재 회귀로 이관했다. 서비스 API·DB schema를 변경하는 기능 작업은 아니다.

## 삭제 목록

| 삭제 파일 | 근거·대체 |
| --- | --- |
| docs/api_service_integration.md | 폐기된 completion 통합 초안. public-run-api.md로 대체 |
| docs/data_selection.md | 현재 없는 data-selection 전용 요청 예시 |
| docs/data_selection_api_contract.md | 폐기된 스키마·전용 HITL API 초안 |
| docs/runtime_configuration.md | 옛 결과 조회·보고서 경로 설명. 현재 Executor/기동 문서로 정리 |
| docs/api-service-layout.md | 이전 src/api_service 구조. architecture/service-layout.md와101 이력으로 대체 |
| docs/reports/result-log-replay-2026-10-04/test_portable_probe.py | 아래 두 파일과 내용이 동일하고 삭제된 api_service import 사용 |
| docs/reports/task-event-insert-2026-10-04/test_portable_probe.py | 같은 복제 probe |
| docs/reports/task-event-full-return-2026-10-04/test_portable_probe.py | 같은 복제 probe |

보고서 probe의 고유 SHA256 prefix는 세 파일 모두 b3e0b465ed039895였다.
같은 이벤트 ID로 다른 내용을 replay해도 최초 payload가 보존되는 단언을
현재 `tests/api_service/test_plan_event_batch_postgres.py`에 통합했다.
기존 event/log ID·sequence·rollback 검증도 유지한다.

## 현재 안내 갱신

- README·docs/README·tests/README에 현재 실행·계약·검증 정본을 안내.
- 기동 문서를 flat YAML, ServiceSettings, 선택적 DB_INIT_ON_START와 실제
  중앙 schema 준비 경로로 재작성. 존재하지 않는 module·오래된 그룹형 설정 제거.
- resume 문서를 통합 POST, 고정 공개 Run ID·private invocation·agent_commands,
  consumption receipt와 projection 복구의 현재 구분에 맞춤.
- 폐기 API 구조 문서의 진입 링크를 현재 서비스 구조로 대체.
- Agent·계획·메모리·커널·Executor·모델·배포의 YAML 예시9개를 최상위 키로
  바꿈. 기동 예시를 포함한10개를 실제 load_settings(config=..., environ={})로 검증.
- 실행 중 소비하지 않는 Executor 결과/보고서 설정3개는 설정 목록에서 선언만
  남아 있음을 명시. 이번 문서 작업으로 설정 키 삭제나 지원 기능을 추가하지 않음.
- 지원되지 않는 Locust 제출·인증 명령 제거. 현재 service_throughput/worker_e2e와
  고정 commit용 과거 스크립트의 범위·측정 의미를 구분.
- 현재 안내·설계 문서의 깨진 로컬 파일 링크45→0. 개인 config.yml 대신 tracked
  example을 안내. 옛 설계의 당시 코드/줄 번호는 역사적 위치로 표시하며 현재
  구현의 근거 링크처럼 바꿔 연결하지 않음.

과거 개선 기록·리뷰·측정 JSON/CSV·manifest 및 Workflow 원본1.0·변경 추적·확정
계약은 유지한다. 보고서의 과거 파일명·hash·수집 결과는 당시 Git commit 기준이다.
`docs/reports/README.md`에서 복제 probe 삭제와 현재 회귀 위치를 안내한다.

## 테스트 수집 정리

pyproject 기본 testpaths는 `tests`, `scripts/diagnostics/tests`다.
벤치마크는 README의 opt-in 조건과 경로를 명시해 실행한다.

| 이전 파일 | 현재 파일 |
| --- | --- |
| executor_throughput/test_analysis.py | test_executor_analysis.py |
| process_scaling/test_analysis.py | test_scaling_analysis.py |
| service_throughput/test_analysis.py | test_service_analysis.py |

같은 Python module 이름 충돌을 없애고 service analysis import도 명시적인
`scripts.benchmarks.service_throughput.analyze`로 바꿨다. 기본 수집만 제외해
벤치마크 내부 충돌을 숨기지 않고 별도 전체 수집도 확인했다.

## 검증

- 변경 전 기본 pytest 수집: 1,416 수집 + 오류5개. 구형 보고서 import3개와
  벤치마크 파일명 충돌2개였다.
- 변경 후 기본 수집: **1,364개, 오류0**. Agent377 + API974 + 진단13.
- 변경 후 `scripts/benchmarks` 명시적 전체 수집: **69개, 오류0**.
  중간 수집에서 추가로 드러난 analyze import 충돌도 수정했다.
- 관련 benchmark 분석·진단·package 경계·bootstrap·startup 회귀:
  **69 통과, 27 skip**, 4.43초. private capture가 없는 조건은 실행했다고 주장하지 않음.
- 일회용 PostgreSQL17/pgvector0.8.6에서 planning API + projection:
  **5 통과**, 12.83초. 최종 타입 정리 후 projection **2 통과**, 4.54초 재확인.
  두 결과는 겹치므로 합산하지 않음. 사용 중인 DB/컨테이너와 분리했으며 이번
  테스트 전용 컨테이너와 익명 볼륨은 검증 후 제거.
- 현재 YAML 예시10개 loader 검증, 현재 안내·설계의 로컬 파일 링크0개 오류,
  git diff --check 통과.
- 고정 도구를 uv run --locked --no-sync로 실행. Ruff3,076/ty759 기존 진단 유지,
  rename·행 이동을 정규화한 새 진단 없음. Ruff format 전체795파일 통과.
  전체 lint/type 통과는 아님. 역사적 보고서/개선 기록의 옛 링크는 현재 정본
  검증에서 제외하며 내용을 소급 변경하지 않음.

이번 결과는 전체 회귀 재실행·실제 SSO SDK/LLM/운영 Executor E2E·부하 측정이
아니다. 과거237.3MiB 측정 원본을 삭제하거나 용량을 대폭 줄인 작업도 아니다.

## 후속

기존 코드 품질·타입 경계 정리를 이어간다. 옛 scripts/loadtest를 현재 SSO·Executor
종료 지점으로 이행할지 폐기할지는 성능 시나리오 작업에서 결정한다. 동적 Dataset
Registry, 실제 모델 품질, 운영 배포 검증의 기존 우선순위는 바꾸지 않는다.
