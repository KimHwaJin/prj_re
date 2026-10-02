# 프로젝트 공유 메모리

`project_memory`는 한 프로젝트의 여러 세션에서 참고할 배경과 선호를 항목별로 보존한다. 현재 세션의 대화 이력·완료 분석의 관찰·프로젝트 `system_prompt`와 별개다. 저장소는 서비스의 기존 `DATABASE_URL` PostgreSQL과 SQLAlchemy 풀을 사용한다. LangGraph checkpoint DB나 새 Redis namespace·벡터 DB를 추가하지 않는다.

## 사용하는 흐름

1. 소유한 프로젝트에 메모리를 명시적으로 등록하거나 `auto_context` 설정에서 현재 사용자 발언의 배경·선호를 추출한다.
2. API Run Worker 또는 Executor Event Worker가 역할 Agent를 호출할 때, 서비스가 owner/project/session/source Run에 묶인 접근 객체를 주입한다.
3. 공통 `ProjectMemoryMiddleware.abefore_agent`가 역할 호출당 한 번 읽고 연결을 돌려준다. 모델 요청·JSON 정정 재시도에는 같은 snapshot을 사용한다.
4. 다른 세션의 다음 역할 호출은 최신 snapshot을 다시 읽는다. 공유 Agent 객체나 전역 변수에 프로젝트별 메모리를 캐시하지 않는다.
5. 메모리는 `HumanMessage` 참조 데이터로 주입한다. 승인·실행 명령·검증된 현재 분석 결과로 취급하지 않는다. 현재 요청과 원본 관찰이 우선한다.

프로젝트 소유자만 사용할 수 있고, 프로젝트/사용자가 비활성화되면 읽기·쓰기가 거절된다. Agent 접근은 source Run이 같은 user/project/session에 속하는지도 검사한다. 프로젝트 삭제가 기존 데이터 행을 즉시 물리 삭제한다는 뜻은 아니다.

## 항목과 저장 형식

| section | 의미 | 자동 추출 |
|---|---|---|
| background | 프로젝트 목표·업무 배경 | auto_context에서 현재 사용자 원문만 |
| analysis_preferences | 프로젝트 차원의 분석 관점·선호 | auto_context에서 현재 사용자 원문만 |
| report_preferences | 보고서 독자·구성·강조점·표현 선호 | auto_context에서 현재 사용자 원문만 |
| shared_findings | 사용자가 명시적으로 공유한 결과 메모 | 관리 API의 명시적 쓰기만 |

각 항목은 `(project_id, section, key)`로 식별하며 content, version, is_deleted, source, updated_at을 갖는다. key는 같은 주제를 수정할 안정적인 영문 키다. 한 프로젝트 최대 64개 key(삭제 표시 포함), 문서 JSON 최대 16000자, 항목 본문 최대 1000자다. 자동으로 오래된 항목을 지우지 않는다. 한도에 도달하면 기존 항목을 짧게 수정하거나 삭제했던 키를 명시적으로 재사용한다.

`project_memories`는 현재 항목과 삭제 버전을 저장한다. `project_memory_receipts`는 쓰기 출처별 digest와 결과를 저장해 동일 요청의 중복 적용을 막는다. 별도 전체 변경 이력 테이블은 이번 범위에 포함하지 않았다. 삭제 표시와 receipt의 유지 기간·운영 정리는 후속이다.

## 설정

기본값은 `manual`이다. 자동 공유 범위가 아직 선택되지 않아 기존 명시적 공유 원칙을 기본으로 유지했다.

```yaml
service:
  agent:
    # off: Agent 읽기/자동 쓰기 비활성화. 명시적 관리 API 자체는 유지.
    # manual: 프로젝트 메모리를 읽되, 쓰기는 관리 API에서 명시적으로 공유.
    # auto_context: 현재 사용자 발언에서 프로젝트 배경/선호를 항목별 추출.
    # 어느 모드도 세션 데이터·수치·결론을 자동으로 공유하지 않음.
    agent_project_memory_mode: manual
```

동일한 환경변수는 `AGENT_PROJECT_MEMORY_MODE`다. 중앙 resolver의 config 명시값 > env > 기본값 순서와 false/0 보존 규칙을 유지한다. YAML에 manual을 명시하면 환경변수 auto_context가 이를 덮어쓰지 못한다. 모드를 변경한 배포 프로세스는 재시작해야 한다.

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

## 자동 추출과 실제 저장 결과

`auto_context`에서 Conversation의 기존 create_agent 응답에 내부 `memory_updates`를 포함한다. 별도 추출 LLM을 호출하지 않는다. 현재 사용자 request에 실제 존재하는 원문 quote와 동일한 content만 허용하고, 기존 항목 버전을 검증한다. history·모델 해석·관찰 결과·파일 내용으로 메모리를 새로 작성하지 않는다. 숫자 문장, URL, 대표적인 파일 경로/확장자, shared_findings 자동 쓰기도 거절한다. 삭제된 항목의 자동 복원과 같은 내용의 반복 갱신을 막는다.

이는 생성형 요약이나 모든 자연어 의미에 대한 증명이 아니다. 문자열 원문 검증과 제한된 분류를 사용한 보수적인 항목 추출이다. 숫자 없는 세션 한정 발언을 모델이 프로젝트 배경으로 잘못 분류할 가능성은 남아 있다. 기본 manual에서는 이 경로가 실행되지 않으며, auto_context 운영 적용 전 실제 사용자 발언을 추가 검토해야 한다. 내용을 수정할 때 전체 문서를 덮어쓰지 않고 해당 section/key만 교체한다.

자동 저장은 검증된 최종 응답 이후 `aafter_agent`에서 실행한다. 동일 source Run의 conversation 쓰기는 동일 출처 ID를 사용한다. 같은 항목의 동시 갱신은 한쪽만 통과하며, 충돌 시 이전 내용을 임의로 덮어쓰거나 별도 LLM을 호출해 다시 결정하지 않는다. 갱신 결과는 public `activity.started/completed`의 project_memory 활동과 답변 Run 결과의 `final_response.project_memory`에서 확인한다. 계획 후보가 있는 경우 활동 이벤트와 내부 graph 결과를 사용한다. 내부 memory_updates와 메모리 문서 자체를 SSE에 그대로 공개하지 않는다.

처리 중 DB 연결은 모델 대기 동안 유지하지 않는다. 자동 추출의 추가 출력 계약이 기존 모델 응답 길이나 재검증에 미치는 일반적인 비용은 이번 기능 검증으로 확정하지 않았다. 모델 호출 횟수 최적화는 [후속 목록](improvements/backlog.md)에 보류되어 있다.

## 반영과 검증

코드 적용 환경의 서비스 DB에 먼저 CRUD Alembic migration을 실행한다. 애플리케이션 기동에서 자동 schema 생성·데이터 backfill을 수행하지 않는다.

```sh
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head
```

새 head는 `20261002_0024`다. 0023까지의 기존 프로젝트/세션/Run과 checkpoint는 수정하지 않고 메모리 두 테이블만 추가한다. downgrade는 메모리와 receipt를 삭제하므로 저장된 공유 지식을 보존해야 하는 운영 환경에서 임의로 실행하지 않는다.

테스트와 실제 모델 확인의 범위는 [051 작업 기록](improvements/051-project-memory-runtime.md)을 참고한다. 이 변경으로 기존 Docker 컨테이너나 운영 DB를 재기동·마이그레이션하지 않았다. 장기 실행 종료 후 새로운 파일/수치 근거 연결, 프로젝트 간 공유, 프로젝트 협업 권한, 자동 결과 요약은 별도 범위다.
