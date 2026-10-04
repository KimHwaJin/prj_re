# Jupyter·Redis 관리 API 정리

2026-10-04의 084부터 사용자 API에서 미사용 Jupyter 서버 registry와 Redis 연결 검사 API를 제거했다. Jupyter 실행은 기존 Executor 연동을 통해 수행하고, 로그인 세션과 실행 결과 Streams는 공통 `REDIS_URL`을 사용한다. 별도 관리자 대체 API는 추가하지 않는다.

## 제거된 계약

| Method | 기존 경로 | 변경 후 |
|---|---|---|
| GET | `/api/v1/jupyter-servers` | 404 |
| GET | `/api/v1/jupyter-servers/{server_id}` | 404 |
| POST | `/api/v1/jupyter-servers/{server_id}/health` | 404 |
| GET | `/api/v1/redis/ping` | 404 |

위 경로는 실제 FastAPI 라우터·자동 OpenAPI에서 제외한다. 서버 조회·probe 서비스, 응답 스키마, Jupyter ORM 모델도 삭제한다. 이전 클라이언트에서 이 endpoint를 연결 검사나 화면 선택 목록에 사용했다면 호출을 제거해야 한다.

현재 세션의 실행 환경은 세션 생성 시 검증하는 `settings.kernel_profile`과 Executor runtime profile을 통해 결정한다. 이를 삭제된 Jupyter registry의 서버 ID로 바꾸지 않는다. 기본·허용 커널 정책은 [세션 API](session-api.md)를 따른다.

기존 `/service/live`, `/service/ready`, `/service/metrics`는 유지한다. 각 endpoint의 실제 검사 범위는 [배포 안내](deployment-configuration.md)에 있다. 삭제된 Jupyter health POST와 동일한 응답이나 별도 서버별 점검을 제공하는 대체 API는 아니다.

## 설정 이행

다음 설정은 삭제한다. YAML·프로세스 env·명시적 로컬 env 파일에서 발견하면 설정명만 담은 `ConfigurationError`를 발생시킨다. 삭제 설정이 여전히 적용되는 것처럼 조용히 무시하지 않는다.

| 삭제 설정 | 이전 용도 | 현재 설정 |
|---|---|---|
| `JUPYTER_ALLOWED_HOSTS` | 별도 registry 서버 접속 허용 목록 | 해당 관리 기능 제거. Executor runtime profile 정책 유지 |
| `JUPYTER_HEALTH_TIMEOUT_SECONDS` | 직접 Jupyter HTTP probe 제한 | 해당 probe 제거. 기존 Executor HTTP 제한 유지 |
| `JUPYTER_TOKEN_ENCRYPTION_KEY` | registry token 암호화 키 | registry token 사용 제거 |
| `REDIS_PING_TIMEOUT_SECONDS` | 공개 Redis ping 전용 timeout | 해당 endpoint 제거. SSO·이벤트별 timeout 유지 |
| `REDIS_HOST` | 실제 소비 코드가 없는 중복 주소 | `REDIS_URL` |

SSO용 bounded Redis pool, 이벤트 consumer의 Redis pool·Stream/group/namespace, Executor HTTP 제출·결과·artifact 연동, Agent·checkpointer·Store 설정은 그대로다. 실제 서비스·원본 `.env`·기존 컨테이너를 이번 작업에서 재설정하거나 재기동하지 않았다.

Fernet 구현과 프로젝트의 직접 `cryptography` 의존성은 제거했다. `uv.lock`과 생성 `requirements.txt`에는 `langgraph-api`의 전이 의존성으로 cryptography가 남을 수 있다. 이를 프로젝트의 남은 Fernet 기능으로 해석하지 않는다. 폐쇄망 사내 SSO SDK가 요구하는 의존성은 그 SDK 설치 계약을 별도로 따른다.

## 기존 DB 보존

기존 `jupyter_servers` 테이블·행·암호화 token은 자동 삭제하거나 변환하지 않는다. 기존 Alembic 0008 revision도 이미 배포된 schema 이력을 위해 유지한다. 따라서 기존 DB뿐 아니라 이력을 처음부터 적용한 새 DB에도 이 레거시 테이블은 남을 수 있다. 현재 서비스에서는 읽거나 쓰지 않는다.

ORM 모델 삭제로 인한 우발적 정리를 막기 위해 `crud_migrations/env.py`의 autogenerate `include_object`는 **DB에서 반영된 `jupyter_servers` 테이블에 대응 ORM 모델이 없을 때** 비교에서 제외한다. 공식 LangGraph Store 테이블 제외도 유지한다. 다른 테이블을 일괄 제외하지 않고, 미래에 대응 모델이 다시 등록되면 그 비교도 막지 않는다.

이 필터는 explicit migration의 `DROP TABLE`, 수동 SQL, downgrade를 막지 않는다. 실제 테이블 제거가 필요하면 데이터 보존·복원 요구를 먼저 정한 별도 migration으로 수행한다. 이번 작업에 새 revision이나 실제 DB 변경은 없다.

## 검증과 다음 범위

[084 작업 기록](improvements/084-unused-infrastructure-apis.md)을 따른다. API 제거·설정 이행·SSO·Streams 공존·패키지 검증이며 실제 Executor/사내 SSO SDK 연결이나 성능 A/B 측정은 아니다.

다음 기능 검증은 SSO → 프로젝트 → 세션 → Run → HITL/resume → Executor 결과/SSE의 통합 계약 확인이다. Message CUD·Workflow CRUD·운영 복구·모델 호출 수 최적화는 기존 후순위로 유지한다. 향후 Jupyter 관리 화면이 실제 요구사항으로 생기면 Executor가 제공하는 대상 목록과 권한 계약부터 정하며, 이번 미사용 registry를 다시 자동 복구하지 않는다.
