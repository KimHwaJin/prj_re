# 077 세션 설정 명세·생성 시 커널 확정

상태: 구현·격리 검증 완료 / 베이스 미병합·미푸시·미배포.
브랜치: `feature/session-settings-contract`. 부모 `4f51ce2`(076 세션 이동 제거).

## 문제 → 변경

SessionCreate.settings는 자유 JSON을 받아 사용하지 않는 모델/실행 정책도 저장할 수 있었다. kernel_profile의 형식 검증은 Agent 실행 준비 단계에 있었고, 생략된 값은 저장되지 않아 서비스 기본값 변경의 영향을 받을 수 있었다. 세션 생성 시 검증·확정하고 Run별 설정과 분리한다는 사용자 승인에 따라 개선했다.

- 공통 `service_contracts.session_settings`에 kernel profile 문자열 제약과 typed SessionSettings를 선언했다. 세션 settings는 kernel_profile만 받는다. 최상위/설정의 알 수 없는 필드는 422다. 모델 선택·실행 mode·repair_level은 세션 설정에서 받지 않는다.
- SessionService의 공통 내부 생성 경계에서 생략/null profile을 중앙 snapshot의 EXECUTOR_RUNTIME_PROFILE로 확정하고 실제 문자열을 DB JSONB에 저장한다. 공개 POST와 편의 Message API 내부 생성에 모두 적용한다. malformed/unsupported settings를 받으면 세션 INSERT 전에 거절한다.
- 같은 Executor 설정 그룹에 EXECUTOR_RUNTIME_PROFILES의 JSON/YAML 허용 목록을 추가했다. 미설정 시 기본 profile 하나만 허용한다. default 포함·중복 없음·문자열 제약을 프로세스 시작 시 검사한다. config > env > 기본값의 기존 중앙 주입을 유지한다. 생성마다 원격 API를 호출하거나 LLM을 추가하지 않는다.
- Agent context는 저장된 profile을 전달한다. 읽기에서 생성 허용 목록을 다시 적용해 기존 profile을 바꾸지 않으며 승인 snapshot/Executor runtime.profile로 이어지는 기존 경로를 유지한다. 세션 이름 변경만 허용하고 settings 변경 PATCH는 422다. Run 모델 선택과 승인 화면 실행 정책은 기존 계약을 유지한다.
- 생성·조회·이름 변경의 settings 설명과 [세션 API 전체 필드 문서](../session-api.md), [Executor runtime 설정](../agentic-executor-runtime.md), config 예시와 배포 패키지 검사도 갱신했다.

## 검증

격리 PostgreSQL17의 별도 identity_test/agentic_runtime_test/agentic_checkpoint_test, localhost55551만 사용했다. 실제 LLM/Redis/Executor 호출은 없다. graph는 기존 mock 모델을 사용하며 Worker·DB·LangGraph checkpoint는 실제 실행했다.

- 관련 5개 모듈의 첫 검사: **118 passed, 3 setup errors**. 생성 유효성/허용 목록, 저장·조회, default/allowlist 변경 후 기존 profile 유지, 내부 Message 생성, 오래된 자유 JSON 보존, 삭제·접수 경합 등을 통과했다. setup error는 임시 검사 파일의 executor_submit_enabled와 fixture의 EXECUTOR_SUBMIT_ENABLED가 중복된 문제였고 서비스 구현 오류가 아니다.
- 임시 설정 중복 제거 후 planning 모듈 재검사: **3 passed**. 실제 API→Worker→HITL→graph 재시작→승인 snapshot에서 선택한 3102311을 보존했다. 기존 편집/승인/멱등/SSE와 답변/모델 거절도 통과했다. 이 결과를 한 번의 121 passed 출력으로 표시하지 않는다.
- 전체 `PYTHONPATH=src python -m pytest -q src`: **681 passed, 399 skipped, 74 warnings**. 외부 DB/서비스 설정이 필요한 검사는 위 결과와 별도이며 수를 합산하지 않는다. 기존 durability/no-checkpointer warning은 내부 역할 검사에서 유지됐다.
- 깨끗한 staging에서 wheel 빌드 후 isolated Python 패키지 검증: 현재 session_settings.py 포함, checkout 미참조, OpenAPI에서 SessionCreate/SessionSettings/SessionUpdate의 필드와 extra 금지, 37 paths·역할 Agent 5개 조립을 확인했다.
- 변경 Python 문법·생성 OpenAPI와 git diff --check를 확인했다. 부하테스트나 시간/처리량 전후 측정은 하지 않았다.

첫 로컬 어댑터 smoke의 json import 위치 문제는 smoke 중 발견·수정 후 단위/전체/PG 검사에 반영했다. 초기 임시 Postgres 준비 전 createdb는 실패했고 준비 확인 후 생성했다. 실제 검사는 준비된 전용 DB에서만 실행했다.

## 적용 범위·한계

077 자체의 DB 구조 migration은 없다. 새 세션부터 명시적인 설정을 저장하며 기존 세션 JSON을 조회/이름 변경만으로 지우지 않는다. 과거 profile 누락/null은 기존 fallback을 유지하므로 생성 시 고정 보장을 소급하지 않는다. 기존 잘못된 profile의 방어적 문법 검사는 유지한다. 기존 데이터 일괄 보정은 수행하지 않았다.

실제 커널 등록/가용성은 정적 허용 목록으로 보장되지 않는다. 배포의 Executor RUNTIME_ALLOWED_PROFILES와 Target/Jupyter 지원에 목록을 맞춰야 한다. 기존 미설정 기본 ml은 바꾸지 않았으므로 Executor가 default/3102311만 제공하면 config에서 default를 명시한다. 사용자별 커널 권한·커널 목록 API는 이번 범위가 아니다.

기존 서비스 DB/컨테이너·원래 checkout의 변경·.env는 수정하지 않았고, 임시 컨테이너/접속 설정은 검사 후 제거했다. 새 코드의 기존 target_project_id/자유 settings 요청은 422가 된다. 075와 같이 배포할 때 메모리 migration은 해당 절차를 따른다. 다음 리뷰는 세션 입력 가능 여부·현재 진행 Run의 응답 표현이다.
