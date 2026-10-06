# 서비스 설정 로딩·주입 통합

> 098 설정 규칙: config.yml은 로컬 전용이며 환경별 YAML과 병합하지 않습니다. 현재 파일 선택·키·초기화는 docs/application-configuration.md를 따릅니다.
작성일: 2026-09-28. Gaia 템플릿 통합과 실행 계층 리팩토링에 포함하는 요구사항/설계다. 실제 .env나 배포 Secret 값은 읽지 않았다. 설정 코드·선언만 확인했으며 설정 로직과 실행 환경은 아직 변경하지 않았다.

**결정: 환경변수/YAML을 읽는 서비스 측 진입점을 하나로 만들고, 검증한 설정 객체를 API·Agent·Run 실행기·Executor 이벤트 처리에 전달한다. 설정 파일 하나, DB 하나, 연결 풀 하나로 통합한다는 뜻이 아니다.**

사용자 추가 설명에 따라 환경 선택은 기존 템플릿 방식을 그대로 따른다. Dockerfile에서 설정하는 환경 선택 변수의 dev/stg/prd 값으로 플랫폼이 config.dev.yml / config.stg.yml / config.prd.yml을 선택한다. 변수 이름과 config.yml 공통 병합 여부는 실제 템플릿에서 확인한다. 새로운 환경 선택 변수를 만들거나 서비스에서 별도의 파일 선택 로더를 추가하지 않는다. 플랫폼이 로딩한 결과를 서비스 adapter가 검증·변환하여 공통 snapshot으로 전달한다. 환경별 설정 파일이 여러 개인 것은 정상이며, 제거 대상은 컴포넌트마다 독립적으로 다른 파일·환경변수·기본값을 적용하는 구조다.

## 확인한 현재 경로

| 위치 | 동작 | 정리할 사항 |
|---|---|---|
| src/config.py | BaseSettings가 .env를 읽고 모듈 import 시 settings를 생성 | 설정 초기화 시점과 플랫폼 설정 로딩 순서 분리 |
| src/agent_config.py | 별도 dotenv 파서가 os.environ.setdefault를 호출한 뒤 AgentSettings 생성, 일부 계측 설정은 YAML 검색 | 로더가 전역 환경을 변경하는 동작 제거, 중복 설정 해석 통합 |
| src/app/worker/config.py | EW_ 접두사로 별도 BaseSettings 생성 | 중앙 설정의 이벤트 영역을 명시적으로 전달 |
| src/app/agent_worker/worker_main.py | AGENT_CHECKPOINT_DATABASE_URL을 HostSettings로 읽음 | API 그래프와 이벤트 재개가 같은 checkpoint 대상/namespace를 쓰도록 검증 |
| src/app/graphs/checkpointer_factory.py | Agent 설정을 다시 읽고 URL 미전달 시 AGENT_CHECKPOINT_DATABASE_URL 사용 | URL·pool 옵션을 호출자가 전달 |
| src/app/services/agent_graph_service.py | Agent 설정 재로딩, WorkerSettings 검증 실패 시 API DB/Redis 설정으로 fallback | 잘못된 설정을 다른 대상 연결로 바꾸지 않고 원인 명시 |
| src/app/services/workflow_persistence.py | WORKFLOW_DATABASE_URL → EW_DATABASE_URL → NullWorkflowStore 선택 | 업무 모드에 맞는 저장 여부와 대상을 명시적으로 검증 |
| src/app/core/run_diagnostics.py 등 | 동작 중 환경변수를 직접 조회 | 진단 설정도 startup snapshot에서 전달 |
| Compose/Deployment/local scripts | env_file, environment, ConfigMap/Secret 등의 외부 주입 | 실제 사용 프로파일별 입력·override 문서화, app 내부 재해석 방지 |

구체적으로 같은 EXECUTOR_SUBMIT_ENABLED도 API Settings 기본값은 false, Agent 로더 기본값은 true다. 이는 코드 기본값 불일치이며 현재 배포에서 실제로 서로 다른 값이 적용됐다는 증거는 아니다. API의 MODEL_NAME/LLM_MODEL_NAME alias와 Agent의 MODEL_NAME 조회도 서로 다르다. 과거 user_request 오류의 확정 원인으로 이 발견을 소급 적용하지 않는다.

## 목표 흐름

```text
플랫폼 설정 로더(YAML/환경 선택/Secret 주입 계약)
                      ↓
service_bootstrap → 플랫폼 adapter → resolve_service_settings()
                      ↓
             검증된 설정 snapshot
                      ↓
        API / Agent / Runtime / Executor / 진단
```

- 플랫폼 코드 자체의 설정 로더는 유지한다. 서비스가 플랫폼 YAML을 다시 임의로 병합하거나 dev/stg 선택을 별도로 추정하지 않는다.
- 실제 플랫폼 설정 접근 방식과 get_routers 호출 순서는 원본 확인 대상이다. 라우터 import 시 설정 객체나 자원을 생성하지 않도록 바꾸고, 앱 초기화 시 주입한 context를 Depends/app.state 또는 명시적 인자로 사용한다.
- 프로세스별 startup에서 한 번 해석한 snapshot을 사용한다. 서로 다른 Pod가 같은 입력을 받았는지 비교하는 것과 프로세스 내부 일관성 보장은 구분한다. 설정 변경은 기본적으로 재기동으로 적용하며 의도치 않은 요청별 재로딩은 제거한다.
- startup의 설정 검증은 DB pool·Worker·외부 API 호출보다 먼저 완료한다. 플랫폼 초기화 때문에 선행 import가 필요해도 서비스 import는 설정/자원 생성 부작용을 가지지 않게 한다.
- Alembic·초기화 CLI·로컬 도구도 같은 설정 해석 함수를 사용하되 전체 웹 앱이나 Worker를 시작하지 않는다.

## 입력과 우선순위

1. 사용자 확정 우선순위는 **선택된 config의 명시적 값 > process 환경변수 > 코드 기본값**이다. 항목별로 적용하며 config에 없는 항목만 환경변수로 보충한다. config와 환경변수가 모두 설정됐고 값이 달라도 config가 우선한다. 정상적인 소스 간 우선순위 적용을 충돌 오류로 취급하지 않는다.
2. dev/stg/prd를 고르는 기존 환경 선택 변수는 config 파일을 읽기 위한 선행 입력이다. 선택한 파일 안의 서비스 설정에 위 우선순위를 적용한다. config.yml 공통 병합이 있다면 플랫폼 규칙을 확인하되, 파일에 명시된 값과 로더가 생성한 기본값의 출처를 구별한다.
3. 플랫폼 로더가 환경변수로 config 값을 이미 덮어쓴 최종 결과만 제공한다면, 그 결과만으로 config 우선을 보장할 수 없다. 원본 config mapping 또는 출처를 보존하는 지원 API를 adapter에서 사용한다. 그것이 없으면 core 수정 없이 같은 선택 경로의 YAML 원본을 중앙 adapter에서 읽는 방식을 검토한다. 각 컴포넌트가 따로 파일을 선택하거나 읽는 방식은 사용하지 않는다.
4. 로컬: 명시적인 local 실행 모드에서만 지정한 .env를 읽는다. local dotenv를 환경변수 보조 소스로 사용할 경우 config > process env > 지정한 local dotenv > 기본값으로 적용한다. process env를 loader가 변경하지 않고 mapping으로 병합한다. 현재 작업 디렉터리를 검색해 임의의 .env를 읽지 않는다.
5. 테스트: 생성자/함수에 전달한 config/env mapping만 사용한다. 실제 .env나 process env에서 값을 자동 보충하지 않는다.

키의 존재 여부로 우선순위를 판단한다. false, 0, 빈 문자열, null을 `or` 연산으로 누락 취급하지 않는다. 선택된 값이 스키마상 허용되면 그대로 사용하고, 허용되지 않으면 출처와 항목을 표시한 검증 오류로 종료한다. 잘못된 config 값을 낮은 순위 환경변수로 조용히 대체하지 않는다. 기본값 없는 필수 항목이 어느 소스에도 없으면 startup 오류다. 중첩 설정도 section 전체가 아니라 leaf 항목별로 같은 규칙을 적용한다.

확정할 canonical 키마다 타입·단위·필수 모드·기본값·비밀 여부·허용 소스·소비자를 목록화한다. 일반 환경변수 전체에 unknown-key 에러를 내지 않고 서비스가 소유하는 설정 영역에서 오타를 검증한다.

## 설정 모델과 검증

서비스 설정은 하나의 큰 평면 객체 대신 runtime/database/checkpoint/events/llm/executor/storage/diagnostics 하위 모델로 나눈다. 모델의 불변성은 중첩 객체까지 고려한다. 하위 라이브러리에 전달할 때 다시 BaseSettings를 생성해 환경을 재조회하지 않도록 명시적인 값 객체/변환기를 사용한다.

- 역할별 DB는 유지 가능하다. 업무 DB와 checkpoint DB가 다르다는 이유로 오류 처리하지 않는다. 같은 checkpoint를 이어 쓰는 API/Executor 이벤트/복구 경로끼리 대상 DB·schema·namespace가 일치하는지 검증한다.
- URL은 단순 replace로 변환/비교하지 않고 driver, host, port, database, schema/options의 의미를 확인한다. 다른 host 표기가 같은 서버인지 문자열 비교만으로 증명했다고 하지 않는다. 초기 연결 검증은 별도 단계다.
- 필수 DB/Redis/Executor/LLM 설정 누락 시 의도하지 않은 다른 DB나 NullWorkflowStore로 조용히 전환하지 않는다. 제출 비활성·mock 모드처럼 명시적으로 꺼진 기능의 설정은 그 모드에 맞게 선택적으로 요구한다.
- Executor 실제/mock/비활성 모드와 LLM 실제/mock 모드의 기본값을 단일화한다. 단일화가 실제 외부 호출을 자동으로 켜는 변경이 되지 않게 기존 명시적 실행 모드를 보존한다.
- 같은 우선순위 소스 안에서 동일 의미의 이전 키가 함께 지정되고 값이 다르면 원인을 표시하고 startup을 실패시킨다. config와 env 사이의 값 차이는 확정된 우선순위로 처리한다. 다른 역할의 DB URL은 alias로 잘못 취급하지 않는다.
- timeout 단위/범위, pool 최소·최대, lease와 갱신 간격, 설정된 경로를 검증한다. 라이브러리가 process 환경만 지원하는 경우에만 bootstrap adapter가 문서화된 값을 한 번 전달한다.

## 적용 결과의 관측과 이전

- startup에 설정 프로파일·버전과 주요 항목의 출처를 남긴다. 로그는 비밀 제외 allowlist를 사용하고 DSN 원문, 토큰, 비밀을 포함한 전체 model dump/hash를 남기지 않는다. 비교용 digest는 비밀을 제외한 명시적 설정 항목만 대상으로 한다.
- 중앙 키 목록에서 로컬 예시 파일·환경별 설정 가이드·배포 매핑을 맞춘다. 설정 파일마다 기본값을 수동으로 복제하지 않는다. ConfigMap/Secret 생성·변경은 실제 배포 작업 시 수행한다.
- 구 MODEL/LLM, CHECKPOINT/AGENT 키의 호환 처리는 중앙 loader 한 곳에 두고 값 충돌을 확인한다. 이전 기간 후 제거한다. 필요한 이름 호환과 각 컴포넌트의 독립적인 설정 로딩은 구분한다.
- 단위 테스트는 환경/로컬 파일 격리, alias 충돌, 누락·범위 오류, 명시적 mock 모드, API와 이벤트 경로에 동일 checkpoint 설정 전달을 검증한다. Gaia 통합 테스트는 설정 검증 실패 시 Worker가 시작되지 않음과 실제 프로파일 선택을 확인한다.

## 순서와 완료 조건

Gaia 통합 골격과 함께 초기 단계에 구현한다. 환경변수 사용처를 먼저 매핑하고 중앙 모델/loader를 만든 뒤 API import-time 설정 → Agent → 이벤트 Worker → checkpointer/Workflow 저장 → 진단/CLI 순서로 전환한다. 기존 함수의 기본값과 fallback 의미를 항목별로 기록하고 명시적인 이전 규칙을 둔다.

완료 기준: 실제로 적용된 설정 값의 출처와 소비자를 한 곳에서 설명할 수 있고, 서비스 컴포넌트가 독립적으로 .env/YAML을 다시 읽지 않으며, 같은 논리 설정이 API/Agent/Worker에서 다르게 해석되지 않는다. 실제 플랫폼 버전에서 동일한 검증을 통과해야 이식 완료로 표시한다.
