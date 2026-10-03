# 063 Checkpoint 실측과 기존 풀 병렬 접근

| 항목 | 내용 |
|---|---|
| 상태 | 구현·격리 기능/성능 검증 완료 / 베이스 미병합·미푸시·미배포 |
| 브랜치 | feature/checkpoint-storage-profile |
| 성능 기준 | 전 45530e6 / 후보 runtime a6212c2 |
| 날짜·근거 | 2026-10-04 KST / 리뷰 6단계 D-10·F-01 |

## 기존 문제

Checkpoint 지연이 큰 상태의 직렬화 때문인지 DB 대기 때문인지 확인되지 않았다. 실제 `AsyncPostgresSaver` 3.1.2는 풀 상한이 4여도 같은 Saver의 cursor I/O를 공통 잠금으로 처리했다. 50명 보조 측정에서 Saver 함수 시간 누계의 89.8%는 이 잠금 대기였고, 잠금 안 최대 동시 진입은 1개였다.

## 실제 변경

- 테스트용 SQL·함수·직렬화·잠금 계측을 추가했다. 고유 thread/channel/version, 최신 참조와 과거 버전, parent write, JSON 구성값을 나누어 검산한다.
- `PooledAsyncPostgresSaver`는 호출마다 공식 Saver 객체를 만들고, **pool과 serializer는 lifespan의 같은 객체**를 사용한다. 공개 메서드를 위임하며 SQL·스키마·승인 원문·복구 기록·durability sync를 유지한다. 라이브러리의 private cursor와 SQL은 수정하지 않았다.
- `create_checkpointer`의 factory를 교체했다. API·Agent 흐름·Command/동일 session 소유권·환경변수·풀 상한·Worker 슬롯·모델 호출 수는 유지한다.
- 성능 측정 후에는 `alist()`의 이력 iterator를 중간에 닫아도 내부 iterator와 연결을 즉시 반환하도록 보완했다. 측정한 `aget_tuple/aput/aput_writes`는 이 후속 보완에서 변경하지 않았다.
- Benchmark의 `--repeat`와 `--trial-index`를 함께 쓸 때 raw 반복 식별자가 고정되던 오류도 수정했다. 이번 50명 원본의 반복 ID는 1/2/3으로 대조했다.

## 사용자 동작과 측정 결과

전후 모두 1프로세스·공통 실행 자리 20개·checkpoint 풀 상한 4·모델 호출당 5초·HTTP Executor fixture다. 다른 세션의 checkpoint 함수가 기존 풀의 허용 범위 안에서 병렬로 연결을 사용한다. 풀 자체를 매 호출 생성하거나 폐기하지 않는다. HITL/Executor 대기 시 connection/slot 반환과 같은 세션 입력 잠금은 유지한다.

50명 각 3회에서 전체 평균은 56.39→54.78초(2.86% 단축), 저장 함수 외부 계측 누계는 사용자당 3.104→1.263초(59.31% 감소)였다. 함수 누계는 서로 겹치므로 E2E에서 그대로 뺄 수 없다. 후속 질문 10명은 36.06→36.43초로 개선되지 않았다. 풀의 실제 연결 수는 1→4, 설정 상한은 4 그대로다. 더 높은 DB 동시 사용 비용도 기록했다.

저장량은 정상 흐름당 약 676KiB·30 checkpoint였다. 최신 JSON과 참조 blob은 약 65KiB이고, 나머지는 과거 metadata/blob/write다. Msgpack 작업은 사용자당 약 1.2ms였다. 상태 reset 후에도 과거 버전은 남는다. 이전 제출 본문 8,045bytes가 후속 답변 상태에 남는 정리 후보를 확인했지만 이번에는 삭제하지 않았다. 저장량 감소는 이번 변경의 성과가 아니다.

## 검증·제한

- 기본 6회·후속 2회를 전후 각각 수행하고 기존 잠금 보조 2회를 추가했다. 18회·455 사용자 시나리오 모두 통과했다. 분석 Agent 소스 99개 hash 동일, 종료 owner/queue/CRUD checkout 0, raw/gzip SHA·저장 행·직렬화 바이트·독립 합계 검산을 확인했다.
- 실제 PostgreSQL 풀의 동시 checkout 2개와 상한 준수, 취소 후 반환, 그래프 HITL·재시작·초기/resume receipt를 검증했다. 첫 회귀는 55 passed·설정 부족 2 skipped, 추가 격리 설정 후 회귀는 31 passed·skip 0(풀 테스트 2개 중복)이었다. 처음 건너뛴 API 테스트도 추가 실행에서 통과했다.
- 실제 PostgreSQL에 repair 후보를 저장하고 풀을 재생성한 뒤 승인·continue·finalize·성공 단계 미재실행·receipt replay·history를 검증했다. 3개 Tool·역할 mock을 쓴 기능 probe로, 5초 모델의 HTTP repair 처리량과는 별개다.
- 이력 중간 종료 보완 후 관련 6개 테스트가 통과했다. 손상 capture 검증은 21 passed·normal capture에 적용되지 않는 hold 검사 2 skipped였다. 이 두 검사기는 이전 062의 burst 원본으로 별도 실행해 2 passed를 확인했으며, 이번 측정의 새 hold 증거로 사용하지 않는다.
- 큰 실제 출력·복수 repair·장기 원장·Pod/quota/HPA·실제 모델/Executor/MinIO/PVC는 미검증이다. 단일 프로세스·유한 burst·전후 고정 순서 시험이므로 운영 SLA나 전체 연결 상한을 보장하지 않는다.

[상세 비교와 근거](../reports/checkpoint-profile-2026-10-04/README.md), [runtime adapter](../../src/agent_service/runtime/langgraph/pooled_saver.py), [재현 방법](../../scripts/benchmarks/worker_e2e/README.md).

## 다음 작업

리뷰 6단계의 나머지인 역할별 상태 타입·읽기 경계와 실행 종료/새 요청의 오래된 필드 수명을 정리한다. 큰 version metadata와 다단계/repair 저장량은 필요한 변경 전에 확대 측정한다. 상태 구조 정리의 효과를 성능 향상으로 미리 단정하지 않는다. 원문 불변 저장소 계약 없는 hash 참조, 과거 checkpoint pruning, durability 완화는 적용하지 않는다. 기존 모델/Registry/Workflow/운영 보류는 유지한다.
