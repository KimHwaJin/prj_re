# 098 내부 템플릿 설정 계약 통합

브랜치: feature/platform-config-contract. 기준: feature/refactor-base / 07f8c9d. 2026-10-06.

## 문제와 합의

097은 config.yml을 공통 정책, config.dev/stg/prd.yml을 연결 overlay로 만들었다. 실제 내부 템플릿은 배포 env로 파일 하나를 선택하며 로컬은 config.yml을 원한다. 같은 의미의 PORT/모델 키를 다시 정의했고 대화 메시지40과 내부 graph32가 템플릿의 턴6/graph100과 불일치했다. 플랫폼 Phoenix 초기화와 우리 register/shutdown이 중복될 수 있었다.

## 최종 변경

- 기본 local=config.yml, dev/stg/prd는 해당 파일만 읽는다. explicit file도 하나만 읽으며 누락은 오류다.
- 최상위 대문자 YAML에 템플릿 PORT/PRIVATE_LLM_*/RECURSION_LIMIT/ACTIVE_MULTI_TURN/SET_MAX_HISTORY/ACTIVE_TRACE/Phoenix를 재사용한다. 플랫폼 전용 키는 보존한다.
- config.service.example.yml에 서비스 추가 블록만 제공하고 각 환경 example은 전체 독립 설정으로 만든다. config.yml도 credential 파일 역할이므로 Git/build context에서는 실제 파일을 제외한다.
- history는 public Run을 턴 identity로 묶어 이전 N턴+현재 Run을 보존한다. HITL 수정은 같은 턴이며 false/0에서도 현재 피드백·승인·checkpoint를 유지한다. 기존 checkpoint의 role/content 이력도 읽는다.
- 모든 서비스 graph 실행 경로와 내부 RoleAgent에 configured recursion budget을 전달한다. 별도 업무상 repair/replan/operation 한도는 유지한다.
- standalone만 Phoenix exporter를 소유하고 플랫폼 app 부착은 provider를 만들거나 종료하지 않는다.
- init/import/export·migrate·Compose·Docker·Kubernetes 예제를 단일 파일 계약으로 갱신한다. CICD의 변경 불가 env 및 한 컨테이너 구조는 유지한다.

## 인수인계

설정 선택/병합/검증은 src/service_settings.py, private 파일 생성은 src/service_runtime/configuration_files.py, 턴 이력은 src/agent_service/runtime/conversation_history.py, 수명은 src/service_bootstrap.py가 담당한다. [최종 사용법](../application-configuration.md), [재사용/추가/제거 추적표](../application-settings-inventory.md)를 기준으로 한다. 공개 Run/Executor API와 DB schema 변경은 없다.

## 검증

- 설정/파일 생성·우선순위·reserved 키/중복·비밀값 비노출, 전체 분석 Agent·HITL·기존 checkpoint 읽기, 모델 선택·SSO·콘솔, 모의 Executor HTTP 및 실제 SIGTERM 종료 회귀 **740 passed / 30.92초**.
- 경고82개는 기존 stateless create_agent의 no-checkpointer durability 경고다. 실패·skip은 없다.
- local/dev/stg/prd 예제를 임시 폴더에서 각각 초기화하여 app.py와 scripts/migrate.py --check-config 결과가 동일함을 확인했다. 포트는 local8000, profile5000이다.
- compose.local.yaml과 compose.external.yaml의 docker compose config --quiet 통과. 실제 컨테이너 실행은 하지 않았다.
- uv build --wheel --offline 및 python -I scripts/diagnostics/validate_agent_package.py 통과. checkout import 없이 새 모듈·5개 role builder·프롬프트/리소스·32개 API path·실제 mock 그래프를 확인했다.
- MODEL_CATALOG 선택 예제도 같은 중앙 loader로 별도 검증했다.
- git diff --check 통과.

초기 회귀 과정에서 로컬 이름을 dev로 기대하던 SSO 테스트와 개발자 YAML이 진단 테스트 env를 덮어쓰는 격리 문제를 수정했다. 이전 checkpoint 이력에 현재 Run ID를 연결하는 과정에서는 apply_review의 제한된 입력 스키마에 user_request를 명시해야 했으며, 740개 최종 검사에 수정 사항을 포함했다. 실제 사내 SDK·collector·배포 인프라는 외부에서 확인할 수 없으며 계약/모의 lifecycle 검증과 실제 배포 검증을 구분한다. 기존 컨테이너 재시작·실제 DB migration·외부 모델 요청은 수행하지 않는다.

## 통합·게시

2026-10-06 사용자 요청으로 구현 commit `ee7332e`를 `feature/refactor-base`에 fast-forward 병합했다. 최신 origin 베이스가 분기 기준 `07f8c9d`와 같음을 확인했고 충돌이나 추가 코드 변경은 없었다. 베이스와 `feature/platform-config-contract`를 `https://github.com/KimHwaJin/prj_re.git`의 origin에 atomic push했다. 파생 브랜치는 보존하며 게시 기록은 베이스의 후속 문서 커밋으로 남긴다. 실제 배포·컨테이너 재시작·DB migration은 수행하지 않았다.
