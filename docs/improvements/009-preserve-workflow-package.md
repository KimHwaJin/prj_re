# 009 — 기존 app/workflow 유지보수 패키지 복원

- 날짜: 2026-09-28
- 브랜치: feature/refactor-preserve-workflow-package
- 출발: feature/refactor-agent-builders / ab814f4 (007·008 포함, 베이스는 006까지 통합)
- 상태: 구현·검증 완료. 베이스 병합·원격 push·배포 미수행.

## 사용자 결정과 문제

src/app/workflow는 기존 작업자가 계속 관리하는 핵심 업무 자산 영역이다. 005에서 설치 리소스와 개발 도구로 나눈 것은 필수 기술 제약이 아니라 설계 선택이었다. 사용자 요청으로 기존 패키지 전체를 원래 위치에 유지한다. 역할별 agent_builders는 그대로 유지하고 새 Agent가 원본 Workflow 패키지를 참조한다.

## 변경

- resources/skills → src/app/workflow/skills.
- resources/executor_tools → src/app/workflow/tools.
- skill_index.yaml과 tool_registry.yaml → 각각 원래 skills/tools 디렉터리.
- 인덱스·레지스트리 생성기 → 원래 skills/generate_skill_index.py 및 tools/generate_tool_registry.py. 이전 원본과 바이트 단위로 동일하게 복원하여 직접 python 파일 실행도 유지한다.
- workflow_lifecycle.md → 원래 workflows/ 디렉터리. 기존 docs 위치에는 링크 안내만 둔다.
- 원래 관리 파일 24개 모두 이동 전 7d0cc53 기준과 소스 및 설치 wheel의 바이트가 동일하다. tmp의 미등록 자산도 보존한다. 해당 문서에 적힌 모든 기능의 구현 완료를 뜻하지 않는다.
- app.workflow.paths가 설치 위치와 원본 경로를 소유한다. 분석의 resource_paths는 이를 재사용하는 얇은 경계이며 자산 사본이 없다.
- 새 Workflow가 기록하는 Tool/Skill 경로는 app/workflow/...로 복원한다. 005~008에서 사용한 agent_service/agents/analysis/resources/... 경로도 같은 파일로 연결한다. source 경로의 디렉터리 이탈 차단은 유지한다.
- Agent의 카탈로그 조회·Workflow 컴파일·데이터 로드 생성·관련 테스트를 새 원본 참조에 맞췄다. node ID/edge/state, 공개 API, 실행 중 DB 데이터는 변경하지 않았다.
- wheel에 원본 패키지 전체를 포함하고 이전 resources 복사본이 들어가지 않는 것을 검증한다. AgentBuilder 프롬프트는 별개 위치에 그대로 유지한다.

## 검증

[원본 24개 해시와 검증 결과](../reports/workflow-package-restoration-2026-09-28.json).

- 원본 24개 파일의 Git blob, 복원된 소스, 설치 wheel 바이트 모두 일치.
- 기존 generator 파일을 python으로 직접 실행해 임시 출력 생성: Skill 3개 / Tool 8개 등록, 원본 YAML과 내용 일치. tmp 제외는 기존 정책이다.
- 리소스·경로 호환·Mock 승인/재개 집중 테스트 10개 통과.
- 전체 오프라인 회귀 **193 passed / 19 failed / 39 skipped**. 008 기준과 실패 목록 및 UUID/경로 접두사 정규화 후 오류 메시지 동일, 신규 실패 없음.
- 기존 소스가 없는 select_features/split_dataset 수집 오류 2개는 동일하게 제외했다. PostgreSQL 조건부 테스트 등은 미실행이며 기존 업무 실패를 이번 복원으로 해결했다고 주장하지 않는다.
- 전체 회귀 첫 실행에서 이동된 Tool을 직접 import하던 test_mock_data_io 경로 누락을 발견했다. 관련 테스트 import를 app.workflow로 갱신한 뒤 재검증했다.
- 깨끗한 임시 build 경로의 wheel을 python -I로 검증: 원본 Workflow 패키지 존재, 임시 resource 사본 없음, 역할 프롬프트 7개/production builder 생성, OpenAPI 33개, Mock 승인·재개·Executor 요청 6 steps 통과.
- 실제 LLM·Executor·Redis·DB에 연결하지 않았고 원본 checkout 및 실행 중 컨테이너를 변경하지 않았다. git diff --check 통과.

## 유지보수 기준

[기존 작업자 안내](../../src/app/workflow/README.md)를 기준으로 계속 작업한다. 이후 API·Agent 패키지 분리에서도 이 패키지를 다시 옮기지 않는다. 모델이 직접 호출하는 LangChain 도구와 Executor용 원본 Tool은 다른 영역이다.

저장된 소스 경로의 호환은 과거 Tool 버전 전체의 보존을 의미하지 않는다. 실제 운영 DB의 모든 장기 실행 checkpoint를 재검증한 것은 아니며, 제출된 payload를 새 경로로 재생성하지 않는다. 기존 공유 PV 산출물 경로는 유지한다.
