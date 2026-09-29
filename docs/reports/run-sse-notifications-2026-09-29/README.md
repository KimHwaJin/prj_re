# Run SSE 변경 전후 비교

변경 전 기준은 `08a6809`, 변경 후는 `feature/run-sse-notifications` 구현이다.
이번 변경은 **상태가 바뀌지 않았는데도 반복하던 SSE 내부 조회와 연결별 DB 트랜잭션 점유를 줄인다.**
Agent·LLM·Executor의 실행 시간을 비교하는 실험이 아니다.

## 측정 조건

- API 프로세스 1개, 격리된 로컬 PostgreSQL 17, 실제 loopback HTTP SSE.
- 양쪽에 동일한 NullPool 테스트 세션 factory를 주입했다. LLM·Executor·Redis 호출과 실행 Worker는 사용하지 않았다.
- 각 조합 1회 측정. 최초 상태 수신 및 0.6초 안정화 후 3초 동안 유휴 SELECT를 집계했다.
- 인증/최초 조회, LISTEN 연결 설정, 쓰기·trigger 비용은 SELECT 집계에서 제외했다.
- 변경 전달 시간은 DB commit 직전부터 HTTP 클라이언트의 변경된 run.state 수신까지다.

## 결과

| 시나리오 | 연결 수 | 유휴 SELECT 이전 → 이후 | 열린 유휴 트랜잭션 이전 → 이후 | 변경 전달 평균 이전 → 이후(ms) |
|---|---:|---:|---:|---:|
| 서로 다른 Run | 1 | 15 → 0 | 1 → 0 | 182.8 → 46.2 |
| 서로 다른 Run | 10 | 140 → 0 | 10 → 0 | 303.3 → 178.6 |
| 서로 다른 Run | 30 | 351 → 0 | 30 → 0 | 803.0 → 541.2 |
| 서로 다른 Run | 50 | 427 → 0 | 50 → 0 | 783.0 → 632.5 |
| 같은 Run 여러 탭 | 1 | 15 → 0 | 1 → 0 | 116.3 → 37.2 |
| 같은 Run 여러 탭 | 10 | 134 → 0 | 10 → 0 | 256.7 → 43.0 |
| 같은 Run 여러 탭 | 30 | 340 → 0 | 30 → 0 | 891.5 → 32.2 |
| 같은 Run 여러 탭 | 50 | 438 → 0 | 50 → 0 | 961.4 → 57.2 |

변경 후 모든 조합에서 전용 LISTEN 연결은 1개였다. 같은 Run의 여러 탭은 공유 구독 1개를 사용했다.
각 측정 뒤 연결을 끊고 구독·캐시·listener가 모두 정리됨을 assertion으로 확인했다.

## 무엇이 바뀌었는가

1. 기존 연결별 0.5초 DB polling을 commit 알림 기반 조회로 바꿨다. 알림 장애·누락은 기본 15초 보조 조회로 보완한다.
2. 동일 사용자·세션·Run·커서의 조회 결과를 프로세스 내에서 공유한다. 서로 다른 Run의 DB 읽기는 여전히 각각 필요하다.
3. 실제 HTTP 시험에서 중첩 인증 dependency가 DB를 SSE 요청 수명 동안 붙잡는 문제를 확인했다. 하위 DB dependency까지 function scope로 바꾸어 스트림 시작 전에 반환한다.

## 해석의 한계

- 3초 동안 SELECT 0회였다는 결과는 영구적으로 조회가 없다는 뜻이 아니다. 기본 15초 보조 확인, 초기 접속, 재생, 실제 변경에는 DB 조회가 필요하다.
- NullPool은 매 읽기마다 새 DB 연결을 만들므로 지연 수치를 운영 풀의 성능으로 일반화할 수 없다. 단일 측정이며 운영 용량·p95 SLA가 아니다.
- 서로 다른 Run과 다른 Pod는 각각 조회한다. 연결 수 50은 사용자 50명의 전체 Agent 업무 부하와 다르다.
- 지속적인 토큰 출력, 장시간 느린 클라이언트, HPA, 폐쇄망 운영 경로는 측정하지 않았다.
- 최초 실패 시험에서 인증 트랜잭션 점유와 양쪽 DB factory 불일치를 발견했다. 원인을 수정하고 양쪽을 다시 측정했으며, 표에는 성공한 비교만 포함한다.
- 최종 검증에서는 종료 hook·프로젝트 소유권 확인·기본 factory 참조를 보완했다. 이들은 측정의 명시적 factory/동일 소유자/비종료 시나리오를 변경하지 않는다.

## 재현

기존 테스트 fixture는 로컬의 일회용 `identity_test` DB만 허용하며 해당 DB의 public schema를 초기화한다.
다른 테스트와 같은 DB에서 동시에 실행하지 않는다. 스크립트 경로는 변경 후 소스의 파일을 양쪽에 동일하게 사용한다.

```sh
PYTHONPATH=<비교할_소스>/src \
DTEST_IDENTITY_TEST_DATABASE_URL=<로컬_일회용_identity_test_URL> \
DTEST_SSE_BENCH_REPORT=<출력_JSON_절대경로> \
python -m pytest <변경후_소스>/scripts/benchmarks/sse_notifications.py -q -s
```

[이전 원본](before.json) · [이후 원본](after.json) · [재현 스크립트](../../../scripts/benchmarks/sse_notifications.py)
