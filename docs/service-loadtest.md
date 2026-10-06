# 서비스 성능 검증 도구와 지원 범위

현재 서비스는 SSO 쿠키·CSRF와 통합 Run 접수/재개 API를 사용한다.
과거 Locust 결과와 현재 실행 가능한 진단 도구를 구분한다.

## 현재 사용할 도구

| 목적 | 도구·안내 | 측정 범위 |
| --- | --- | --- |
| API·CRUD·명령 Worker·HITL·SSE 처리량 | [service_throughput](../scripts/benchmarks/service_throughput/README.md) | 실제 로컬 HTTP/DB/checkpoint, 테스트 SSO와 모델 응답 fixture |
| Executor 결과·후속 처리 경계 | [worker_e2e](../scripts/benchmarks/worker_e2e/README.md) | 시나리오별 로컬 Executor fixture와 관찰·메모리·결과 처리 |
| 실제 모델 파라미터·실행 확인 | [기능 콘솔](service-demo-console.md), [Agent 개발 안내](agent-development/README.md) | 명시적으로 연결한 모델/Executor를 이용한 기능 확인 |

각 runner의 입력·격리 DB·capture 검증·종료 조건을 해당 README에서 확인한다.
같은 테스트 DB에 회귀 테스트와 벤치마크를 동시에 실행하지 않는다.
fixture 모델의 지연값이나 로컬 Executor 결과를 실제 모델·운영 Executor
전체 처리량으로 해석하지 않는다. 이번 문서 정리에서 새 부하를 측정하지 않았다.

## 이전 Locust 도구의 현재 제한

`scripts/loadtest/`와 `compose.loadtest*.yaml`은 과거 CRUD/4단계 HITL 측정을
재현하기 위한 도구다. 현재 서비스에 그대로 실행하는 안내로 사용하지 않는다.

- `scenario.prepare_user()`는 X-User-Id를 사용한다. 현재 서비스는 이 헤더만으로
  인증하지 않는다. SSO 쿠키·CSRF 클라이언트로 이행이 필요하다.
- `scenario.execute(submit=True)`는 현재 planning runtime의 Executor 제출 연결이
  없다고 명시적으로 거절한다. CLI에 submit 옵션이 있다는 사실이 실제 제출
  시나리오의 지원을 의미하지 않는다.
- `DATA_MOCK`·데이터 선택 전용 API·네 단계마다 새 Run을 만든다는 옛 설명은
  현재 흐름의 사용 계약이 아니다. 현재 공개 Run ID는 재개에도 유지한다.
- 과거 스크립트를 현재 서비스에 맞추는 작업은 이번 정리 범위에서 수행하지 않았다.

따라서 기존 control.py/Locust 명령을 현재 서비스의 지원되는 실행 예제로
제공하지 않는다. 과거 측정의 commit·인증·시나리오는 해당 날짜의 보고서와
Git 이력에서 확인한다. [벤치마크 적용 범위](../scripts/benchmarks/README.md)를
함께 따른다.

## 결과 해석

API 응답 시간, 큐 대기, graph 실행, 모델 대기, checkpoint/DB 저장, SSE 전달,
Executor 접수와 실제 완료는 각각 다른 지표다. HTTP와 FLOW를 합친 Locust
Aggregated 값을 API RPS로 해석하지 않는다. 제출202는 실제 실행 성공이 아니다.

비교 시 같은 모델 fixture 지연, graph 총한도, 사용자 수·생각 시간·조회 간격,
pool 예산·자원, 반복 수와 종료 지점을 고정한다. 실패·미완료·skip을 성공한
요청의 평균에 섞지 않고 별도로 보고한다. 운영 Kubernetes/HPA와 실제 서비스의
성능 확정은 별도 검증이 필요하다.
