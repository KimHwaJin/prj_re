# 033 — 결과 저장 DB 왕복 축소·동일 조건 A/B

- 상태: 1차 성능 개선·A/B·전체 회귀·wheel 검증 완료. 베이스 병합·배포 미수행.
- 브랜치: `feature/projection-db-roundtrips`
- 출발 commit: `8836df6`
- 날짜: 2026-09-30

## 목표와 범위

사용자 우선순위를 처리량·성능으로 전환한다. 운영/장애 대응 신규 기능은 후순위다. 정상 결과 저장 경로의 중복 SELECT, INSERT 후 재조회/refresh, 동일 Task 연결 commit을 줄인다. 기존 로그/이벤트 원자성·멱등성·접근 권한·세션 잠금은 유지한다.

한 번에 전체 저장 구조를 교체하지 않는다. 새 로그 INSERT RETURNING 재사용, 메시지 세션 검증/잠금 결과 재사용, 저장된 생성 값 반환, Task 연결 조회·no-op 경로부터 줄인다. LLM 호출/Agent 업무 순서·Worker 동시성은 변경하지 않는다.

## 검증 계획

- 변경 전 `8836df6`과 변경 후 소스를 고정하고 같은 PostgreSQL/풀/슬롯/5초 LLM/4회 호출·SSE 흐름으로 1·10명 비교.
- DB 저장 구간의 SQL/commit/시간을 별도 관측하고 전체 큐/모델 대기와 구분.
- 정상 생성의 반환 ID/sequence/타임스탬프·내용, 동일 key 재처리, 메시지 소유권/동시 생성, graph task 연결 일치·충돌 검증.
- 기존 DB 수명·로그 원자성·checkpoint 저장 복구·전체 회귀 확인.

작업 commit은 이 기록을 포함한 feature commit이다.


## 구현·현재 검증

- 새 Agent 로그 INSERT의 RETURNING 객체를 바로 사용한다. 중복/경합 또는 기존 불완전 로그일 때만 기존 잠금·재조회 경로를 사용한다. expire_on_commit=True인 외부 session factory의 refresh 호환도 유지한다.
- MessageService는 기존 lifecycle 검증의 마지막 세션 조회에서 필요한 row lock까지 얻고 그 객체를 사용한다. 메시지 INSERT가 반환한 ID/sequence/시간값으로 DTO를 만든 후 commit하므로 후행 refresh를 없앤다. 소유권·사용자/프로젝트 상태·이동/삭제 경합 검사는 유지한다.
- Task 연결은 Run→Task 개별 조회 대신 join 조회를 사용한다. 이미 같은 graph_task_id이면 변경·commit 없이 반환하고 다른 ID 충돌은 거절한다.
- 새 schema/config/환경변수/큐/Worker는 없으며 Agent 흐름과 모델 호출 수는 변경하지 않았다.

동일 probe의 실제 SQL 비교:

| 작업 | 이전 SQL | 이후 SQL | 실제 DB commit 이전→이후 |
|---|---:|---:|---:|
| 새 로그+이벤트 | 8 | 5 | 1→1 |
| 기존 완성 로그 | 1 | 1 | 0→0 |
| 새 메시지 | 11 | 9 | 1→1 |
| 기존 메시지 | 8 | 7 | 0→0 |
| 새 Task 연결 | 3 | 2 | 1→1 |
| 기존 동일 Task 연결 | 2 | 1 | 1→0 |

변경 후 관련 테스트 17개 통과(10.67초), 변경 전 동일 SQL probe 3개 통과(3.99초, 2개 제외). 전체 소스 653 passed, 53 warnings, 2 subtests passed(267.29초). 경고는 기존 checkpointer 없는 하위 그래프의 durability 설정 관련이다. wheel 변경 모듈 일치·테스트 제외 확인 완료.

HTTP A/B 완료: 1·10명 조건 각각 전후 2회, 44명/176개 실행 구간·모델 호출 정상. Worker SQL 344→281회/사용자(18.3% 감소), commit 메서드 호출 51→48회. 저장 시간은 1명 0.592→0.429초, 10명 0.488→0.415초로 줄었다. 전체 평균은 1명 24.534→24.229초, 10명 48.220→48.179초이며, 10명 배치 처리율은 약 0.180명/초로 사실상 같다. 작은 반복 수로 운영 처리량 증가를 주장하지 않는다.

[상세 보고서·원본·검산](../reports/projection-db-roundtrips-2026-09-30/report.md), [계측/재현 도구](../../scripts/benchmarks/projection_roundtrips/README.md).

이번은 결과 저장 경로의 중복 왕복 축소이며 전체 성능 최적화 완료는 아니다. 반복 검증 공유·저장 단위 묶기·동시성별 처리량/자원 검증이 남았다. Agent 호출/실행 단위는 2단계로, 신규 에러 처리/운영성 작업은 마지막 단계로 보류한다. 추가 DB migration/config는 없다. 기존 보호 장치를 유지했고 원본 체크아웃·기존 서비스는 변경하지 않았다. 임시 PostgreSQL·볼륨은 정리했다. 베이스 병합·push·배포는 하지 않았다.
