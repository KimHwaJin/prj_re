# 074 SQL 준비·ORM 결과 비용 분해

상태: 진단·검산 완료 / 서비스 runtime 불변 / 베이스 미병합·미푸시·미배포.
브랜치: `feature/sqlalchemy-cost-diagnosis`. 부모 `f1ca885`(073 완료), 측정 소스 `522e52a`.

## 문제 → 이번 작업

073 이후 남은 SQLAlchemy CPU가 SQL 준비와 ORM 결과 생성 중 어디서 쓰이는지 분리되지 않았다. opt-in benchmark cProfile에 함수 caller 메타데이터만 추가하고, 메인·offload의 CPU 시계와 exclusive self CPU를 대조했다. request/prompt/parameter 값은 기록하지 않는다. `src`·서비스 설정·API·모델 호출 수·실행 한도는 바꾸지 않았다.

## 확인한 비용 → 판단

표준10명 메인 thread CPU6.783초 중 SQL expression/cache 모듈0.734초·compiler0.078초, ORM loading 모듈0.104초였다. DB 실행/결과/bridge0.888초와 ORM transaction/상태/flush 비용도 분산되어 있다. loading 모듈 수치는 driver/JSON/builtin을 포함한 전체 읽기 비용이 아니며, 모듈 비용을 제거 가능한 개선율로 해석하지 않는다.

표준10명 Log/Event 쌍 조회410회·Task 연결 조회230회를 확인했다. caller에는 LogService.create·UserRepository.get_active·TaskService.attach_graph_task_for_run·lock_session의 반복 SQL builder가 나타났다. 같은 binding/transaction 중복이나 불필요 조회를 입증한 수치는 아니다. warm 측정 중 SQLCompiler 생성200회와 INSERT compiler200회, SELECT compiler 방문 기록 없음으로, 모든 조회가 매번 SQL 문자열로 재컴파일된다는 추측도 배제했다.

다음 후보는 Log/Event 및 공통 User/Session 읽기의 SQL 구조 재사용을 한 묶음으로 검토하는 것이다. 매번 새 바인딩·실제 SELECT·권한/잠금/복구/첫 payload를 보존한다. 결과 캐시·Task 연결 조회 무조건 생략은 선택하지 않는다. 후보의 속도 효과는 미측정이며 다음 profiler off 전후 비교로 채택 여부를 결정한다.

## 검증 → 한계

표준1/10명·큰 결과1명 CPU 진단3회와 off 대조10명1회, 총4시도22흐름 완료·오류0. 모델0/HTTP Executor 합성 출력이며 실제 실행 계산 부하는 없다. 진단 테스트3개 통과, raw/source/SQL 목적/CPU 분류/결과·drain 검산 완료. profiler on10명7.147초·off4.038초는 진단 오버헤드 설명이며 성능 개선 수치가 아니다. 프로덕션 변경과 전후 속도 개선 주장은 없다.

073 runtime과066/067/071/072 보류를 유지했다. 원래 checkout/.env와 기존18개 서비스는 보존하고 임시 자원만 제거했다. 큰 JSON 컬럼의 소비/bytes·driver 비용, 실제 Pod/지속 부하/HPA는 미확정이다. 모델 호출 수/Registry/Workflow CRUD/운영의 후순위는 유지한다.

[상세 분석·원본·재현·검산](../reports/sqlalchemy-cost-2026-10-04/README.md).
