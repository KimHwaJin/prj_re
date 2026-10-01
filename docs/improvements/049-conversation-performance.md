# 049. 후속 답변의 메타데이터·계획 문맥 비용 축소

작업 브랜치: `feature/conversation-performance`

출발 commit: `59eec1f66f04bb63c21abd6720b0d03d76c19b0d`

작업일: 2026-10-02

## 문제와 결과

단순 설명·보고서 문구 수정에도 전체 Workflow 계약을 주고 Skill 메타데이터 조회를 허용해 추가 모델 호출과 큰 입력이 발생했다. 같은 create_agent 루프의 첫 응답에서 답변 또는 등록 Skill 선택으로 나누고, 선택 후에만 계획 계약을 제공하도록 바꿨다. 답변 요청에 새 모델 분류 호출을 추가하지 않았다.

동일 입력 실모델 A/B에서 설명은 누적 입력 44.7% 감소·메타데이터 호출 2→0, 시간은 52.96→58.46초로 증가했다. 보고서 수정은 입력 71.3% 감소·호출 5→2, 시간은 102.06→60.68초로 감소했다. 전체 설명 지연 해결이나 서비스 처리량 증가를 주장하지 않는다. [상세 결과·한계](../reports/conversation-performance-2026-10-02.md)와 [8회 측정 JSON](../reports/conversation-performance-2026-10-02.json)을 참고한다.

## 변경 위치와 동작

- `conversation/prompt.md`는 첫 판단·근거 답변 지침, 같은 역할의 `planning_prompt.md`는 상세 계획 계약이다. 기존 단일 create_agent 선언을 유지한다.
- 내부 Reply에 `kind=planning`, `skill_ids`를 추가했다. 등록 Skill만 고를 수 있으며 최종 응답은 answer/plans다. 이 내부 단계는 REST/SSE 공개 타입이 아니다.
- 공용 `PlanningContractMiddleware`는 첫 모델 요청의 도구 binding을 비우고 유효 Skill 선택을 표준 ToolNode의 read_skill로 연결한다. 첫 단계의 무단 tool call은 실행하지 않는다. 조회가 끝난 invocation 메시지에서만 상세 Workflow Schema·도구를 제공한다. 가변 공유 phase 상태가 없어 캐시된 Agent의 다른 세션에 계획 문맥을 넘기지 않는다.
- 기존 discovery 횟수 상한, Workflow/input/repair 정책, data_reference, source Run·owner·수치 검증과 실행 승인을 유지한다. ProviderStrategy의 기존 Pydantic fail-fast 동작도 유지한다.
- wheel package-data를 역할 폴더의 `*.md`로 확장하고 설치 검증에서 상세 prompt와 middleware 포함을 확인한다.
- 전체 회귀에서 048의 run_id 통일에 맞지 않은 loadtest 테스트 fixture 한 곳을 발견해 공개 `run_id`로 정정했다. 운영 코드는 해당 결함 수정 범위에 포함하지 않는다.

새 환경변수·DB migration·공개 API 변경은 없다. 공개 OpenAPI 36개 path의 정규화 SHA256은 변경 전·후 `cc6f1d371e93f77a91a28463daeaaf0932213cb67ab0a0d08999323af0beb80b`로 같다.

## 검증

핵심 Agent/grounding/planning 관련 73개 통과. API·Agent 전체 최종 회귀 **890개 + subtest 2개가 통과**했다(391.40초). 기존 checkpointer 없는 mock 호출의 durability warning 88개가 있으며 새로운 실패는 없다. 실제 신규 계획 smoke는 31.86초에 등록 Skill 선택→상세 계약→4개 Tool(data_load/profile_data/compute_statistics/detect_outliers)의 유효 계획 한 개를 생성했다. 추가 search_tools 조회 3회가 있었으며 신규 계획 속도 개선을 주장하지 않는다. Executor 제출은 0건이다. 최종 wheel은 source checkout 없이 12개 역할 선언과 prompt, API 36개 path, mock graph 4단계, 상세 계획 prompt 포함을 검증했다. 설치 의존성은 변경하지 않았다.

실모델 A/B 8회 모두 마지막 답변의 근거 검증을 통과했다. 조건별 두 회이며 공유 모델 서버 부하는 통제하지 못했다. 변경 후 설명은 수치 본문 작성에 따른 정정 두 번이 남아 지연이 줄지 않았다. grounding 한계를 통계적 정당성·문장 의미의 정답 보장으로 해석하지 않는다.

## 다음 범위

후속 답변 출력 계약을 단순화해 수치 정정 재호출을 줄이는 성능 작업이 우선이다. 새로운 Workflow 추천, Dataset API, project_memory, SSO 내부 SDK 연결과 에러/운영성 확장은 이번 항목에 넣지 않았다. 기존 로컬 checkout과 Docker 환경은 유지했다.

## 통합·게시

구현·최종 검증을 완료했다. 베이스 병합과 origin 게시 SHA는 게시 완료 후 기록한다.
