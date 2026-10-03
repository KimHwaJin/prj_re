# 071 결과 로그 replay 확인 일괄 조회

상태: 후보 구현·관련 회귀·동일 조건 전후 비교 완료 / 속도 이득 미입증으로 채택 보류·미병합·미푸시·미배포.
브랜치: `feature/result-log-replay-batch`. 부모 `89f65ea`(070 완료), 구현 `9673b4e`.

## 기존 문제 → 실제 변경

공개 이벤트 결과를 저장할 때마다 로그·연결 TaskEvent 존재를 개별 SELECT했다. 동일 transaction에서 Run별 대상 키를 묶어 완성된 쌍만 조회하도록 GraphResultBatch를 확장하고 planning persistence에 연결했다. SQL 구조는 재사용하며 UUID/키와 결과는 transaction마다 새로 읽는다. 512키 chunk는 bind 크기 제한이다.

Run advisory barrier·세션/권한·입력 event 순서·sequence 할당·동시 commit·첫 payload·누락 복구를 유지한다. 신규/불완전 로그는 기존 conflict/repair 경로이며 prepared 키 밖은 기존 단건 조회다. 완료/rollback·타 session/Run으로 lookup을 넘길 수 없다. Agent/LLM·checkpoint·API·schema·환경변수·풀·총한도는 그대로다.

## 검증 → 실제 성능 영향

관련56개 회귀(skip0) 및 동일 입력 portable probe 전후 각1개가 통과했다. 최초3이벤트 SQL16→14, replay4→2, commit1을 확인했다. 초기36통과·2 fixture 오류는 시험 파일의 잘못된 설정 키를 제거하여 해결했고 initial 로그도 보존했다.

표준50명은 시간·CPU 평균 차이가 작고 전후 범위가 겹쳐 속도 개선을 입증하지 못했다. 동일 한도20/CRUD10·checkpoint4·event4·LLM0/HTTP Executor fixture 표준50명3회 평균 완료16.362→16.337초(0.2% 감소), API CPU15.694→15.636초(0.4% 감소)다.1/10/30·보조20 Operation1/10 및 CPU profiler on 진단은 상세 보고서에서 별도 비교한다. profiler 속도를 평균에 섞지 않는다. 전체18회·424흐름·오류0·검산1,692개다.

## 채택 판단 → 남은 제한 → 다음 작업

50명 첫 trial의 중복 확인 조회900회를 줄였지만 표준50명 속도/CPU는 사실상 같고 보조 결과도 혼재한다. 추가 lookup/payload 보유의 복잡성 대비 속도 이득을 입증하지 못하여 채택을 보류한다. 코드·증거를 이 브랜치에 보존하며 다음 성능 작업은 부모89f65ea(070)의 운영 소스에서 분기하고 필요한 검증 기록만 가져간다. 후보를 다음 작업의 runtime 기본값으로 승계하지 않는다.

유한 로컬 시험이며 실제 모델/Executor·Pod CPU/memory·지속 유입·HPA·replica DB 예산은 미검증이다. transaction 동안 ORM payload를 보유하므로 장기 history RSS 상한은 별도다. 512키 제한을 전체 메모리 상한으로 오인하지 않는다. 확인된 SQL 감소와 실제 시간/CPU 결과를 분리한다.

다음은 남은 INSERT/sequence/Task 식별·메시지/권한 비용의 목적별 분석이다. 안전한 묶음 처리와 실제 이득이 확인되는 구간만 선택한다. 모델/Registry/Workflow CRUD/운영 보완·066 보류·067 미확정 원인 순서는 유지한다. 시험 자원만 정리하고 기존18개 컨테이너·원래 checkout/.env를 보존했다.

[상세 비교·원본·재현·검산](../reports/result-log-replay-2026-10-04/README.md).
