# 프로젝트 공유 메모리

프로젝트당 **하나의 Markdown 문서**를 관리한다. 관리 화면은 문자열 하나를 조회·편집·초기화하며 별도 memory_id, entries, topic key는 없다. `system_prompt`는 사용자가 작성하는 명시적 프로젝트 지침이고 `project_memory`는 사용자와 Agent가 갱신하는 참고 맥락이다. 세션의 대화/체크포인트와 실제 분석 결과·보고서·아티팩트는 각 원본에 보존한다. 075가 051~053의 항목 저장·API를 대체한다.

프로젝트 기본 CRUD와 system_prompt/prompt_version의 적용 시점은 [프로젝트 API](project-api.md)를 따른다. 메모리 문서 version과 지침 버전은 별개다.

## 공개 관리 API

기본 prefix `/api/v1`. 기존 SSO 쿠키·쓰기 CSRF 헤더·활성 사용자/프로젝트 소유권 검사를 적용한다. body의 user_id나 memory_id를 받지 않는다.

| Method | Path | 의미 |
|---|---|---|
| GET | /projects/{project_id}/memory | 문서 조회. 최초 미작성은 content="", version=0, updated_at=null |
| PUT | /projects/{project_id}/memory | 문서 전체 수정. 자유로운 Markdown/일반 문자열 허용 |
| DELETE | /projects/{project_id}/memory?expected_version=N | 문서 초기화. content=""로 바꾸고 버전 증가 |

PUT 요청:

```jsonc
{
  // 새 문서 전체. 빈 문자열도 허용하며 초기화처럼 버전이 증가한다.
  "content": "## 프로젝트 배경\n생산라인의 불량 원인을 분석한다.\n\n## 보고서 선호\n핵심 결론과 업무상 의미를 먼저 설명한다.\n",
  // GET에서 받은 문서 버전. 0은 최초 쓰기 전뿐이다.
  "expected_version": 0
}
```

GET/PUT/DELETE 공통 응답 예시:

```jsonc
{
  // 단일 문서 형식 버전. 변경 횟수인 version과 별개.
  "schema_version": 2,
  // 이 문서를 공유하는 프로젝트 ID. 공개 응답에 내부 사용자 UUID를 반복하지 않는다.
  "project_id": "22222222-2222-4222-8222-222222222222",
  // 저장한 문자열 전체. API 조회에서는 모델 입력 예산에 맞춰 자르지 않는다.
  "content": "## 프로젝트 배경\n생산라인의 불량 원인을 분석한다.\n\n## 보고서 선호\n핵심 결론과 업무상 의미를 먼저 설명한다.\n",
  // 문서 전체의 단조 증가 버전. 초기화해도 0으로 되돌리지 않는다.
  "version": 1,
  // UTC ISO 변경 시각. 최초 미작성은 null.
  "updated_at": "2026-10-04T09:00:00+00:00"
}
```

오래된 expected_version은 **409**, 형식·설정 한도 초과는 **422**, 소유하지 않거나 비활성 프로젝트는 **404**다. 알 수 없는 body 필드와 null content는 422다. 선택적 `Idempotency-Key`(1~100자, 공백만 불가)를 사용할 수 있다. 같은 키·같은 요청은 원래 응답을 재생하며 현재 문서를 되돌리지 않는다. 같은 키를 다른 요청에 재사용하면 409다.

내용이 같은 비어 있지 않은 PUT은 버전을 늘리지 않는다. DELETE와 빈 content PUT은 이미 비어 있어도 버전을 증가시킨다. 이는 초기화 이전 snapshot으로 시작한 Agent/다른 탭의 늦은 저장을 막는 경계다. 초기화 후 최신 버전을 읽은 새로운 사용자 요청은 다시 메모리를 만들 수 있다.

## 문서 내용

권장 제목은 아래 네 가지다. **공개 API가 이 제목을 강제하지는 않는다.** 자유로운 제목·설명도 저장·조회할 수 있다.

| Markdown 제목 | Agent 내부 분류 | 자동 갱신 |
|---|---|---|
| `## 프로젝트 배경` | background | 현재 요청에 근거한 지속적인 업무 배경·목표 |
| `## 분석 선호` | analysis_preferences | 프로젝트 차원의 지속적인 분석 선호 |
| `## 보고서 선호` | report_preferences | 독자·구성·강조·표현 선호 |
| `## 공유할 주요 발견` | shared_findings | 명시적 문서 편집으로만 공유 |

수치·파일 경로·스키마·실행 결과 전체를 자동으로 모으지 않는다. 공유한 발견도 참고용 기록이며 검증된 Executor 근거나 Dataset Registry로 승격되지 않는다. 상세 결과는 원본 Run/보고서/아티팩트를 참조한다.

## Agent 읽기·부분 갱신

공통 `ProjectMemoryMiddleware.abefore_agent`가 역할 호출당 한 번 읽고 연결을 돌려준다. 다음 세션/호출은 새 snapshot을 읽으며 공유 Agent 인스턴스에 사용자 데이터를 캐시하지 않는다. JSON 정정 재시도에는 같은 snapshot을 쓴다. `create_agent(store=...)`와 외부 graph `compile(store=...)`에 공식 Store를 주입한다.

모델 입력은 `HumanMessage`의 `reference_type=project_memory`이며 memory.content는 선택한 Markdown, memory.version은 원본 문서 전체 버전이다. system_prompt·실행 승인·현재 관찰을 대신하지 않는다. 현재 요청과 원본 관찰을 우선한다. 출처 quote·Run ID·변경 시각은 참조 입력에서 제외한다.

기존 역할 선택을 유지한다. conversation은 네 분류 모두, 계획 수정/오류 수정은 배경·분석 선호, 결과 판단은 배경·분석 선호·공유 발견, 보고서 역할은 보고서 선호·배경·공유 발견을 참조한다. 자유 제목/제목 없는 텍스트는 공통 참고 후보다. 현재 요청과의 어휘 관련성, 역할 순서로 우선순위를 정하고 **완전한 섹션**만 예산 안에 넣는다. 섹션 중간을 자르거나 저장 문서를 줄이지 않는다. 생략 수는 selection.omitted_sections에 알린다. 제외된 기존 섹션은 자동 수정할 수 없다. 임베딩·별도 검색 모델·추가 요약 호출은 없다.

Agent 내부 변경 제안:

```jsonc
{
  // 고정 분류. 사용자 API path/항목 ID가 아니다.
  "section": "report_preferences",
  // 제목과 바깥 공백을 제외한 기존 본문 전체를 정확히 복사.
  // 없거나 빈 섹션일 때만 빈 문자열이다.
  "old_text": "보고서는 비전문가가 이해하기 쉬운 표현으로 작성한다.",
  // 변경 후 본문 전체. 여전히 유효한 기존 내용을 함께 보존한다.
  "content": "보고서는 비전문가가 이해하기 쉬운 표현으로 작성하고 핵심 결론을 먼저 설명한다.",
  // 개별 항목 버전이 아닌 프로젝트 문서 버전.
  "expected_version": 12,
  // 변경 근거인 현재 사용자 요청의 정확한 인용.
  "quote": "앞으로 보고서는 핵심 결론부터 설명해줘",
  // 지속적인 배경/선호 변경/명시적인 기억 중 하나.
  "intent": "preference_change"
}
```

한 번에 서로 다른 섹션만 변경한다. 이전 본문 일치, 문서 버전, 현재 원문 근거, 지속성, 입력 예산을 검증한 후 다른 부분을 보존하여 합친다. 코드 fence/들여쓰기 안의 제목을 섹션 경계로 오인하지 않는다. 중복 제목은 읽을 수 있지만 자동 수정하지 않는다. Agent replacement에는 새 레벨 2 제목을 넣을 수 없다.

동시 변경은 문서 전체 CAS를 사용한다. 다른 섹션 수정이어도 snapshot이 오래되면 이번 자동 저장은 **not_saved**다. 자동 재쓰기/재추론 없이 실제 저장 결과를 활동 이벤트와 final_response.project_memory에 알린다. 검증된 자동 저장 결과는 `{status:"saved", version:N}`이며, 실패는 `{status:"not_saved", reason:"..."}`다. 내부 memory_updates는 프론트가 보내는 입력이 아니다.

다른 섹션 보존은 서버가 보장한다. **수정 대상 섹션 안에서 의미를 빠뜨리지 않는 것은 모델 품질 검증 대상**이다. 원문 일치 검사는 의미 보존의 완전한 증명이 아니다. 전체 문서 자동 재작성·자동 요약·수치/분석 결과 자동 공유는 구현하지 않았다.

## 저장·원자성

공식 LangGraph `AsyncPostgresStore`, 기존 `DATABASE_URL`의 PostgreSQL `store` 테이블을 사용한다. 체크포인트 DB/Redis/PV 파일에 문서를 따로 복제하지 않는다.

| 대상 | namespace | 고정/계산 key |
|---|---|---|
| 프로젝트 문서 | (`dtest`, `project_memory`, 내부 user UUID, project UUID) | `document` |
| 멱등 저장 결과 | (`dtest`, `project_memory_receipts`, 내부 user UUID, project UUID) | source_id SHA-256 |

문서 value는 schema_version/content/version/updated_at/source다. namespace의 소유자·프로젝트는 서비스가 검증하며 namespace만으로 권한을 부여하지 않는다. 최신 source에는 직접 편집 여부 또는 source Run/Session과 변경 원문·intent를 보존하지만 공개 문서 응답/모델 입력에는 노출하지 않는다. 전체 변경 이력 저장소는 아니다. receipt는 읽기 대상 문서와 분리되며 콘텐츠의 두 번째 원본이 아니다.

쓰기에는 기존 프로젝트 배타 잠금과 Worker 실행 소유권 검사를 사용한다. 공식 Store의 같은 PostgreSQL transaction에서 문서와 receipt를 함께 커밋한다. 실패하면 모두 롤백한다. 사용자/프로젝트 비활성화·삭제와의 경계도 유지한다. API asyncpg pool과 공식 Store psycopg pool은 라이브러리 차이로 별개이며 Store는 프로세스당 최대 min(2, CRUD pool size), 최소 0 연결을 유지한다. LLM 대기에는 연결을 잡지 않는다.

## 통합 설정

```yaml
service:
  agent:
    # off: Agent 읽기/자동 쓰기 중단. 관리 API는 유지.
    # manual: 읽기와 명시적 관리 API만. 기본값.
    # auto_context: 현재 요청의 지속적인 배경·선호를 부분 갱신.
    agent_project_memory_mode: manual
    # Markdown 본문 문자 수. 출처 JSON은 포함하지 않으며 자동 삭제/잘림 없음.
    agent_project_memory_max_chars: 16000
    # 자동 교체할 섹션 본문 및 원문 quote 각각의 문자 수. 수동 PUT에는 적용 안 함.
    agent_project_memory_patch_max_chars: 4000
    # 한 번에 변경할 서로 다른 섹션 수. 1~4, findings는 자동 갱신 제외.
    agent_project_memory_max_updates: 4
    # 모델에 주입하는 전체 참조 JSON의 문자 수.
    agent_project_memory_prompt_max_chars: 6000
    # UTF-8 byte 기반 보수적 추정. 정확한 모델 토큰 수가 아님.
    agent_project_memory_prompt_max_tokens: 4096
```

config 명시값 > env > 기본값. 환경변수는 위 키의 대문자다. prompt 한도 중 하나가 0이면 Agent 읽기/자동 쓰기를 모두 건너뛰며 관리 API는 유지한다. 저장 max_chars는 1024~1000000, patch_max_chars는 1~16000이면서 max_chars 이하, max_updates는 1~4다. prompt 두 한도는 0~1000000이다. 변경은 프로세스 재시작 후 반영한다.

기존 `AGENT_PROJECT_MEMORY_MAX_TOPICS`와 `AGENT_PROJECT_MEMORY_TOPIC_MAX_CHARS`는 제거했다. YAML/env에 남아 있으면 설정 오류로 알려준다. patch_max_chars는 기존 개별 항목 제한을 이름만 바꾼 값이 아니라 **섹션 본문 교체 제한**이다. 저장 한도를 낮춰도 기존 GET 데이터는 숨기지 않으며 초과한 문서는 점진적으로 짧게 수정할 수 있다.

## 기존 데이터 이행·배포

`20261004_0028`은 0027 이후의 Alembic CRUD migration이다. 기존 section/key Store 항목을 네 제목의 Markdown으로 합치며, 살아 있는 본문은 그대로 보존하고 삭제 표시는 본문에서 제외한다. 같은 섹션은 key 순서로 연결한다. 최초 문서 버전은 기존 항목 버전의 합이며 이전 출처/삭제 메타데이터도 source.previous_sources에 남긴다. 무관한 Store namespace는 변경하지 않는다. 새 문서 키로 옮긴 항목 행은 삭제한다.

이전 항목 응답의 receipt는 새 문서 API 응답으로 재생할 수 없어 이행 경계에서 제거한다. 새 API에서 멱등 키를 새로 사용한다. downgrade 시 Markdown 전체를 marked legacy 항목 하나로 보존하며 다시 upgrade하면 문서 문자열을 그대로 복원한다. 이전 코드로 내용을 수정한 경우에는 일반 legacy 항목으로 이행된다.

**이전 버전 쓰기 프로세스를 중지하고** 설정의 폐기 키를 제거한 뒤 `alembic -c alembic.crud.ini upgrade head`를 실행하고 새 API/Worker 버전을 기동한다. old/new writer가 동시에 다른 형식으로 쓰는 rolling 전환은 지원하지 않는다. 이 작업에서는 격리 테스트 DB에만 migration을 실행했고 기존 로컬 서비스 DB/컨테이너는 업데이트하지 않았다.
