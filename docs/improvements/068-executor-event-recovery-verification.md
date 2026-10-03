# 068 Executor 이벤트 복구 경로·중단 원인 추적

| 항목 | 내용 |
|---|---|
| 상태 | 경로 수정·105회귀·역순 E2E·50명 진단2회 완료 / 과거 최초 원인 미확정·066 후보 병합 보류 |
| 브랜치 | feature/executor-event-recovery-verification |
| 부모 | d0d06fb (067) |
| 측정 구현 | 2da80a9e97ab378f5271c5a15ffecd01f16342b7 |
| 범위 | 설정/Worker 이력 GET, benchmark 원인 계측, 실제 PG pending write 버전 경계 |

## 기존 문제 → 변경

통합 base가 root 주소여도 이력 요청은 /executions...에 고정돼 제출 API의 /api/v1 prefix와 달랐다. EXECUTOR_EXECUTION_PATH + /events에서 유도하고 필요할 때만 공통 EXECUTOR_EVENTS_PATH로 지정한다. 올바른 상대 template을 startup에서 확인하며 기존 API-prefixed base/경로 설정도 유지한다. Inbox·순번 gap 보존·원자 routing·세션 순서·소유권·receipt·POST 자동 재제출 금지·durability sync 정책은 바꾸지 않았다.

benchmark에서 command/event 순번별 HTTP status·expected_version·key hash·예외 타입/frame을 기록하도록 선택 계측을 추가했다. 실패 deadline에서는 API 종료 전에 전체 세션 live 증거를 보존한다. 제출 body/code/key 원문을 기록하지 않고 운영 entrypoint에 이 계측을 넣지 않았다.

## 검증과 영향

105 관련 회귀(skip0)·독립 원본/SHA/집계 검산98개 통과. 실제 PG에서 역순/중복/sequence 충돌, 동시 routing, gap 보충/실패 backoff/늦은 도착/페이지 catch-up을 확인했다. 실제 HTTP 소켓에서 기본 root base의 /api/v1/executions/{id}/events 호출과 1→2→3 처리를 확인했다.

실제 API/PG/Redis/Worker/SSE의 역순1명은 리포트까지 완료했다(발행1,6,5,4,3,2,9,8,7,10; history GET0). 50명 대형20 Operation 두 번은 100개 흐름/2,100 POST 모두202·오류0·owner/recovery/Inbox0. 이전 event49/expected_version33 경계도100회 모두202였다. 지연0 모델/합성 HTTP Executor이며 submitted Python 실행 시험이 아니다. 진단 계측 추가 및 baseline 대조 부재로 이번 시간을 성능 향상으로 해석하지 않는다.

## 남은 제한 → 다음

067 중단의 원래 HTTP/예외 사슬이 없고 이번 재현도 실패하지 않았으므로 최초 원인은 여전히 미확정이다. 저장 candidate와의 인과나 이벤트 순서 문제를 확정하지 않는다.

실제 새 tagged pending write를 구 LastValue graph가 읽으면 observations가 dict가 된다. 새 reader는 동일 PG에서4개 facts를 한 번만 복원하고 finalize1회로 재개한다. 새 reader의 과거 호환은 확인했지만 구 reader 역방향 호환/혼합 버전 배포의 자동 보호는 없다. 066 후보 병합 보류를 유지한다. 필요하면 이력 경로 수정 부분은 독립 반영할 수 있다.

후속은 저장 후보 채택·버전별 실행/drain/rollback 계약과 최초 오류 증거 확보다. 다음 성능 후보는 API CPU sampling이며 모델 호출 수 최적화와 광범위 운영 보완 보류는 유지한다. 기존18개 서비스/checkout/.env를 보존했고 소유한 임시 컨테이너/볼륨/DB만 정리했다. 베이스 병합·푸시·배포 미수행.

[상세 결과/원본/재현](../reports/executor-event-recovery-2026-10-04/README.md), [설정·배포 버전 경계](../deployment-configuration.md).
