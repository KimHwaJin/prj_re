# 053 프로젝트 메모리 길이·주제·역할·갱신 정책

| 항목 | 내용 |
|---|---|
| 상태 | 구현·검증·베이스 병합·origin 게시 완료 |
| 시작일 | 2026-10-02 |
| 브랜치 | feature/project-memory-policy |
| 출발 commit | effe70351e5656c402a168adffaf6089273613c2 |
| 배포 상태 | 미배포, 기존 Docker/.env 유지 |

## 문제와 변경 방향

052는 공식 LangGraph Store와 Agent runtime.store 연결을 완료했으나 길이 정책은 64 topic/16000자/항목 1000자/batch 4개로 고정돼 있었다. 역할마다 프로젝트 전체 JSON을 전달하면서 삭제 표시·출처·시각도 모델 입력에 들어갔다. 저장 용량이 곧 입력 용량이었고 auto_context는 현재 발언 원문을 그대로 저장하는 범위였다. 사용자는 항목 개수와 갱신 단위 설명 이후 길이·제공 범위·갱신 시점과 내용 정책을 반영하라고 요청했다.

수정은 기존 official Store·short transaction·버전/소유권/Worker claim/receipt를 유지하며 공통 middleware로 적용한다. 저장 한도와 입력 예산을 분리하고, 현재 원문에 근거한 지속적인 주제만 짧게 정리하여 항목별로 갱신한다. 파일/데이터/실행 결과를 자동 공유하지 않는다. 별도 요약 모델·주기 작업·자동 만료/삭제·Vector 검색을 추가하지 않는다.

## 실제 변경

- `service_contracts/project_memory.py`: immutable MemoryLimits와 절대 입력/조회 guard. 기본값은 유지하고 배포 설정으로 낮추거나 높일 수 있다.
- `agent_config.py`, `service_settings.py`, AgentContext/PlanningRuntime: 중앙 config > env > defaults에서 API 정책·모델 응답 schema·역할 미들웨어까지 동일한 한도 주입. 기동 후 불변 snapshot이며 재시작으로 반영.
- `agent_service/runtime/memory_selection.py`: 역할별 section, 현재 요청 단어/한국어 인접 두 글자 관련성, 안정적 우선순위. 주입 메시지 전체를 문자/UTF-8 byte 기반 추정 토큰 예산으로 제한한다. 정확한 모델 tokenizer/global context 보장을 주장하지 않는다.
- `ProjectMemoryMiddleware`: 한 번 읽은 full snapshot은 내부 버전 검증에 사용하고 모델에는 선택된 section/key/content/version만 제공. 삭제/출처/시각은 Store에 보존. 같은 호출의 retry는 같은 참조를 사용.
- `Conversation`: configured max_updates/topic_max_chars를 실제 출력 schema에도 반영. persistent project_context/preference_change/remember를 기존 응답에 포함하고 valid final response 이후 저장한다. 별도 메모리 LLM 호출 없음.
- `runtime/project_memory.py`: 현재 request의 exact quote·지속/임시 표현·source/section/version/숫자/경로/삭제/중복 검사. content는 짧게 정리 가능하며 quote와 intent를 latest source에 보존.
- API memory schema·snapshot/JSONC: 관리 API 경로/기존 요청 필드 유지, PUT content의 절대 guard 16000자로 표시하고 실제 배포 기본 한도 1000자는 서비스에서 검사. 읽기의 source.quote/source.intent는 선택적으로 추가되어 수동 항목의 응답은 그대로다. 나머지 documented Run/SSO/Workflow validation 계약은 변경하지 않았다.

## 정책과 경계

[현재 메모리 계약](../project-memory.md)에 설정별 기본값/범위/단위·역할 표·이행을 기록했다. config.yml에 설명된 주석 예시를 둔다. 환경변수만으로 바꾸려면 YAML의 해당 명시값을 생략한다.

한 항목은 section/key로 정해진 주제이며 64개는 채팅/세션/Run 수 제한이 아니다. batch 4개는 한 원자 저장의 서로 다른 주제 수다. 삭제 표시도 개수에 포함되며 관리 API로 최신 version을 사용해 명시적으로 복원할 수 있다. 같은 내용(공백 차이 포함)은 자동 재갱신하지 않는다. 같은 source 재전송은 같은 결과를 반환한다.

한도를 낮추면 기존 데이터를 숨기거나 삭제하지 않는다. 이미 성공한 동일 source/body 재송신은 현재 한도가 낮아져도 기존 receipt 결과를 반환한다. 새로운 저장만 현재 batch/본문 한도를 검사한다. 절대 guard 이내의 기존 문서는 관리 API로 읽을 수 있고 새 topic/용량 증가는 거절하되 기존 문서를 더 작게 만드는 수정은 허용한다. 조회도 절대 guard로 제한하여 대량 문서가 모델에 무제한 들어가지 않게 한다.

기본 manual을 유지한다. auto_context는 Conversation 응답의 최종 검증 후 저장하며 다른 역할/Executor 완료/주기 작업으로 자동 저장하지 않는다. “이번 보고서만” 등의 임시 요구는 세션 요청으로만 처리하고 지속적인 배경/선호나 명시 기억 요청만 공유한다. 현재 요청·원본 관찰이 메모리보다 우선하며 project system_prompt/실행 승인/검증된 근거와 구분한다.

prompt 예산 중 하나가 0이면 Agent 읽기/자동 갱신을 중단한다. 양수여도 안내문 자체가 들어가지 않으면 참조를 넣지 않고 자동 갱신을 허용하지 않는다. 큰 항목은 잘라서 의미를 바꾸지 않고 생략한 뒤 다른 항목을 검사한다. 생략 수는 역할 범위 안 활성 항목 기준이며 원본 삭제가 아니다.

짧은 정리의 의미 충실성·주제의 의미 중복·다국어 지속성 분류를 기계적으로 증명하지 않는다. prompt와 보수적인 표현 guard, 원문 출처/관리 API를 제공하지만 실제 업무 표현의 오분류/요약 오류 가능성은 남는다. 복잡한 의미 검색·자동 만료/이력/삭제 정리는 후순위다.

## 검증

- 최종 관련 89개 테스트 통과, 18.61초, 경고 9개. 초기 관련 88개 결과와 합산하지 않는다. 기존 Store/API/소유권/동시성/Worker claim/원자 rollback/0025 migration 회귀를 포함한다.
- 신규 정책 단위 19개(기본 YAML 로딩 포함): config 우선순위/잘못된 한도/한도 증가, 실제 reply schema, role 필터, 삭제·source 제외, 안내문 포함 문자/추정 token 한도, 관련성 우선, 0일 때 DB read/write 0, current quote/임시 요청/지속 요청 검사.
- 신규 실제 PostgreSQL 3개: batch 6개와 항목 1100자 설정 적용·원자성, tombstone count, 설정 하향 후 기존 읽기/점진 축소, 정리된 content와 exact quote/intent/source Run 저장·멱등 replay.
- API→queue Worker→actual create_agent→모델 대역→official Store/PG→SSE→다음 세션 회귀 유지. 모델 입력에는 source를 제거하고 실제 저장/관리 API에서는 source quote/Run을 확인.
- 실제 qwen38-27b-nvfp4 + official Store + 전용 localhost scratch PG: 저장, 이번 요청만 제외, 다른 세션 참조, 같은 key/개수의 version 갱신, 갱신 후 참조, manual 자동 갱신 차단 여섯 사례 통과. 각각 모델 1회, 4.451/3.547/3.485/4.224/3.393/3.217초. 인증 double/직접 Agent 호출이며 Worker/Executor/Phoenix 실제 전체 E2E나 성능 A/B는 아니다. Executor 제출 0, Store 재연결 후 보존 확인.
- 합성 32 topic fixture의 전체 참조 문서(옛 정책 안내 wrapper 제외)와 현재 전체 report 참조 메시지 크기를 [검증 결과 JSON](../reports/project-memory-policy-verification-2026-10-02.json)에 보존했다. 실제 모델 전후 latency/처리량 측정으로 해석하지 않는다.
- 현재 OpenAPI 38개 path, documented 12개 path/28개 schema를 현재 snapshot과 대조했다. 베이스와 비교한 validation 변경은 MemoryPut/MemorySource 두 schema뿐이고 경로 계약은 동일하다. JSON/JSONC의 값 동일성을 생성 도구로 확인했다.

**최종 전체 API·Agent 회귀 959개와 subtest 2개 통과**, 412.40초, warning 100개. 최종 소스를 별도 테스트와 겹치지 않게 단독 실행했다. wheel은 source checkout 없이 12개 역할/prompt·38개 OpenAPI path·mock 4단계 및 새 memory_selection 포함을 통과했다. 최초 전체 실행에서 주석만 있는 YAML agent 그룹이 null로 읽혀 실패한 것을 확인해 빈 객체로 수정했다. 기본 배포 YAML 로딩 검사도 추가하고 깨끗한 프로세스에서 최종 전체 회귀를 통과했다. 한도 재송신 보완 중 겹친 scratch DB 테스트 결과는 제외했으며, 최종 관련 89개와 전체 959개는 별도 프로세스에서 순차 실행한 결과다. checkpointer 없는 내부 graph의 기존 durability 경고를 전역 억제하지 않았다. 모델 호출 최적화와 외부 Executor Dataset Registry는 기존 보류를 유지한다.

## 적용·통합·게시

DB schema는 052의 0025 그대로이며 추가 migration은 없다. 최신 서비스 DB가 이미 0025면 코드/설정만 적용한다. 0025 이전이면 052의 API/Worker drain→Alembic upgrade head→새 코드 기동 순서를 따른다. 기존 source에 quote/intent가 없는 항목도 읽는다.

구현 commit: `aa3416b1845d72309deea0c873f82b25cbce6b29`. `feature/project-memory-policy`를 `feature/refactor-base`에 fast-forward 병합하고 두 브랜치를 origin에 atomic push했다. 원격 두 브랜치가 구현 SHA와 일치함을 확인했다. 이 게시 기록은 베이스의 후속 문서 commit으로 남기며 파생 브랜치는 구현 commit을 보존한다. 원래 사용자 checkout·.env는 변경하지 않았다.
