# 100 설정 정의·소스 처리 구조 정리

2026-10-06. 브랜치: `feature/settings-model-cleanup`. 기준: `feature/refactor-base` / `4d9b051`와 선행 보안 키 수정 `0ac031e`.

## 문제

AgentSettings, 수동 AGENT_KEYS, 환경변수용 수동 파서에 필드 정의가 중복되어 있었다. GROUPS는 이미 합의에서 제거한 중첩 YAML을 유지했고 PLATFORM_ONLY_KEYS는 사용하지 않는 플랫폼 키를 알아야만 앱을 시작하게 했다. 이 때문에 IS_SECURITY_SERVICE 및 S3_FILE_URL_EXPIRES_IN 정상 철자가 기동을 막았다. YAML 타입을 문자열/JSON으로 바꾸고 다시 파싱하는 불필요한 과정도 있었다.

## 변경과 책임

- 모델 필드가 소비 키·별칭·타입·기본값·검증의 기준이다. AGENT_KEYS/EXTRA_KEYS/GROUPS/PLATFORM_ONLY_KEYS/ALIASES와 수동 Agent 파서 및 검색 SETTING_FIELDS를 제거했다.
- settings_sources는 소스 읽기와 선언된 필드 선택, runtime_settings는 앱 정책, settings_snapshot은 불변 객체와 진단 요약, settings_migrations는 폐기한 우리 설정의 오류 안내를 맡는다. service_settings는 소스 선택·우선순위·공통 값 조립과 프로세스 snapshot만 담당한다.
- native YAML 타입은 그대로 전달하고 환경변수의 컬렉션만 JSON으로 파싱한다. 설정 오류는 필드 이름만 노출한다.
- 공유 YAML의 미소비 키는 허용 목록 없이 건너뛴다. check-config에 unused_config_keys로 이름만 제공한다. 플랫폼 확장은 서비스 코드 변경을 요구하지 않는다. 우리 설정 오타도 이 목록에 나오므로 설정 점검에서 확인해야 한다. 타입·범위·별칭 충돌·중복 키 오류는 유지한다.
- 중첩 service/group은 제거하고 테스트·진단·벤치마크에서 만들던 YAML도 평탄하게 이전했다. 분석 그래프 테스트는 개인 config.yml이 없어도 격리된 snapshot으로 동작한다.
- 공개 예제의 두 플랫폼 키 철자를 바로잡았다. 기존 YAML의 플랫폼 값은 변경하지 않는다.
- 파일 선택과 YAML > env > 명시 local dotenv > 기본값, 공유 DB/Redis/Executor 대상·수명 정책, 공개 API와 DB schema는 유지한다. 개별 Executor REST 경로 계약 자체는 이번 로더 정리에서 변경하지 않는다.

## 사용·인수인계

[최종 설정 가이드](../application-configuration.md), [설정 분류표](../application-settings-inventory.md)를 따른다. 새 설정은 해당 모델의 필드를 선언한다. key 목록 또는 파서를 중복 수정하지 않는다. 로컬 파일은 config.yml, 배포는 선택된 config.dev/stg/prd.yml 하나다. 기동 전 `uv run python app.py --env local --check-config`의 unused_config_keys에서 우리 키 오타를 확인한다. 플랫폼 미소비 키 이름이 출력되는 것은 정상이다.

## 검증

- 관련 설정·전체 분석 Agent·HITL·Executor 모의 HTTP·SSO·메모리·수명·종료 회귀 **772 passed / 31.32초**, 실패·skip 없음. 기존 no-checkpointer durability 경고82개는 유지된다.
- local/dev/stg/prd 네 환경에서 공개 예제를 임시 폴더에 init한 뒤 app.py와 scripts/migrate.py --check-config 결과가 일치했다. 포트8000/5000과 실행 한도·이력 정책도 확인했다.
- uv build --wheel --offline 및 python -I scripts/diagnostics/validate_agent_package.py 통과. 체크아웃 import 없이 새 설정 모듈·5개 역할 builder·프롬프트·리소스·32개 API 경로와 mock 그래프를 확인했다. 테스트 fixture는 wheel에 포함되지 않는다.
- 중앙 모델 필드로 유도한 canonical key 수174개가 기존 명세와 같음을 확인했다.
- git diff --check 통과.

초기 검사에서 명시한 빈 kernel 목록/빈 Workflow DB 주소를 기본값으로 바꾸지 않도록 수정했다. 일부 회귀의 개인 config.yml 의존과 소문자 오류 필드 표기도 정리하여 최종 검사에 포함했다. 설정 검증은 연결 성공을 의미하지 않는다. 실제 컨테이너 재시작·DB migration·외부 모델 요청은 수행하지 않았다. 선행 099의 두 철자 호환 수정은 포함되지만 이제 전용 허용 목록 자체가 없어졌다.
