# 008 — 역할별 Agent 선언과 독립 프롬프트

- 날짜: 2026-09-28
- 브랜치: feature/refactor-agent-builders
- 출발: 007 feature/refactor-remove-azure / 4021b96
- 상태: 구조 이행·오프라인 및 설치 패키지 검증 완료. 베이스 병합·push·배포 미수행.

## 문제와 사용자 결정

기존 components/specs.py, dependencies.py, prompts/에 역할별 설정이 흩어져 있었다. 프롬프트 일부는 Python 문자열이고 일부는 Markdown이어서 Agent 하나의 선언과 지침을 한곳에서 확인하기 어려웠다.

사용자는 역할별 agent_builders 패키지 안에 선언·전용 프롬프트를 모으고, 공용 도구는 공유 범위에 맞춰 밖에 두기로 했다. 프롬프트는 내용이 완전히 같아도 역할별 별도 파일로 유지한다. create_agent를 유지하는 목적은 LangChain/Deep Agents 미들웨어를 확장 지점으로 활용하는 것이다. 단일 호출이라는 이유로 create_agent를 제거하는 이전 제안은 채택하지 않는다.

## 실제 변경

- routing, intent_classifier, skill_selector, workflow_generator, conditional_decider, faq, report_writer 7개 역할에 agent.py / prompt.md / __init__.py를 배치했다.
- 각 패키지는 build_agent를 제공하고 dependencies.py는 공유 모델과 builder 결과를 연결한다. import 시 모델·DB 풀·Worker를 생성하지 않는다.
- 기존 Markdown 4개는 원본 바이트를 그대로 옮겼고 Python 상수 2개와 FAQ 인라인 문자열은 실제 문자열 값 그대로 파일로 추출했다. 전체 7개 프롬프트의 바이트 해시를 전후 대조했다. 공통 프롬프트 내용·상속·정규화는 추가하지 않았다.
- 기존 분류 label 지시문, JSON Schema 지시문, Skill 카탈로그 추가 방식은 유지했다.
- components/specs.py와 중앙 prompts의 이전 로더를 제거했다. 현재 활성 선언이 있는 역할 파일을 단일 기준으로 삼는다.
- specs에는 read_skill_documents가 등록되어 있었지만 실제 Workflow create_agent의 tools는 빈 목록이었다. 삭제된 선언을 근거로 도구를 새로 켜지 않았다. 실제 호출은 기존 generate_workflow 노드가 공용 tools/catalog.py를 통해 수행한다.
- 분석 공용 도구는 analysis/tools에 유지했다. 업무 간 실제 공유 도구가 아직 확인되지 않아 빈 agent_service/tools 패키지는 만들지 않았다. 여러 그래프 노드가 공유하는 스키마는 schemas에 유지한다.
- 실제 LLM이 없는 추천·파일 조회 placeholder를 Agent로 꾸미지 않았다. 미사용 추천 프롬프트는 docs/agent-development/reference-prompts로 옮겨 참고용으로 보존했다.
- package-data와 wheel 검증 스크립트 및 개발 가이드를 갱신했다.

## 구현하지 않은 범위

이번 작업은 합의한 구조의 첫 이행 단계다. Workflow 생성은 create_agent를 사용하고, 나머지는 기존 비동기 adapter를 사용한다. 모든 역할의 create_agent 통일·공통 factory/middleware·project_memory·State/Context/Store 연결은 다음 단계다. 미들웨어가 이미 작동한다고 설명하지 않는다. API/execution_service/integrations 전체 패키지 재배치, graph 분해, checkpoint 계약 변경도 수행하지 않았다.

## 검증

[검증 및 프롬프트 해시](../reports/agent-builders-layout-validation-2026-09-28.json).

- 변경 전/후 동일 명령으로 오프라인 회귀 실행: 각각 **193 passed / 19 failed / 39 skipped**. 실패 이름과 UUID를 정규화한 오류 메시지가 모두 동일하고 신규 실패 없음.
- 기존 Tool 소스 누락으로 수집 오류가 나는 test_select_features.py, test_split_dataset.py는 전후 동일하게 명시 제외했다. 조건부 DB 테스트 등 39개는 미실행이며 PostgreSQL 재검증으로 해석하지 않는다.
- 7개 프롬프트의 원본 내용과 소스·wheel 바이트 해시 일치.
- 깨끗한 임시 build 경로에서 wheel을 만들고 python -I로 격리 검증: 프롬프트 7개, 실제 production builder 조립, OpenAPI 33 paths, Mock 승인/재개/Executor 요청 6 steps 통과.
- 처음 로컬 build/를 재사용한 wheel에는 삭제된 이전 prompts 파일이 남아 검증이 실패했다. 별도 임시 build 경로에서 다시 빌드해 통과했다. 이전 캐시 오염을 피하는 방법을 개발 가이드에 기록했다.
- 삭제한 import/팩터리 참조 없음, git diff --check 통과. 외부 LLM·Executor·Redis 및 실제 DB를 호출하지 않았다.

```sh
PYTHONPATH=src python -m pytest src/app/test src/agent_service/agents/analysis/tests -q \
  --ignore=src/agent_service/agents/analysis/tests/test_select_features.py \
  --ignore=src/agent_service/agents/analysis/tests/test_split_dataset.py
python -I scripts/diagnostics/validate_agent_package.py <fresh-wheel-path>
```

## 다음 구현 전에 검토할 사항

1. Agent별 메모리 읽기·쓰기 정책과 확정 시점. aafter_agent가 끝났다고 전체 업무나 Executor가 성공한 것은 아니다. 승인된 입력·확정 결과를 갱신 근거로 삼는 방향을 권장하되 정책 확정은 다음 단계다.
2. 대화 이력과 요약 상태의 보존 범위. 현재 stateless 내부 호출에 SummarizationMiddleware만 붙여도 전체 세션 이력이 생기지 않는다. 부모 그래프·내부 Agent 간 전달/반영 규칙과 checkpoint 수명을 먼저 확정한다.
3. 내부 모델의 JSON Schema/tool calling 지원 범위. OpenAI 호환 URL이라는 이유만으로 모두 지원한다고 가정하지 않는다. provider별 지원 방식, 공통 미들웨어 순서·추가 LLM 호출 예산을 함께 검증한다.

실행 중 컨테이너와 원본 checkout은 변경하지 않았다. 동일 세션 잠금·WAITING_EXECUTOR 정책, 공개 API, 노드 ID/edge/state, 기존 DB 스키마는 이번 이동으로 바뀌지 않는다.
