# 토큰 버퍼 변경 전후 비교

이전 기준 `039d516`, 변경 후 `feature/token-event-buffer`. 동일 인터프리터에서 버퍼의
저장 함수를 제어 가능한 coroutine으로 바꾸어 **저장 스케줄과 적재 제한**을 비교했다.
실제 DB·LLM·HTTP 성능 측정이 아니다. 각 조합 3회씩 실행했으며 원문 누락 여부를 검사했다.

| 조건 | 이전 | 이후 |
|---|---:|---:|
| 50ms 저장 간격, 5ms마다 1글자 연속 입력: 첫 저장 중앙값 | 477.8ms | 50.9ms |
| 위 연속 출력이 끝나기 전에 발생한 저장 | 0회 | 8회 |
| writer를 100ms 막고 1000개 조각 입력: 적재 후 미저장 조각 | 1000개 | 8개 |
| 위 조각의 UTF-8 payload 합계 | 2000 bytes | 16 bytes |
| 저장 재개 후 1000개 내용 일치 | 통과 | 통과 |

적재 제한 비교는 동작을 드러내기 위해 상한을 **64 bytes / 8 items**로 낮춘 시험이다.
운영 기본값은 256 KiB / 1024 items이다. 이후 코드에서는 8개가 차면 생산자가 기다리며,
writer를 풀어 주면 모든 조각을 저장한다. 이전 코드는 writer가 막혀도 1000개를 받는다.

연속 출력 시험에서는 80개 조각을 입력한 뒤 close한다. 이전 코드는 문자 수 기준에
미달하고 입력 사이 간격도 짧아 출력 종료 후 tail flush에서 처음 저장했다. 이후 코드는
약 50ms 후부터 저장했다. 즉 이전의 **입력 공백 기준**이 **첫 적재 후 경과 시간 기준**으로 바뀌었다.

## 해석의 한계

- Agent 전체 실행 시간, LLM 추론 시간, 실제 SQL 처리량·DB pool·프로세스 RSS를 측정하지 않았다.
- 이후 코드의 전체 저장 횟수는 9회, 이전은 1회다. 조기에 전달하기 위해 쓰기 횟수가
  늘어나는 조건이며 DB 부하가 무조건 감소하는 변경이 아니다.
- 실제 DB lock·commit·rollback·Run 종료 순서는 별도 PostgreSQL 통합 테스트에서 검증했다.
- 로컬 event loop 스케줄링 오차가 있다. 운영 환경의 지연 보장이나 처리량 한계로 일반화하지 않는다.
- 배포 환경·실제 모델의 스트리밍 동작·장시간 느린 DB 시험은 수행하지 않았다.

## 재현

```sh
PYTHONPATH=src python scripts/benchmarks/token_event_buffer.py --baseline 039d516 --output /tmp/token-buffer-comparison.json
```

이 스크립트는 Git에서 이전 파일을 읽어 임시 모듈로 실행하며 checkout을 변경하지 않는다.
설정 snapshot은 외부 자원이 없는 오프라인 시험 안에서만 사례별로 초기화한다.

[측정 원본](comparison.json) · [재현 스크립트](../../../scripts/benchmarks/token_event_buffer.py) · [구현 기록](../../improvements/027-token-event-buffer.md)
