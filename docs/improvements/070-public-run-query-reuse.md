# 070 공개 Run 상태 조회문 재사용

상태: 구현·관련 회귀·동일 조건 전후 비교 완료 / 베이스 미병합·미푸시·미배포.
브랜치: `feature/public-run-query-reuse`. 부모 `61c25f7`(069 완료), 구현 `0a1fe5b`.

## 기존 문제 → 실제 변경

PublicRunService가 상태를 읽을 때마다 root/latest alias·최신 invocation 하위 조회·scalar Bundle/Task join을 구성했다. DB SQL 준비 캐시와 별개로 Python 구문·cache key 구성 비용이 반복됐다.

`runs/public_state_query.py`에 불변 배치/단건 조회문을 선언하고, PublicRunService는 UUID 목록·Run ID·session ID만 매 호출 새로 바인딩한다. 이전 `_snapshot_query`는 제거했다. 공유하는 것은 SQL 구조이며 결과·권한·DB session을 캐시하지 않는다. 여전히 매번 실제 DB SELECT를 수행하고 root/latest/Task는 한 statement snapshot이다. legacy ID 해석, model/checkpoint metadata·Task 없는 기록, 권한 확인·SSE 동작을 보존한다.

## 검증 → 성능 영향

실제 PostgreSQL/API/SSE 회귀51개(skip0) 통과. 여러 resume·SSE cursor/notify/동시 탭·권한 폐기·모델 선택·DB 연결 반환을 확인했다. 신규 회귀는 가변 ID 목록·외부 commit 후 동일 reader의 최신 값·dirty ORM 객체 보존·여러 사용자/세션/Run 동시 바인딩 분리를 검증한다.

한도20·CRUD10/overflow0·checkpoint4·event4·동일 SSE/notify·LLM0/HTTP Executor 합성 출력을 유지했다. 표준4 Tool/2 Operation의1/10/30명 각 전후1회,50명 각3회에서 전체 완료는1.768→1.657초,4.312→3.527초,13.037→9.845초,50명 평균21.529→16.610초다. 50명 CPU20.761→15.792초로23.9% 감소했다.

보조20 Operation의10명은15.855→14.775초(6.8% 감소),1명은4.186→4.215초로 개선이 없었다. 1명 보조 CPU는6.9% 증가했다. 워크로드에 따라 효과가 다르다. CPU 프로파일에서는 구문 builder가 측정 중271→0회로 줄었으며 초기 준비 단계에 한 번 만들어졌음을 확인했다. profiler on 표본은 속도 평균에서 제외했다.

전체18회·424흐름(표준 성능12회/382흐름·보조4회/22흐름·진단2회/20흐름), 오류0, 원본/SHA/소스/harness/결과/집계 검산1,644개 통과. 시험 자원만 정리하고 기존18개 서비스와 원래 checkout/.env를 보존했다.

## 남은 제한 → 다음 작업

유한 로컬 burst 결과다. 실제 모델/Executor 계산·Pod 자원 제한/지속 유입/HPA 및 여러 replica의 DB 연결 예산은 별도다. 이번 개선율을 운영 전체 지연에 그대로 적용하지 않는다. 모델 호출 수·동시 실행 한도·환경설정·checkpoint 포맷을 바꾸지 않았고066 저장 후보 보류는 유지한다.

다음은 잔여 SQL 실행/ORM 처리·DB roundtrip의 목적별 비용 분해다. 남은 비용이 모두 불필요하다고 간주하지 않고, 개선 가능한 단일 구간과 보존해야 할 권한·멱등성·순서 조건을 먼저 확인한다. 모델 호출 최적화/Registry/Workflow CRUD/광범위 운영 보완의 기존 후순위는 유지한다.

[상세 비교·원본·재현·검산](../reports/public-run-query-2026-10-04/README.md).
