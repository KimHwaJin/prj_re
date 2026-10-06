# Run별 LLM 선택

최초 `POST /api/v1/sessions/{session_id}/runs`에서 선택한다.
`main_model_name`은 실제 모델 ID가 아닌 서버에 등록된 별칭이다.

```json
{
  "main_model_name": "reasoning-v1",
  "input": {"messages": [{"role": "user", "content": "분석해줘"}]}
}
```

- 생략하면 접수 당시 `DEFAULT_MODEL`을 선택한다.
- 등록되지 않은 이름은 422이며 Run/Task를 만들지 않는다.
- 응답과 목록/상태 조회에 `main_model_name`, `model_revision`이 포함된다.
- resume body는 기존 `command`, `resume_token`만 보낸다. 모델 변경은 허용하지 않는다.
- 같은 Idempotency-Key와 원래 body를 다시 보내면 이후 기본 모델이 바뀌어도
  기존 Run을 반환한다. 다른 모델 이름으로 동일 키를 재사용하면 409다.
- 다음 새 Run에서는 다른 모델을 선택할 수 있다. 동일 세션의 미종료 작업 보호는 유지한다.

## 중앙 설정

`config.dev.yml`, `config.stg.yml`, `config.prd.yml`의 service.llm에 등록한다.
설정 해석은 기존대로 config > env > 기본값이며, `MODEL_CATALOG` 전체를 한 설정으로
선택한다. 환경의 항목과 YAML의 항목을 부분 병합하지 않는다.

```yaml
DEFAULT_MODEL: reasoning-v1
MODEL_CATALOG:
  reasoning-v1:
    provider: openai_compatible
    model_name: deployed-model-name
    api_base_url: https://llm.internal.example/v1
    api_key: replace-in-deployment-secret-config
    temperature: 0
    timeout_seconds: 60
    max_retries: 2
    enable_thinking: true
    structured_output_mode: prompt_json
  fast-v1:
    provider: openai_compatible
    model_name: another-deployed-model
    api_base_url: https://llm.internal.example/v1
    api_key: replace-in-deployment-secret-config
    structured_output_mode: provider_json_schema
```

실제 키는 레포에 커밋하지 않는다. Secret으로 마운트한 선택 config 파일 또는
환경의 JSON `MODEL_CATALOG`를 사용한다. YAML에 카탈로그를 명시하면 env 카탈로그를
덮어쓴다. `${ENV}` 문자열 자동 치환이나 Agent 내부 환경변수 재조회는 없다.
등록 필드 오타, 잘못된 기본 별칭, 빈 카탈로그는 기동 검증 오류다.

카탈로그를 사용하지 않는 기존 설정은 그대로 `MODEL_*`, `API_BASE_URL`을 읽어
`default` 별칭 하나를 만든다. 명시적으로 카탈로그를 등록하면 모델별 설정은 해당
항목과 항목 기본값에서 결정되며 기존 평면 MODEL_*를 모델별 항목에 혼합하지 않는다.
테스트에는 `provider: mock`, `model_name: test-model`, `mock_delay_ms: 5000`을 사용한다.
기존 mock Agent의 시나리오 범위(Executor 제출까지)는 변경하지 않았다.

## 저장 및 실행 경계

1. Run 접수: 등록 alias와 비밀정보를 제외한 설정 SHA-256을 서버 메타데이터
   `_model_selection: {name, revision}`에 저장한다. 요청에서 이 메타데이터를 지정할 수 없다.
2. Worker: 저장한 선택을 검증한 뒤 `model_selection`을 LangGraph state에 넣는다.
3. 각 LLM 노드: state → AgentContext → 선택된 역할 Agent의 `ainvoke()`로 전달한다.
4. 사용자 resume: 루트 Run에서 선택을 상속하고 체크포인트와 일치하는지 확인한다.
5. Executor 이벤트: 체크포인트 선택을 검증하고 같은 역할 Agent로 후속 분석/보고서를 생성한다.

그래프·Worker·체크포인트/서비스 DB 풀을 모델별로 만들지 않는다. 런타임이 그래프를
구성할 때 등록 모델별 역할 Agent 묶음을 한 번 만들고 같은 런타임 호출에서 재사용한다.
묶음 내부 역할들은 하나의 ChatOpenAI를 공유한다. 실제 LLM HTTP 연결은 모델 서비스별로
필요하며, 이를 DB 연결 풀 공유와 혼동하지 않는다. 기본 모델을 변경해도 기존 묶음의
모델을 전역 변수로 바꾸지 않으므로 세션 간 동시 실행이 섞이지 않는다.

개별 역할의 create_agent, 프롬프트, 프로젝트 prompt 미들웨어 및 구조화 응답 전략은 유지한다.
새 역할 개발자는 `context_from_state(state)`를 `ainvoke(..., context=...)`에 전달해야 한다.
단독 그래프 테스트에서 선택이 없는 경우 기본 역할을 사용할 수 있지만 서비스의
resume/Executor 이벤트 진입점은 기록 없는 선택을 허용하지 않는다.

## 장기 Run과 배포 주의점

- DB/체크포인트에는 alias와 hash만 저장한다. endpoint, API key, 클라이언트 객체는 저장하지 않는다.
- 모델 ID, endpoint, 추론 옵션, timeout/retry, 구조화 출력 설정 등이 바뀌면 hash가 바뀐다.
  키 회전은 hash를 바꾸지 않는다.
- 기존 Run이 사용하는 별칭의 설정은 모든 API/이벤트 Worker 배포에 유지한다.
  새 모델은 새 별칭(예: reasoning-v2)을 추가하고 DEFAULT_MODEL만 변경한다.
  기존 작업이 끝난 뒤 이전 별칭을 제거한다.
- 선택이 제거/변경됐으면 접수 전 resume는 409, 이미 큐에 들어간 호출은
  `RUN_MODEL_UNAVAILABLE` 오류로 종료하며 자동 재시도하지 않는다.
- Executor 이벤트에서 선택이 유효하지 않으면 그래프를 재개하지 않고 기존 이벤트 Worker의
  실패/재시도 처리로 전달한다. API의 waiting_executor 잠금을 임의로 해제하지 않는다.
  설정 복원과 이벤트 재처리가 필요하다. 관리자 복구 API는 별도 후속 작업이다.
- 모델 기록이 없는 과거 중단 Run은 자동 추정하지 않는다. 조회/취소/동일 요청 재전송은
  가능하지만 재개는 명시적인 복구 없이는 거절한다. 무조건 default를 채우는 마이그레이션은 없다.
- 동일 endpoint/model ID 뒤의 제공자가 가중치를 교체하는 것까지 hash가 감지할 수는 없다.
  장기 재현성이 필요하면 제공 모델의 버전도 고정해야 한다.
