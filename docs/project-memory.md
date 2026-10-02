# 프로젝트 공유 메모리

`project_memory`는 한 프로젝트의 여러 세션에서 참고할 배경과 선호를 항목별로 보존한다. 현재 세션의 대화 이력·완료 분석의 관찰·프로젝트 `system_prompt`와 별개다. 저장소는 **공식 LangGraph `AsyncPostgresStore`**이며 서비스의 기존 `DATABASE_URL` PostgreSQL을 사용한다. `create_agent(store=...)`와 외부 LangGraph의 `compile(store=...)`에 같은 Store를 주입한다. 체크포인트 DB나 Redis·벡터 DB를 추가하지 않는다. 051의 전용 ORM 저장소는 052에서 제거했고, 길이·역할·갱신 정책은 053에서 추가했다.

## 사용하는 흐름

1. 소유한 프로젝트에 메모리를 명시적으로 등록하거나 `auto_context` 설정에서 현재 사용자 발언의 배경·선호를 추출한다.
2. API Run Worker 또는 Executor Event Worker가 역할 Agent를 호출할 때, 서비스가 공유 Store와 owner/project/session/source Run에 묶인 정책 객체를 주입한다. 미들웨어는 `runtime.store`를 이용한다.
3. 공통 `ProjectMemoryMiddleware.abefore_agent`가 역할 호출당 한 번 읽고 연결을 돌려준다. 역할/현재 요청에 맞는 제한된 참조를 만들며 모델 요청·JSON 정정 재시도에는 같은 snapshot/참조를 사용한다. prompt 한도 중 하나가 0이면 읽지 않는다.
4. 다른 세션의 다음 역할 호출은 최신 snapshot을 다시 읽는다. 공유 Agent 객체나 전역 변수에 프로젝트별 메모리를 캐시하지 않는다.
5. 메모리는 `HumanMessage` 참조 데이터로 주입한다. 승인·실행 명령·검증된 현재 분석 결과로 취급하지 않는다. 현재 요청과 원본 관찰이 우선한다.

프로젝트 소유자만 사용할 수 있고, 프로젝트/사용자가 비활성화되면 읽기·쓰기가 거절된다. Agent 접근은 source Run이 같은 user/project/session에 속하는지도 검사한다. 프로젝트 삭제가 기존 데이터 행을 즉시 물리 삭제한다는 뜻은 아니다.

## 항목과 저장 형식

| section | 의미 | 자동 추출 |
|---|---|---|
| background | 프로젝트 목표·업무 배경 | 현재 사용자 원문으로 뒷받침되는 짧은 주제 |
| analysis_preferences | 프로젝트 차원의 분석 관점·선호 | 현재 사용자 원문으로 뒷받침되는 짧은 주제 |
| report_preferences | 보고서 독자·구성·강조점·표현 선호 | 현재 사용자 원문으로 뒷받침되는 짧은 주제 |
| shared_findings | 사용자가 명시적으로 공유한 결과 메모 | 관리 API의 명시적 쓰기만 |

각 항목은 `(project_id, section, key)`로 식별하며 content, version, is_deleted, source, updated_at을 갖는다. key는 같은 주제를 수정할 안정적인 영문 키다. 기본값은 프로젝트 최대 64개 key(삭제 표시 포함), 출처를 포함한 문서 JSON 최대 16000자, 항목 본문 최대 1000자, 한 번에 최대 4개 갱신이다. 모두 설정할 수 있다. 자동으로 오래된 항목을 지우지 않는다. 한도에 도달하면 기존 항목을 짧게 수정하거나 삭제했던 키를 명시적으로 재사용한다.

공식 Store의 `store` 테이블에 항목과 삭제 버전을 JSON 문서로 저장한다. `store_migrations`는 공식 Store schema 이력이다. 애플리케이션의 ORM 모델이나 별도 BaseStore 구현은 없다.

| 문서 | namespace | key |
|---|---|---|
| 메모리 항목 | (`dtest`, `project_memory`, 내부 user UUID, project UUID, section) | 안정적인 주제 key |
| 중복 요청 기록 | (`dtest`, `project_memory_receipts`, 내부 user UUID, project UUID) | 출처 ID의 SHA-256 |

중복 요청 기록도 같은 공식 Store에 source_id·digest·결과를 저장한다. receipt namespace는 모델의 프로젝트 메모리 조회에 포함하지 않는다. namespace는 분리 표식이며 권한 검사 자체가 아니다. 서비스는 활성 사용자/프로젝트 소유권·source Run을 검사하고, 반환된 항목의 namespace와 JSON 내부 식별자도 검증한다. 별도 전체 변경 이력 테이블은 이번 범위에 포함하지 않았다. 삭제 표시와 receipt의 유지 기간·운영 정리는 후속이다.

## 설정

기본값은 `manual`이다. 자동 공유 범위가 아직 선택되지 않아 기존 명시적 공유 원칙을 기본으로 유지했다.

```yaml
service:
  agent:
    # off: Agent 읽기/자동 쓰기 비활성화. 명시적 관리 API 자체는 유지.
    # manual: 프로젝트 메모리를 읽되, 쓰기는 관리 API에서 명시적으로 공유.
    # auto_context: 지속적인 프로젝트 배경/선호 또는 명시적인 기억 요청만 갱신.
    # 어느 모드도 세션 데이터·수치·결론을 자동으로 공유하지 않음.
    agent_project_memory_mode: manual
    agent_project_memory_max_topics: 64         # 삭제 표시도 포함. 1..1024.
    agent_project_memory_max_chars: 16000      # 출처 포함 저장 JSON. 1024..1000000.
    agent_project_memory_topic_max_chars: 1000 # 본문 및 자동 원문 인용. 1..16000.
    agent_project_memory_max_updates: 4        # 한 원자 batch. 1..32, max_topics 이하.
    agent_project_memory_prompt_max_chars: 6000 # 참조 메시지 전체. 0..1000000.
    agent_project_memory_prompt_max_tokens: 4096 # 보수적 추정 토큰 예산. 0..1000000.
```

환경변수는 위 설정 키를 대문자로 바꾼 이름이다. 예: `AGENT_PROJECT_MEMORY_MAX_TOPICS`, `AGENT_PROJECT_MEMORY_PROMPT_MAX_TOKENS`. 중앙 resolver의 config 명시값 > env > 기본값 순서와 false/0 보존 규칙을 유지한다. YAML에 manual을 명시하면 환경변수 auto_context가 이를 덮어쓰지 못한다. 모드를 변경한 배포 프로세스는 재시작해야 한다.

명시적으로 입력한 `shared_findings`는 사용자 기록이다. 자동으로 검증된 Executor 결과나 재사용 가능한 데이터 파일로 승격되지 않는다. 원본 Run/Executor 결과 확인과 Dataset Registry를 대신하지 않는다.

## 명시적으로 공유하고 수정하는 API

기본 prefix는 `/api/v1`이다. 기존 SSO 로그인 쿠키와 쓰기 CSRF 헤더, 프로젝트 소유권 검사를 사용한다. body에 user_id를 넣지 않는다.

| Method | 경로 | 동작 |
|---|---|---|
| GET | /projects/{project_id}/memory | 항목·버전·출처와 삭제 표시 조회 |
| PUT | /projects/{project_id}/memory/{section}/{key} | 새 항목 생성·기존 항목 수정·명시적 복원 |
| DELETE | /projects/{project_id}/memory/{section}/{key}?expected_version=현재버전 | 항목 삭제 표시 |

PUT 예시:

```jsonc
{
  // 프로젝트 전체에 명시적으로 공유할 항목 본문.
  "content": "보고서는 비전문가가 이해할 수 있도록 원인과 다음 행동을 중심으로 작성한다.",
  // 새 key는 0. 수정/복원은 GET에서 받은 최신 항목 version.
  "expected_version": 0
}
```

section은 위 표의 네 값이고, key는 영문 소문자로 시작하는 `[a-z][a-z0-9_]*`, 최대 48자다. PUT/DELETE의 선택적 `Idempotency-Key`는 최대 100자다. 같은 key로 같은 요청을 다시 보내면 당시 결과를 반환하고 버전을 또 올리지 않는다. 같은 Idempotency-Key로 다른 내용을 보내면 409다.

조회 예시:

```jsonc
{
  "schema_version": 1,
  "user_id": "00000000-0000-4000-8000-000000000001", // 인증된 소유자의 내부 UUID.
  "project_id": "00000000-0000-4000-8000-000000000002", // 공유 범위인 프로젝트 UUID.
  "entries": [
    {
      "section": "report_preferences", // 정보의 역할.
      "key": "style", // 이 역할 내 안정적인 주제 키.
      "content": "원인과 다음 행동 중심으로 간결하게 작성한다.", // 공유 본문.
      "version": 1, // 수정·삭제·복원마다 증가하는 항목 버전.
      "is_deleted": false, // true 항목은 사용할 지식이 아닌 삭제 버전 표시.
      "source": {"kind": "user_edit"}, // API 명시 편집. 자동 추출은 user_request와 Run/Session ID.
      "updated_at": "2026-10-02T00:00:00+00:00" // 마지막 갱신 UTC 시각.
    }
  ]
}
```

다른 사용자의 프로젝트·비활성 프로젝트는 404, 오래된 버전/멱등성 충돌은 409, 형식·용량 초과는 422다. 삭제된 key도 버전을 유지하므로 오래된 화면에서 expected_version=0으로 되살릴 수 없다. 복원은 최신 삭제 버전을 명시해야 한다.

## 모델 입력에 제공하는 범위

저장 용량과 모델 입력 용량은 별개다. 삭제 표시, 원문 quote, source Run/Session ID, updated_at은 관리 API/Store에 보존하고 모델 메모리 메시지에서는 제외한다. 다음 역할별 범위에서 현재 요청과 겹치는 단어(한국어는 인접 두 글자도 사용)를 우선하고 역할 분류 순서·항목 버전·key로 안정적으로 정렬한다. Vector/추가 LLM 검색은 없다. 이는 의미 검색이 아닌 결정적인 관련성 휴리스틱이다.

| 역할 | 제공하는 분류 |
|---|---|
| Conversation | 배경·분석 선호·보고서 선호·명시적 공유 메모 |
| 계획 재작성 | 배경·분석 선호 |
| 실행 결과 판단 | 배경·분석 선호·명시적 공유 메모 |
| 보고서 작성 | 보고서 선호·배경·명시적 공유 메모 |
| 오류 수정 | 배경·분석 선호 |

공통 factory의 역할 이름이 위 다섯 역할에 해당하면 이 범위를 적용한다. 신규/legacy 역할은 네 분류를 기본으로 제공하므로 역할 추가 시 `memory_selection.ROLE_SECTIONS` 또는 명시적 미들웨어 role을 검토한다. 역할 필터는 권한 검사가 아니며 소유권은 서비스 정책이 검사한다.

참조 메시지의 안내문·식별자·선택 정보·쓰기 정책까지 포함하여 문자 예산과 추정 토큰 예산을 모두 검사한다. 토큰은 폐쇄망 모델 tokenizer를 추가 조회하지 않고 UTF-8 byte 수로 보수적으로 추정한다. **정확한 모델 token count나 전체 LLM 입력 예산의 보장이 아니다.** 모델 tokenizer를 사용할 때 선택 함수의 counter를 교체할 수 있다. 한 항목이 너무 크면 자르지 않고 그 항목을 생략한 뒤 다른 항목을 검사한다. selection.omitted_topics는 역할 범위 안의 활성 항목 중 생략한 수다. 전체 문서 소실/삭제로 해석하지 않는다.

두 prompt 한도 중 하나가 0이면 Agent의 읽기·자동 갱신을 중단하지만 명시적 관리 API는 유지한다. 0이 아니어도 안내문이 예산에 들어가지 않으면 메모리 메시지를 넣지 않고 자동 갱신도 허용하지 않는다. 기동 시 한도를 낮췄어도 절대 입력/조회 guard 이내의 기존 메모리는 관리 API로 읽을 수 있다. 설정을 넘는 기존 문서는 새 항목/용량 증가를 거절하며 기존 항목을 더 짧게 줄이는 수정은 허용한다. tombstone은 자동 정리하지 않는다.

## 갱신 시점과 내용 정책

기본 manual은 관리 API의 명시적 편집만 저장한다. auto_context에서는 **기존 Conversation create_agent 응답**에 갱신안을 포함하고, 검증된 최종 응답 이후 aafter_agent가 저장한다. 별도 주기 작업이나 메모리 전용 LLM을 추가하지 않는다. 다른 역할 Agent와 Executor 완료 이벤트는 메모리를 자동 갱신하지 않는다. 다음 역할 호출부터 새 항목을 읽고, 이미 실행 중인 호출의 snapshot은 바꾸지 않는다.

허용하는 것은 지속적인 프로젝트 배경, 분석/보고서 선호, 명시적인 기억 요청이다. content는 원문의 의미를 유지한 짧은 주제로 정리할 수 있고 source.quote에는 현재 사용자 발언의 정확한 부분을, source.intent에는 project_context/preference_change/remember를 보존한다. 원문 이력은 최신 항목의 근거이며 전체 변경 이력 저장소는 아니다. 예: “앞으로 보고서는 비전문가 대상으로 쉽게 작성해줘”를 “보고서 독자는 비전문가이며 쉬운 표현을 사용한다”로 정리한다.

“이번 보고서만 짧게”·“이번 분석에서는 이상치를 빼줘” 같은 임시 요구는 현재 세션 요청으로만 처리한다. 모델 지침과 보수적인 한국어/영어 지속·임시 표현 검사로 이 범위를 제한한다. 원문 인용이 현재 request에 실제 존재하는지 확인하며 history·파일·모델 응답으로 새 원문을 만들지 않는다. 숫자 문장, URL, 대표 파일 경로/확장자, shared_findings 자동 쓰기와 삭제 항목 자동 복원은 거절한다. 분석 결과·수치·데이터는 기존 명시적 공유 API만 허용하며 검증된 실행 근거를 대신하지 않는다.

**자연어 의미나 요약의 충실성을 기계적으로 증명하는 것은 아니다.** 다국어·새로운 표현의 분류나 짧은 정리에서 오류가 남을 수 있다. default manual을 유지하고 원문 출처/관리 API로 확인·수정할 수 있게 한다. 운영 auto_context 적용 전 실제 업무 표현을 추가 평가해야 한다.

같은 주제는 기존 section/key와 version을 사용하고 변경된 항목만 갱신한다. 동일 원문을 재송신했을 때 receipt로 중복 저장을 막고, 내용이 같은(공백 차이 포함) 항목은 재갱신하지 않는다. 모델에게 기존 키 재사용을 지시하지만 서로 다른 표현의 주제를 의미적으로 완전히 중복 제거하지는 않는다. 충돌 시 기존 항목을 임의로 덮어쓰지 않는다.

갱신 결과는 public activity.started/completed의 project_memory 활동과 답변 Run 결과의 final_response.project_memory에서 확인한다. 내부 memory_updates·quote·전체 메모리는 SSE에 공개하지 않는다. Store의 사용·원자 쓰기·Worker 점유 검사와 삭제/복원 동작은 052와 같다.

처리 중 DB 연결은 모델 대기 동안 유지하지 않는다. 공식 Store는 psycopg를 사용하며 기존 CRUD 엔진은 asyncpg를 사용하므로 풀을 직접 공유하지 않는다. API 프로세스당 하나의 Store runtime이 lazy psycopg 풀을 소유한다. 최소 연결 0, 최대 `min(2, DATABASE_POOL_SIZE)`, 대기 timeout은 `DATABASE_POOL_TIMEOUT_SECONDS`다. 새 URL/별도 환경변수는 없으며 **Pod DB 예산에는 CRUD 풀·checkpoint 풀·bridge 풀과 이 최대 2개 연결을 각각 합산**해야 한다. Store 자체의 TTL/자동 만료와 embedding/벡터 검색은 켜지 않는다.

쓰기 트랜잭션에서는 공식 `AsyncPostgresStore(connection)`과 공개 `abatch()`를 사용한다. 항목과 receipt가 동일한 psycopg 트랜잭션에 커밋된다. 기존 서비스 DB 세션이 사용자·프로젝트·Task 점유 barrier를 저장 완료까지 유지해 삭제나 오래된 Worker와 경쟁하지 않는다. 다른 driver 세션 두 개의 분산 쓰기를 만드는 것이 아니다. CRUD 세션은 권한/lock만 담당하고 메모리 변경은 Store 트랜잭션 하나에서만 수행한다. 자동 추출의 추가 출력 계약이 기존 모델 응답 길이나 재검증에 미치는 일반적인 비용은 이번 기능 검증으로 확정하지 않았다. 모델 호출 횟수 최적화는 [후속 목록](improvements/backlog.md)에 보류되어 있다.

## 반영과 검증

코드 적용 환경의 서비스 DB에 먼저 CRUD Alembic migration을 실행한다. 애플리케이션 기동에서 자동 schema 생성·데이터 backfill을 수행하지 않는다.

```sh
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head
```

새 head는 `20261002_0025`다. pinned `langgraph-checkpoint-postgres==3.1.2`의 기본 Store schema 0..3을 Alembic에서 준비한다. `store.setup()`을 기동 중 호출하지 않는다. 실제 메모리와 receipt를 0024 테이블에서 공식 Store로 이전한 뒤 `project_memories`, `project_memory_receipts`를 제거한다. 기존 content·version·삭제 표시·출처·갱신 시각·동일 요청 결과를 보존한다. 새 환경도 Alembic head 하나로 준비한다. 0024 파일은 이미 게시된 migration 이력이므로 삭제하지 않는다.

0025→0024 downgrade는 이 프로젝트 메모리 namespace를 이전 테이블로 복원하며 다른 Store 문서/공식 테이블은 유지한다. 더 이전의 0024→0023은 기존 migration에 따라 메모리를 삭제한다. 기존 서비스와 checkpoint 이력은 변경하지 않는다. 배포 중 옛 코드와 새 코드가 서로 다른 저장소에 쓰지 않도록 관련 API/Worker를 중지·drain한 뒤 migration과 새 버전 배포를 완료한다. 혼합 버전 동시 기동은 지원하지 않는다.

`api_service/core/memory_store.py`는 풀 수명, `service_contracts/memory_store.py`는 namespace·JSON 읽기, `api_service/services/project_memory_policy.py`는 소유권·버전·동시성·출처·receipt 정책을 담당한다. 제거된 `project_memory_service.py`/전용 모델의 호환 shim은 없다.

테스트와 실제 모델 확인의 범위는 [053 메모리 정책 기록](improvements/053-project-memory-policy.md)을 참고한다. 이 변경으로 기존 Docker 컨테이너나 운영 DB를 재기동·마이그레이션하지 않았다. 장기 실행 종료 후 새로운 파일/수치 근거 연결, 프로젝트 간 공유, 프로젝트 협업 권한, 자동 결과 요약은 별도 범위다.
