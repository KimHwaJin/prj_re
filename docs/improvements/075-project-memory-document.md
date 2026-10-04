# 075 프로젝트 메모리 단일 Markdown 문서

상태: 구현·격리 검증 완료 / 베이스 미병합·미푸시·미배포.
브랜치: `feature/project-memory-document`. 부모 `b97b884`(`feature/foreign-key-review`).

## 문제와 변경

관리 화면에서 프로젝트 메모리를 하나의 참고 문서로 편집하려는데 기존 계약은 section/key별 항목·버전과 entries API를 노출했다. 사용자에게 불필요한 항목 관리가 필요했고 문서 전체 편집·초기화 경계도 불명확했다.

공개 자원은 프로젝트당 하나의 Markdown `content`와 문서 전체 `version`으로 변경했다. GET/PUT/DELETE `/api/v1/projects/{project_id}/memory`로 조회·전체 수정·초기화한다. memory_id, entries 및 개별 항목 경로/스키마/설정은 제거했다. system_prompt, 세션 이력과 분석 결과는 각 역할을 유지한다. 저장은 기존 공식 LangGraph AsyncPostgresStore의 동일 namespace, 고정 key `document`를 사용하며 새로운 메모리 DB나 파일 원본을 만들지 않는다.

자동 갱신은 Agent middleware에서 현재 발언을 근거로 고정 제목의 섹션 본문을 교체한다. 보이지 않은 기존 섹션·중복 제목은 수정하지 않고 다른 부분의 문자열을 보존한다. 부분 수정도 문서 전체 버전 CAS를 적용한다. 초기화는 빈 문서에도 버전을 증가시켜 늦은 자동 저장을 차단한다. 동일 요청 receipt와 문서를 같은 transaction에서 저장하고 오래된 재생 응답은 현재 문서를 되돌리지 않는다. LLM 대기 중 DB 연결을 잡지 않는다.

기존 off/manual/auto_context 정책은 유지한다. 기본 manual이며 추가 모델 호출, 전체 문서 자동 요약·재작성, 분석 수치 자동 공유는 도입하지 않았다. 문서/Agent 부분 수정/모델 참조 예산은 별도 설정이다. 제거한 MAX_TOPICS/TOPIC_MAX_CHARS 설정은 조용히 무시하지 않고 오류로 안내한다. 현재 설정과 필드 설명은 [계약 문서](../project-memory.md)를 따른다.

## 기존 데이터와 배포 경계

Alembic `20261004_0028`은 기존 항목 본문을 제목별 Markdown으로 합치고 이전 출처/삭제 메타데이터를 보존한다. 항목 행과 이전 형식 receipt를 정리하고 무관한 Store namespace는 유지한다. 문서 버전은 이전 항목 버전의 합으로 시작한다. 0027 downgrade/upgrade에서도 문서 원문을 보존한다.

배포 전 이전 writer를 중지하고 폐기 설정을 제거한 뒤 `alembic -c alembic.crud.ini upgrade head`를 수행해야 한다. 이전/새 writer를 함께 쓰는 rolling 이행은 지원하지 않는다. 기존 로컬 DB·서비스 컨테이너에는 migration/재배포하지 않았다. 원래 checkout의 사용자 변경·.env도 보존했다.

## 검증 결과

- 격리 PostgreSQL 관련 검사: **91 passed, 0 skipped**. 공개 API, owner/비활성화, 전체 문서 CAS, 다른 섹션의 동시 변경, receipt 재생, 부분 실패 rollback, reset 뒤 stale Agent 차단, 설정 한도 축소, migration·역방향 이행, API→실제 Worker→LangGraph→공식 Store→SSE→새 세션 읽기를 확인했다. 모델은 HTTP 테스트 응답을 사용했다.
- 전체 애플리케이션 검사 `PYTHONPATH=src python -m pytest -q src`: **650 passed, 391 skipped**. 외부 서비스 설정이 없는 검사는 제외되며 위 PostgreSQL 검사는 별도 실행했다. 두 결과의 테스트 수는 중복되므로 합산하지 않는다.
- Markdown fence/중복 제목/자유 제목·대상 밖 원문 보존, 현재 원문·지속성·역할별 예산, prompt_json/provider_json_schema, 실제 create_agent middleware 경계를 확인했다.
- 필드 주석 생성기의 24개 파일·5,357개 주석·6개 inline block 검사와 JSON/JSONC 동일성 통과. Run/Workflow/SSO의 검증 계약은 변경하지 않았다.
- 깨끗한 staging에서 wheel을 빌드하고 isolated Python으로 `validate_agent_package.py` 통과: 공개 OpenAPI 37 paths, 역할 Agent 5개 생성, checkout 미참조, tests 미포함, 현재 workflow/state·역할별 prompt 자원 보존을 확인했다.
- 부하검사 HTTP 모델 fixture의 새 문서 응답이 실제 proposal/현재 원문 validator에 통과하는 것을 확인했다. 이번 작업에서 부하검사를 다시 수행한 것은 아니다.
- 변경 Python 파일 컴파일과 `git diff --check` 통과.

격리 자원은 PostgreSQL17/Redis7.4의 별도 localhost 포트·DB·컨테이너만 사용했다. 기존 18개 서비스와 실제 이벤트 그룹에는 합류하지 않았다. 검증 후 이 작업의 두 임시 컨테이너와 임시 접속 설정 파일을 제거했다. 기존 18개 컨테이너는 실행 상태를 유지했다.

## 실제 모델·성능·검사 범위의 한계

대상 섹션 안의 기존 의미를 모델이 빠뜨리지 않는지는 실제 LLM 정성 검증 대상이다. old_text 일치는 의미 보존의 증명이 아니다. 서버가 보장하는 범위는 다른 섹션 원문, 입력/소유권/버전/출처 검사, 원자 저장이다. 이번 작업은 실제 LLM·Executor 계산 부하·프로덕션 데이터를 사용한 시험이나 처리량 전후 측정이 아니며 속도 향상을 주장하지 않는다.

저장 namespace를 정확한 key로 읽지만 모델 입력은 예산 내 완전한 섹션만 선택한다. 긴 단일 섹션은 생략될 수 있으며 자동 잘림/요약으로 저장 문서를 변경하지 않는다. 자동 저장 충돌 시 추가 추론 없이 not_saved를 알린다.

저장소 최상위 pytest 수집은 benchmark/report에 있는 동명 모듈·standalone import 때문에 실패했다. 이를 본 작업과 무관하게 고치지 않았으며 전체 회귀 범위는 위의 `src` 명령이다. 배포 패키지 검증기의 오래된 state.py 삭제 단정은 현재 064 패키지 계약에 맞게 수정했다.
