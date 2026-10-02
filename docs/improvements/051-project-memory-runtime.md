# 051 프로젝트 공유 메모리 저장과 미들웨어 연결

| 항목 | 내용 |
|---|---|
| 상태 | 구현·전체 회귀·격리 DB/실제 모델/패키지 검증 완료, 베이스 병합·origin 게시 완료 |
| 시작일 | 2026-10-02 |
| 브랜치 | feature/project-memory-runtime |
| 출발 commit | f8d559f1710d0a1efe12056171989db0c281dc5e |
| 배포 상태 | 미배포, 기존 Docker·운영 DB 유지 |

## 문제와 원인

ProjectMemory는 AgentContext의 선택적 Protocol만 정의되어 있었다. 실제 저장소, 서비스 주입, 미들웨어 읽기·갱신이 연결되지 않아 같은 프로젝트의 다른 세션에서 배경이나 보고서 선호를 이어 쓰지 못했다. 043의 last_analysis_context는 정확히 같은 user/project/session의 완료 관찰만 보존하므로 이를 프로젝트 메모리로 재사용하면 기존 격리 전제가 깨진다.

또한 공유 문서 하나를 통째로 요약해서 덮어쓰면 다른 세션이 갱신한 주제가 사라질 수 있다. 별도 DB 풀을 만들거나 모델 대기 동안 DB를 점유하면 앞선 풀 수명·짧은 트랜잭션 개선을 되돌리는 결과가 된다.

## 실제 변경

| 영역 | 변경 전 | 변경 후 |
|---|---|---|
| service_contracts/project_memory.py | 저장된 실제 계약 없음 | 네 section, 안정적인 topic key, 본문·expected_version, 자동 추출 원문 quote |
| api_service의 memory 모델·service | 저장소 없음 | 기존 서비스 PostgreSQL/SQLAlchemy 풀의 현재 항목·출처·버전·삭제 표시와 멱등 receipt |
| CRUD Alembic | head 0023 | 0024가 메모리 두 테이블만 생성, 기존 프로젝트/Run/checkpoint 이력 유지 |
| 프로젝트 관리 API | 메모리 관리 없음 | 소유자 GET, 항목별 PUT·DELETE, 기존 SSO·CSRF 유지 |
| 공통 create_agent factory | project_memory 정책 없음 | 재사용 ProjectMemoryMiddleware를 모든 역할에 연결, provider 없으면 DB 접근 없음 |
| PlanningRuntime과 서비스 graph 조립 | memory 접근 객체 미주입 | API Run/Executor Event 경로에 owner/project/session/source Run으로 묶인 port 주입 |
| Conversation | 갱신 계약 없음 | 선택적 memory_updates 검증·최종 응답 후 쓰기·실제 저장 결과 전달 |
| 설정·개발 안내·OpenAPI | Protocol 설명만 존재 | 모드·필드 설명·명시적 공유 API·새 경계 안내 |

역할 호출의 before_agent에서 한 번 읽고 DB를 반환한다. 각 모델 요청과 JSON 재검증에는 그 invocation의 snapshot을 HumanMessage 참조로 주입한다. 기존 system_prompt와 실제 관찰의 grounding은 유지한다. 다음 세션/다음 역할 호출은 최신 문서를 다시 읽는다. 공유 캐시 Agent 객체에 owner별 문서를 저장하지 않는다.

쓰기 단위는 전체 메모리 문서가 아니라 section/key다. 항목별 expected_version을 비교하고 프로젝트 쓰기 트랜잭션에서 한도와 receipt를 원자적으로 검사한다. 다른 key는 모두 유지하고, 같은 key의 오래된 수정은 거절한다. 삭제 버전은 보존해 stale 생성 요청으로 복원되지 않게 한다. Worker의 자동 쓰기는 기존 TaskService 점유 검사도 적용하여 오래된 claim이 갱신을 커밋하지 못하게 했다. checkpoint 자체의 완전한 fencing을 새로 구현한 것은 아니다.

기본 모드는 manual이다. 자동 저장 범위 질문에 답이 없는 상태에서 기존 명시적 공유 원칙을 유지한 기본값이며, 자동 저장 선택이 확정되었다고 기록하지 않는다. auto_context에서는 **현재 사용자 발언의 짧은 원문**에서 배경·분석 선호·보고서 선호만 항목별로 추출한다. 본문=원문 quote, source/owner/version을 검사한다. 생성형 요약이나 Executor 관찰 기반 자동 결과 공유는 하지 않는다. 숫자·URL·대표적인 파일 경로·shared_findings와 삭제 항목 자동 복원은 거절한다. 수치·세션 결과는 명시적 관리 API로 공유할 수 있지만 사용자 기록일 뿐, 검증된 실제 분석 근거가 되는 것은 아니다.

메모리 전용 LLM 호출을 추가하지 않았다. 기존 create_agent 출력에서 추출하고 공통 미들웨어가 저장한다. 일반 호출 횟수 최적화와 보고서 정정 축소는 사용자 요청대로 계속 보류한다. 자동 분류의 자연어 의미를 기계적으로 전부 증명하는 것은 아니므로 manual과 내용 검사/수정 API를 제공한다.

## 검증 결과

- 로컬 모델 대역·실제 create_agent: prompt_json/provider_json_schema 검증, 호출당 한 번 읽기·retry 중 재조회/미검증 쓰기 방지, 동시 공유 Agent의 owner/project 분리, 다음 호출의 최신 읽기, 원문·버전·범위·삭제 정책, 충돌의 not_saved, 공개 활동/대화/Run 결과 일치.
- 실제 PostgreSQL: 관리 API와 owner 검증, 동일/다른 key 동시 갱신, 멱등 재전송, 배치 원자성·직렬화 한도, soft deletion, Run/Session 출처 검사, migration downgrade/upgrade의 기존 프로젝트 보존.
- 실제 API→queue Worker→graph→LLM 대역→PostgreSQL→SSE→다른 세션: 저장 결과와 원문 선호 전달, 내부 memory_updates 비공개, 새 Executor Execution 없음.
- 연결 하나의 테스트 풀: 인증 DB session을 관리 API가 재사용하며, 모델을 멈춰 둔 동안 메모리 snapshot 연결이 반환되고 다른 API 쓰기가 진행됨.
- 최종 점유 검사 이후 관련 122개 테스트 통과(18.16초). 현재 사용자 Run의 점유 토큰이 바뀌면 메모리 쓰기 0건임을 확인.
- 실제 qwen38-27b-nvfp4: 합성 요청으로 원문 선호 추출·다른 세션 사용·manual의 자동 저장 차단 세 사례 통과. 저장 사례는 모델 2회/7.763초·쓰기 1건, 다음 세션은 1회/3.472초·쓰기 0건, manual 사례는 1회/3.542초·쓰기 0건. 이 시험의 메모리 provider는 메모리상 대역이며 실제 DB/Worker/SSE 시험과 별개다. 성능 A/B나 실제 업무 전체 의미 정확성의 보장이 아니다.
- 최종 wheel: source checkout import 없이 12개 역할·prompt·새 메모리 리소스·OpenAPI 38개 path·mock 4단계 통과.
- 공개 문서 snapshot: 기존 10개 문서 경로의 validation 규칙 유지, memory 2개 경로/3개 method 추가. JSONC와 JSON 동일성, Workflow 정의 계약 동일성 확인.

최종 전체 회귀는 **932개 테스트와 subtest 2개 통과, 402.44초**였다. 경고 99건에는 기존 checkpointer 없는 durability 경고 등이 포함된다. 신규 단위 15개와 실제 PostgreSQL 9개를 포함한 최종 소스 상태를 검증했다. 모델 대역 회귀를 실제 모델/Executor 전체 E2E 성공으로 해석하지 않는다. 개인정보·인증 설정·실제 모델 원문은 Git에 포함하지 않는다. 기존 no-checkpointer durability warning을 전역 억제하지 않았다.

## 적용과 남은 범위

[설정·API·소스 위치·필드 설명](../project-memory.md)을 따른다. 배포 전 서비스 DB에서 CRUD Alembic upgrade head(0024)가 필요하며 기동 시 자동으로 테이블을 만들지 않는다. 테스트에서는 guard가 지정한 localhost scratch DB만 변경했다. 기존 운영 DB, 사용자 checkout/.env와 Docker Compose는 변경하지 않았다.

생성형 요약·세션 결과의 자동 공유·Dataset Registry·프로젝트 간 공유·협업 권한·메모리 receipt/삭제 표시 운영 정리는 이번 범위 밖이다. 모델의 배경/선호 분류에는 의미적 오류 가능성이 남으며 자동 모드 적용 전 실제 사용자 발언 평가가 필요하다. 기억이 최신 실행 근거를 대신하지 않는다는 경계를 유지한다. 관리자 복구·시스템 에러 운영성은 기존 후순위다.

## 통합과 게시

구현 commit: `6e9222364ba777cb406e1e1d4ebd1d01c5d4c1c2`. `feature/project-memory-runtime`의 검증된 38개 파일을 `feature/refactor-base`에 fast-forward 병합하고 두 브랜치를 origin에 atomic push했다. 원격 베이스와 파생 브랜치가 이 구현 SHA로 일치함을 확인했다. 이후 베이스의 별도 문서 commit에 통합·게시 결과를 기록한다. 파생 브랜치는 구현 commit을 보존한다.

원본 `feature/total_merge_v1` checkout의 HEAD·변경 상태·추적 파일·`.env`는 이전 상태로 유지한다. 실제 배포·운영 DB migration은 수행하지 않았다.
