# 072 TaskEvent 원자 저장과 생성값 반환

상태: 후보2개 구현·회귀·동일 조건 측정 완료 / 처리량 개선 미입증·채택 보류·미병합·미푸시·미배포.
브랜치: feature/task-event-atomic-insert. 부모 f229b9e(070 runtime+071 기록), 첫 구현 fb0fc38, 최종 구현 975edfb3b0c750dfff984386cfa6f1d2147717aa.

## 문제 → 개선 후보

Run→Task SELECT·sequence UPDATE·TaskEvent INSERT의 왕복3회를 같은 Task row lock을 유지하는 CTE1회로 묶었다. 첫 후보의 전체 Event RETURNING과 최종 후보의 생성된4필드만 RETURNING을 별도로 비교했다. payload는 첫 입력을 유지하고 ORM identity map·flush·commit/rollback·server timestamp·pending 의존 경로 fallback을 보존한다. API/Agent·checkpoint·schema·환경변수·capacity·pool과 보호 쿼리는 그대로다.

## 검증 → 실제 효과

첫 후보147회귀·18성능시도424흐름·검산1,694개, 최종161회귀·portable probe1개·18성능시도424흐름·검산 1,694개 완료. 모든 성능 시도는 완료했으며 개발 과정의 strategy/테스트 PK/commit 후 deferred 필드 오류와 수정 검증은 별도 원본으로 보존했다. 회귀 두 집합을 unique test 개수로 합산하지 않는다.

동일3이벤트 신규SQL16→10/replay4→4/commit1 유지.50명 첫 trial 물리 왕복2,000감소·논리 Task UPDATE/Event INSERT1,150개씩 유지. 표준50명3회 평균 완료 16.452→17.132초(4.13% 증가), API CPU 15.771→16.242초(2.99% 증가). 표준1/10/30/50·보조20 Operation·profiler는 [상세 보고서](../reports/task-event-insert-2026-10-04/README.md)에서 분리한다. 첫 후보도 표준50명16.465→16.600초로 개선되지 않았다.

## 판단 → 제한 → 다음

SQL 감소가 처리량으로 이어지지 않아 두 후보를 채택하지 않는다. 다음 작업은 f229b9e의070 runtime에서 분기하고 필요한072 기록만 가져간다. 다음은 결과 재개1회 안의 권한/상태/메시지 투영 조회를 목적별로 대조하여 실제 중복 및 CPU 비용부터 검증한다. 안전하게 재사용 가능한 구간만 후보를 만든다.

유한 localhost 시험이고 실제 Pod/HPA·지속 유입·원격 DB·멀티 replica·RSS/실제 LLM·Executor 연산은 미검증이다. 모델/Registry/Workflow CRUD/운영·066 채택/버전호환·067 과거 원인 보류는 유지한다. 기존18서비스·원래 checkout/.env를 보존하고 시험2컨테이너/volumes만 정리했다. [첫 후보 별도 기록](../reports/task-event-full-return-2026-10-04/README.md).
