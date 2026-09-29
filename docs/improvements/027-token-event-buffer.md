# 027 — LLM 토큰 이벤트 버퍼 상한·시간 기준 저장

- 날짜: 2026-09-29
- 기준: `039d516` (026을 feature/refactor-base에 fast-forward 병합)
- 브랜치: `feature/token-event-buffer`
- 구현·검증 commit: `30e583e`
- 검증 후 이번 작업의 일회용 PostgreSQL 컨테이너·볼륨 정리 완료.
- 상태: 구현·격리 PostgreSQL/전체 회귀/오프라인 비교/패키지 검증 완료. 베이스 병합·push·배포 미수행.

## 문제와 범위

`LLMTokenEventBuffer`는 LangChain callback으로 받은 출력 조각을 DB의
`llm.token.delta` 이벤트로 저장한다. 026의 SSE 조회 개선보다 앞에 있는 쓰기 경로다.

- 기존 `asyncio.Queue()`에 상한이 없어 DB가 느리면 callback 입력이 계속 적재됐다.
- `queue.get(timeout=interval)`이 토큰마다 다시 시작되어, 연속 출력 중에는 설정 시간이
  지나도 문자 수 기준에 도달하거나 출력이 멈출 때까지 저장되지 않을 수 있었다.
- 기존 정상 종료 tail flush/실패 시 복구 필요 정책은 유지한다. 실행 동시성, LLM 선택,
  Agent 프롬프트·graph·middleware, Message/Workflow 관리 정책은 바꾸지 않는다.

이는 코드와 재현 시험으로 확인한 적재·저장 지연 경로다. 운영 OOM 발생이나 과거의
전체 Run 지연 원인이 이 버퍼 하나였다고 확정한 것이 아니다.

## 변경

1. Run별 UTF-8 payload 바이트·조각 수 상한을 추가했다. 큐, 배치, DB 저장 중인 조각을
   합산하고 commit/DB 반환이 끝나야 용량을 반환한다. 빈 큐라도 저장 중인 payload는
   용량에서 제외하지 않는다. 큰 SDK 조각은 Unicode 문자열 경계를 보존하며 나눠 적재한다.
2. 첫 조각의 적재 시각으로 저장 기한을 계산한다. **경과 시간 또는 누적 문자 수** 중
   먼저 도달한 조건으로 저장한다. 버퍼가 가득 차면 크기 기준 미달이어도 저장해
   공간을 돌려준다. 상한에 정확히 닿지 않아도 남은 공간에 다음 조각을 담을 수 없으면
   부분 배치를 깨워 저장한다. 각 조각마다 전체 누적 내용을 순회하던 길이 계산을 제거했다.
3. 가득 차면 callback이 기다린다. LangChain의 `run_inline=True`, `raise_error=True`로
   coroutine과 오류 전파를 연결하고, callback 전체 대기에 기한을 둔다. 같은 buffer의
   동시 callback은 순서대로 적재한다. 기다리는 생산자별 별도 background task는 만들지 않는다.
4. DB 저장에도 기한을 둔다. 실패·대기 초과 시 writer/생산자가 종료하도록 연결하고,
   미완료 저장을 성공이나 자동 graph 재시도로 처리하지 않는다. 기존
   `ExecutionNeedsRecovery` 경로가 세션 소유권을 보존하고 프로세스의 새 점유를 중단한다.
5. 종료 표식을 큐에 넣지 않고 별도 닫힘 상태로 writer를 깨운다. 버퍼가 가득 차도
   종료 요청이 막히지 않는다. 정상 종료는 마지막 조각까지 저장한 후 반환한다.
   `short_session`으로 반복 취소에도 DB 반환 완료를 확인하고 기존 cleanup 기한을 유지한다.
6. 이벤트 이름·payload·문자 offset·공개 Run 커서 재생 규격은 유지한다. 별도 테이블,
   마이그레이션, 인프라 및 DB 연결 풀은 추가하지 않는다.

## 설정

config 명시값 > env > 기본값의 기존 중앙 설정을 사용한다. 환경변수 추가 입력 없이도
기본값으로 동작한다. 음수·0·NaN·Infinity 등 부적절한 값은 시작 시 거절한다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| LLM_TOKEN_FLUSH_INTERVAL_SECONDS | 0.2초 | 첫 적재 후 저장 시작 기준. DB 저장 완료/화면 표시 기한이 아님 |
| LLM_TOKEN_FLUSH_CHARACTERS | 256 | 배치 저장 문자 수 기준(기존 설정) |
| LLM_TOKEN_BUFFER_MAX_BYTES | 262144 (256 KiB) | Run별 적재·배치·저장 중 UTF-8 payload 합계 상한, 최소 4 |
| LLM_TOKEN_BUFFER_MAX_ITEMS | 1024 | Run별 적재·배치·저장 중 조각 수 상한 |
| LLM_TOKEN_ENQUEUE_TIMEOUT_SECONDS | 5초 | 한 callback의 적재 대기 총 기한. 큰 조각 분할 대기도 포함 |
| LLM_TOKEN_WRITE_TIMEOUT_SECONDS | 5초 | 각 LLM별 배치 DB 저장 시도 기한. 풀 획득·SQL·commit·반환 포함 |

전체 tail 정리는 기존 `RUN_CLEANUP_TIMEOUT_SECONDS` 정책의 적용도 받는다.
Python coroutine은 강제 종료할 수 없으므로 DB 드라이버가 취소에 응답하지 않으면
기한만으로 자원을 반환했다고 간주하지 않는다. 기존 정책대로 unhealthy 상태에서
실제 종료를 기다리며 운영 복구가 필요하다. timeout은 자동 재제출 신호가 아니다.

바이트 상한은 payload를 한 번씩 계산한 값으로 **프로세스 RSS 한도가 아니다**.
Python 객체, join 중 임시 문자열, SDK가 전달한 원본 및 SDK/HTTP 내부 버퍼는 별도다.
동시 Run 수가 늘면 각 Run의 예산도 합산된다. 상한 때문에 토큰을 임의 삭제하지 않지만
실패한 Run의 미저장 조각을 crash 이후 복원하는 기능은 추가하지 않았다.

## 검증

- 단위·기존 cleanup 집중 테스트 36개 통과(2.77초).
- 실제 PostgreSQL 포함 39개 집중 테스트 통과(6.95초).
- 추가 Run 실패 연계를 포함한 PostgreSQL 5개 테스트 통과(5.59초).
- DB 반환 중 반복 취소 및 남은 바이트 공간 검증을 포함한 최종 단위·cleanup 38개 통과(2.51초).
- 1차 전체 회귀: 602 passed + 2 subtests, 53 warnings, 215.70초. 이후 부분 공간 처리 보완을 포함한 최종 결과는 아래에 기록한다.
- 연속 출력 중 flush, 바이트/조각 수 제한, 큰 Unicode 조각 분할, 대기 해소,
  실제 LangChain callback 오류 전파, writer 장애/시간 초과, 꽉 찬 버퍼 종료를 검증했다.
- 격리 PostgreSQL의 실제 Task 행 lock으로 writer를 막은 뒤 적재량 제한과 누락 없는
  재개를 검증했다. DB write timeout 시 rollback, offset 미전진을 확인했다.
- 실제 Worker가 토큰 commit 후 terminal event를 기록하고, 공개 Run cursor로 재생하는지
  확인했다. 장애 시 성공·자동 재시도 이벤트가 없고 recovery guard를 유지함을 확인했다.

[변경 전후 비교 보고서](../reports/token-event-buffer-2026-09-29/README.md)

## 제한·후속

- 정상 출력 중 더 자주 저장하므로 기존에 늦게 한 번 저장하던 경우보다 DB 쓰기가
  늘 수 있다. 이번 목적은 전달 시점과 메모리 상한의 보장이다. DB 쓰기 감소를 주장하지 않는다.
- 버퍼가 가득 차면 모델 스트림 소비가 느려진다. 모델 제공자의 전송/버퍼 정책까지
  제어하지는 못한다. 실제 LLM·Executor·Redis·Kubernetes는 이번 검증에서 호출하지 않는다.
- UI 전달에는 DB 저장 외에도 026 SSE 병합 간격(기본 0.5초)과 네트워크 시간이 더해진다.
- 운영 지속 부하에서 값 조정이 필요하다. Pod 전체 메모리/DB/모델 용량 상한, 자동 복구 API,
  장기 이벤트 보존은 별도 후속 항목이다.

## 최종 검증 결과

- **603 passed + 2 subtests passed**, 53 warnings, 213.35초. API·Agent 전체 회귀 통과.
- 최종 코드의 단위·cleanup 검증 38개 통과, 실제 PostgreSQL 검증 5개 통과.
- 별도 테스트에서 실제 DB 행 lock·rollback과 callback 실패 후 Run 복구 보호를 검증했다.
- 기존 fixture의 migration upgrade/downgrade/upgrade 통과. 신규 migration은 없다.
- 변경 Python 파일 5개 문법 검사, `git diff --check` 통과.
- wheel 격리 검증: API 34경로, 역할 Agent 7개 생성, mock 그래프 6단계 실행 통과.
- 이전/이후 × 연속 출력/저장 지연 × 3회 = 총 12개 오프라인 비교 사례에서 내용 일치.
- 실제 LLM·Executor·외부 Redis를 호출하지 않았고, 기존 실행 중인 서비스는 변경하지 않았다.

```sh
# 외부 환경이 아닌, 일회용 로컬 identity_test DB를 먼저 지정한다.
PYTHONPATH=src python -m pytest src/api_service/test src/agent_service/agents/analysis/tests -q --disable-warnings --maxfail=2
PYTHONPATH=src python scripts/benchmarks/token_event_buffer.py --baseline 039d516 --output /tmp/token-buffer-comparison.json
```

[최종 검증 메타데이터](../reports/token-event-buffer-2026-09-29/validation.json)
