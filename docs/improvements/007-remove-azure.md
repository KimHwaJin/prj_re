# 007 — 불필요한 Azure 모델·설정 제거

- 날짜: 2026-09-28
- 기준: `feature/refactor-base` / `236d335`
- 작업 브랜치: `feature/refactor-remove-azure`
- 상태: 구현·관련 테스트 검증 완료. 베이스 병합·원격 push·배포 미수행.

## 문제와 범위

사용하지 않는 Azure 모델 지원 때문에 중앙 설정, API/Agent 설정 모델, 모델 팩터리와 비동기 HTTP 검증에 별도 분기를 유지하고 있었다. 사용자 요청으로 Azure 지원을 제거한다. 다른 환경변수의 재설계나 Executor/DB 비동기 전환은 이번 범위가 아니다.

## 변경

- AgentSettings의 Azure 전용 필드 4개 및 mapping 로딩 제거.
- API Settings의 llm_api_version 및 중앙 로더의 LLM_API_VERSION 별칭 제거.
- Azure endpoint 유무로 모델 provider를 선택하던 자동 감지 제거.
- MODEL_PROVIDER 허용값은 openai_compatible과 mock. 기본값은 openai_compatible.
- AzureChatOpenAI 생성 분기 및 .env.example의 Azure 예제 블록 제거.
- 기존 HTTP MockTransport 검증에서 Azure 5개 조합만 제거. OpenAI 호환 모델의 plain/prompt_json/provider_json_schema/nested_agent/cancel 검증은 유지.
- langchain-openai는 OpenAI 호환 모델에 필요하므로 유지한다. Azure 전용 직접 의존성은 없으며 의존성 및 lock 변경은 없다.

## 설정 전환

YAML의 삭제된 설정은 기존 strict 정책에 따라 unknown setting 오류다. 환경변수 및 명시적 로컬 dotenv의 미등록 키는 무시하고 자동 선택에 사용하지 않는다. MODEL_PROVIDER 또는 LLM_PROVIDER에 azure_openai를 지정하면 시작 오류다. 실제 모델용 MODEL_NAME/API_BASE_URL/MODEL_API_KEY와 Mock 설정은 유지한다. [설정 가이드](../configuration-bootstrap.md)를 갱신했다.

## 검증

```sh
PYTHONPATH=src python -m pytest \
  src/app/test/test_bootstrap_settings.py \
  src/agent_service/agents/analysis/tests/test_async_llm.py \
  src/agent_service/agents/analysis/tests/test_service_load_mock.py -q --tb=short
```

- 기존 Python 3.11 환경, 외부 LLM·Executor·DB 연결 없이 **54 passed**.
- 별도 로더 smoke 확인: 삭제된 YAML 키 5개 각각 거부, 동일 키의 환경변수는 무시, 기본 provider 유지, 삭제한 필드 및 설정 출처 목록 제거, config/env 각각에서 지원 종료 provider 거부.
- 소스·현재 환경변수 예제·YAML·스크립트·의존성/lock에서 Azure/LLM_API_VERSION 잔여 참조 없음.
- git diff --check 통과. 전체 회귀·PostgreSQL·부하테스트는 이번 변경에서 재실행하지 않았다. 006의 기존 실패 해소를 주장하지 않는다.

## 보존한 기록과 후속 작업

006의 문서 및 검증 JSON은 과거에 수행한 Azure 검증 사실을 보존한다. 006 문서에 지원 제거 후속 기록을 연결했다. 아키텍처 설계의 Microsoft Azure 문서 링크는 일반적인 큐 설계 참고자료이므로 유지한다. 원본 체크아웃의 로컬 설정 및 실행 중 컨테이너는 수정하지 않았다.

다음 구현 후보는 예정했던 Executor HTTP·Workflow DB 비동기 전환이다.
