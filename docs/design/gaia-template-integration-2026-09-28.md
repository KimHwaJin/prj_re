# 폐쇄망 Gaia 템플릿 통합 설계

> 2026-10-01 사용자 식별 변경: 아래 `X-User-Id` 계약은 이전 결정 기록이다. 현재 서비스는 SSO 로그인 쿠키와 변경 요청의 `X-CSRF-Token`을 사용하며, [현재 SSO 계약](../sso-authentication.md)을 우선한다. Run/계획/데이터 body와 내부 UUID 소유권은 유지한다. Gaia body의 user_id를 검증된 로그인 신원으로 신뢰하지 않는다.

작성일: 2026-09-28. 사용자 설명과 현재 저장소를 바탕으로 한 설계다. 실제 폐쇄망 템플릿 원본은 이 저장소에서 확인하지 못했다. 플랫폼 코드 수정, 애플리케이션 구현 변경, 배포는 하지 않았다.

**결정: 플랫폼이 만든 FastAPI 앱에 서비스 라우터와 실행 수명을 연결한다. 플랫폼 제공 `src/gaia/core.py`, `common`, `lib`는 수정하지 않는 것을 기본으로 한다. 루트 `app.py`와 서비스 소유 adapter를 통합 경계로 삼는다. 루트 파일명 변경만으로 이식 완료로 보지 않는다.**

## 사용자 설명으로 확인한 환경

- 루트: `app.py`, `config.dev.yml`, `config.stg.yml`, `config.yml`, `logger.yml`, `pyproject.toml`, `src/`.
- 추가 확인: Dockerfile에서 설정한 환경 선택 변수(dev/stg/prd)에 따라 플랫폼이 `config.dev.yml`, `config.stg.yml`, `config.prd.yml`을 읽는다. 이 기존 선택/로딩 경로를 유지한다. 변수의 실제 이름과 `config.yml`의 공통 설정 병합 여부는 원본 확인 대상이다.
- `src/`: `common`, `gaia`, `lib`, `middleware`, `routers`, `workflows`.
- `routers/__init__.py:get_routers()`에 라우터를 등록하며 `GaiaService`가 이를 앱에 연결한다. 반환 타입, prefix 적용, 호출 순서는 실제 원본 확인 대상이다.
- `GaiaService`는 FastAPI 생성, 로거, 기본 미들웨어, 라우터, health 등을 조립한다.
- 설명된 `main()`은 OpenAPI 설정, Instrumentator 연결 후 `self.app.router.lifespan_context = lifespan`을 대입하고 Uvicorn을 실행한다.
- 루트 `app.py`는 src를 import 경로 앞에 넣고 `GaiaService().main()`을 호출한다. 사용자 추가 확인에 따라 app.py는 서비스 측에서 수정 가능한 진입점으로 취급한다. 기동 연결 코드 수정에 별도 재승인을 요구하지 않는다. 실제 원본 확인은 허용 여부를 다시 묻기 위한 것이 아니라 필수 초기화·기동 계약을 보존하기 위한 것이다.
- 하나의 Deployment, Pod당 컨테이너 하나라는 기존 제약은 유지한다. 별도 Worker 서비스나 추가 인프라를 전제하지 않는다.
- 추가 사용자 설명: 플랫폼은 `/api/v1/{workflow}/run`, workflow GET 조회와 `src/workflows/*.py`의 일반 객체 ainvoke 실행을 제공한다. 최신 정정에 따라 이 서빙 규격은 필수 준수 조건이 아니며 우리 API·비동기 접수 계약을 우선한다. 플랫폼 호출 객체 연결은 선택적 adapter로 둔다. 기존 Gaia 기동/설정 합의와 특정 workflow 서빙 규격은 구분한다. [다중 Agent 설계](extensible-agent-runtime-2026-09-28.md)의 최신 정정을 따른다.

## 가장 먼저 확인해야 할 lifespan 문제

현재 저장소의 `src/app/api/v1/router.py`는 APIRouter lifespan에서 Run Worker와 reconciler를 시작·종료한다. `src/main.py`에는 별도의 runtime shutdown도 있다. 따라서 라우터만 옮기면 현재 앱 진입점의 모든 종료 책임까지 자동으로 이전되는 것은 아니다.

제공된 설명대로 라우터 연결 후 플랫폼 main에서 lifespan_context를 새 함수로 덮어쓰면, 이미 합성된 라우터 lifespan이 제외될 수 있다. API가 정상 응답한다는 사실은 Worker가 시작됐다는 증거가 아니다. `GaiaService.main()` 호출 전에 lifespan을 연결하는 것만으로는 이후 덮어쓰기를 막을 수 없고, main은 서버 실행 동안 반환하지 않으므로 호출 뒤에 설정하는 것도 해법이 아니다.

2026-09-28 격리 재현 결과:

- 로컬 FastAPI 0.141.1 / Starlette 1.6.0의 FastAPI와 APIRouter를 생성했다. 라우터 lifespan은 worker_start/worker_stop을 기록하고, 플랫폼을 모사한 lifespan은 platform_start/platform_stop을 기록했다.
- APIRouter를 include한 뒤 lifespan_context를 플랫폼 함수로 교체하고 TestClient context로 시작/종료: HTTP 라우트 200, 기록은 platform_start → platform_stop만 발생.
- include 후의 기존 lifespan_context를 보존하고 플랫폼 context와 중첩 합성하여 시작/종료: HTTP 200, platform_start → worker_start → worker_stop → platform_stop 발생.
- HTTP/DB/Redis/Executor 실서비스를 사용하지 않았다. 실제 Gaia 템플릿 및 해당 버전의 결과가 아니라, 설명된 덮어쓰기 패턴의 로컬 재현이다.

## 통합 방식 선택

기본안은 수정 가능한 app.py에서 서비스 소유 bootstrap을 호출하는 방식으로 확정한다. app.py에는 진입 코드만 두고 실제 연결은 별도 모듈에 둔다. GaiaService 생성자가 조립한 앱을 재사용하고 플랫폼 main의 필수 설정을 보존한 뒤 기존 앱/라우터 수명과 서비스 runtime 수명을 합성하여 Uvicorn을 실행한다. 설명된 main을 무조건 재호출해 합성한 lifespan을 덮어쓰지 않는다. 공식 확장 hook이 원본에서 확인되면 같은 기본안 안에서 이를 활용해 중복 기동 코드를 줄인다.

1. 플랫폼에 공식적인 앱 구성/lifespan 확장 hook이 있으면 우선 사용한다. get_routers 등록만으로 lifespan이 보존된다고 가정하지 않는다.
2. 그런 hook이 없고 app.py에서 기동을 관리할 수 있다면, GaiaService가 조립한 동일 FastAPI 인스턴스를 사용하고 서비스 소유 bootstrap adapter가 기동을 담당한다. 이 경로는 덮어쓰기를 수행하는 기존 main을 그대로 호출하는 방식과 다르다. 플랫폼 main의 OpenAPI, 계측, expose, host/port, 로그 및 기타 필수 설정을 원본 기준으로 보존하고 lifespan을 명시적으로 합성한다.
3. 플랫폼 main 호출이 필수이고 확장 hook도 없다면 이는 통합 제약으로 기록하고 플랫폼 지원 지점을 확인한다. 런타임에 uvicorn.run을 전역 monkey patch하거나 내부 core를 몰래 수정하지 않는다.

세부 구현은 실제 main과 생성자 전체를 확인한 뒤 확정한다. 제공된 코드 요약만으로 플랫폼 초기화 책임을 모두 복제했다고 주장하지 않는다. 이식성과 무관한 새 FastAPI 앱을 만들어 플랫폼 인증·미들웨어·계측을 누락하는 방식을 사용하지 않는다.

## 구현 경계

```text
app.py                         플랫폼 기동 계약 + 서비스 bootstrap 호출
src/gaia, common, lib          플랫폼 제공 코드 유지
src/routers/__init__.py        get_routers 기존 형식에 서비스 APIRouter 등록
src/<service_package>/        서비스가 소유하는 API/실행기/업무 코드
  integration/               플랫폼 설정·lifespan·인증 컨텍스트 연결
  runtime/                   공통 supervisor, 실행 슬롯, 자원 수명
```

`<service_package>`와 세부 디렉터리는 개념적 배치다. 현재 `src/app` 유지 여부는 실제 템플릿의 import 충돌을 확인해 결정한다. 루트 app.py와 패키지 app, 현재 top-level config 및 플랫폼 config, workflows 등 이름 충돌을 반드시 확인한다. sys.path 변경에 의존하는 우연한 import 성공을 완료 조건으로 삼지 않는다. get_routers 함수 반환 구조도 임의로 가정하지 않는다.

플랫폼을 아는 코드는 adapter에 모으고 핵심 실행기는 GaiaService를 import하지 않는다. 로컬에서도 같은 supervisor/settings 변환 경계를 사용한다. get_routers나 모듈 import 중에 Worker·DB 연결·외부 API 호출을 시작하지 않는다.

통합 startup에서 설정 검증 → 필요한 풀/HTTP client 준비 → 공통 supervisor 시작 순서를 관리한다. 부분 초기화 실패도 역순으로 정리한다. 종료 시 새 claim 중단 → 제한된 drain/복구 가능한 상태 확보 → 보조 loop 종료 → 자원 종료를 수행한다. 플랫폼 계측과 기존 다른 라우터의 수명도 보존한다. 과도기에는 기존 router lifespan과 새 supervisor가 동일 Worker를 두 번 시작하지 않게 단일 소유자를 지정한다.

## 설정·라우터·미들웨어·폐쇄망 패키징

설정 로딩·검증·주입의 구체적인 이전 범위는 [설정 통합 설계](configuration-unification-2026-09-28.md)를 따른다. API·Agent·Worker의 독립적인 설정 로더를 하나의 서비스 snapshot으로 연결하는 작업을 플랫폼 통합과 함께 선행한다.

- dev/stg/prd YAML의 선택은 플랫폼 규칙을 따른다. 서비스 설정의 항목별 우선순위는 사용자 요청대로 config 명시값 > 환경변수 > 기본값이다. 플랫폼 로더가 이미 env/default를 병합했다면 원본 값의 출처를 확인해 이 우선순위를 보장한다. 현재 .env, config, agent_config가 각각 다른 DB/Redis 값을 독립적으로 선택하는 이중 설정 경로를 만들지 않는다. secret은 플랫폼 주입 경로를 사용하고 문서/로그에 출력하지 않는다.
- URL prefix, 사용자 인증 컨텍스트, 오류 응답, 요청 ID는 플랫폼 방식에 맞춰 연결한다. 로거·미들웨어·Instrumentator·metrics route를 중복 등록하지 않는다.
- 플랫폼 health의 의미를 확인한다. 고정 health 응답만으로 Worker 실행 가능 상태를 판단하지 않고, 별도 readiness 또는 지원되는 확장 경로를 검증한다. 실제 probe 설정 변경 가능 여부는 별도로 확인한다.
- pyproject.toml을 통째로 덮어쓰지 않는다. 플랫폼 Python/FastAPI/Starlette/Pydantic 등과 서비스 의존성의 교집합을 확인하고 재현 가능한 설치 자료를 만든다. 로컬 테스트 버전이 폐쇄망 버전이라고 가정하지 않는다.
- 폐쇄망 대상 OS/CPU/Python에 맞는 내부 패키지 저장소 또는 wheel 공급 경로를 사용한다. 기동 시 외부 다운로드가 필요한 의존성·계측 exporter가 없는지 확인하고 필요한 모델/메타데이터 자원과 공유 PV 경로를 명시한다.
- DB schema/checkpoint 초기화는 승인된 배포 마이그레이션 경로에서 수행한다. get_routers import나 Pod별 startup에서 임의로 스키마를 변경하는 방식으로 대체하지 않는다.

## 사용자 인터페이스 — 최신 합의

사용자는 플랫폼 인증과 별개로 등록된 ID만 입력해 서비스를 사용하도록 요구했다. 사용자용 API는 X-User-Id 공통 헤더, DB의 admin/user 역할·소유권 검사로 통일한다. 로그인·토큰 발급 및 플랫폼 인증 연계를 구현 전제로 삼지 않는다. /users/me는 조회, 사용자 등록·수정·비활성화는 관리자 전용이다. 상세 정책은 [확정 인터페이스](platform-user-api-contract-2026-09-28.md)를 따른다.

플랫폼이 제공하는 UserChatGaia DTO는 참고 형식이며 동일한 body 계약을 강제하지 않는다. 선택 옵션 중 main_model_name만 지원한다. 생략 시 기본 LLM, 지정 시 등록 모델을 사용하고 Task/resume에 고정한다. session_system_prompt/chat_type/is_super_agent/a2a_remove_urls 기능은 이번 범위에서 제외한다. 내부 데모의 호출 방식도 운영 계약의 기준으로 삼지 않는다.

Gaia 앱의 기동·미들웨어·계측 통합 요구는 그대로 유지한다. 사용자 인터페이스는 설계 합의이며 아직 소스에 적용하지 않았다.

## 작업 순서와 완료 조건

핵심 실행 계층 교체에 앞서 **플랫폼 통합 골격 검증**을 수행한다. 확인된 정체 수정·규칙 테스트처럼 독립적인 작업은 병행 가능하나, 로컬 전용 진입점에 새 런타임을 결합한 뒤 마지막에 이식하지 않는다.

1. 실제 template app.py/core.py/get_routers, 설정 로더, pyproject 및 실행 명령을 확인하고 hook 또는 서비스 bootstrap 경로를 결정한다.
2. 가짜 runtime으로 startup/종료 정확히 1회, 라우트 등록, 플랫폼 OpenAPI/health/metrics/미들웨어 보존, import 부작용 없음을 확인한다.
3. 부분 startup 실패·정상 종료·TERM에서 정리 순서를 확인한다. Worker가 죽었는데 API health만 정상인 상태를 탐지할 수 있는지 검증한다.
4. 동일 통합 골격에 새 runtime을 연결해 API → HITL → Executor mock → 완료 흐름을 검증한다.
5. 대상 의존성으로 폐쇄망 설치·기동, YAML 환경 선택, 공유 PV, 재시작 후 복구를 검증한다.

현재 완료된 것은 요구사항 문서화와 로컬 lifespan 패턴 재현이다. 실제 템플릿 호환 구현과 폐쇄망 검증은 미완료다.

참고: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/). 공식 문서는 lifespan이 공유 자원의 시작·종료를 관리하는 수단임을 설명한다. 위 덮어쓰기 판정은 사용자 제공 코드 흐름과 로컬 재현을 근거로 한다.
