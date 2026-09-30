# 034 — 결과 단위 검증 공유·배치 저장

- 상태: 구현·집중/전체 회귀·1/10명 A/B·wheel 검증 완료 / 베이스 병합·배포 미수행
- 브랜치: `feature/projection-batch-storage`
- 출발 commit: `eef0bb7`
- 날짜: 2026-09-30

## 문제와 범위

033 이후에도 한 그래프 결과의 메시지마다 사용자·프로젝트·세션 검증을 반복하고 로그/메시지마다 commit한다. 기본 dispatcher의 한 결과 처리만 짧은 트랜잭션으로 묶는다. Run 로그 barrier → 사용자·프로젝트·세션 barrier → 로그/Task 행 순서로 잠그고, 검증한 세션은 같은 DB transaction 안에서만 재사용한다. 공개 CRUD·사용자 정의 dispatcher의 저장 계약과 Agent/모델 흐름은 유지한다. Task identity 연결·LangGraph checkpoint는 별도 저장 경계다.

## 검증 계획

실제 PostgreSQL에서 정상 저장/재처리 SQL·실제 commit 수, 동시 중복 입력, 중간 실패·취소 시 rollback, 사용자 trigger 연결, 권한/프로젝트 검증, 읽기 일관성을 확인한다. 전체 회귀 및 기존 5초 LLM·4슬롯·1/10명 HTTP A/B로 잠금 경합·저장 시간·큐·완료 시간을 확인한다. 코드·DB·조건을 고정하고 실패/누락 결과는 성공으로 처리하지 않는다.

## 구현과 집중 검증

- 기본 GraphPersistenceDispatcher만 결과 단위 commit/rollback한다. custom dispatcher 및 개별 CRUD는 기존 호출 계약을 유지한다.
- GraphResultBatch는 같은 AsyncSession·transaction·사용자·세션·프로젝트에서만 검증 결과를 사용한다. commit 이후 재사용을 거절한다.
- MessageService의 잠금 뒤 insert를 내부 helper로 분리하고 기본 그래프 결과에서 commit을 상위로 넘긴다. 변환/메시지 키/순서/leaf/title 정책은 유지한다.
- 로그·대응 이벤트의 멱등 저장은 유지하며 배치에서 commit을 지연한다. Run 로그 advisory lock을 공유해 배치와 개별 로그 writer의 역순 경합을 직렬화한다. 개별 새 로그는 이 lock 때문에 SQL이 5→6회로 1회 늘고, 완성 로그 재조회는 1회 그대로다.
- 원본 사용자 메시지를 그래프에서 저장하는 경로의 Run/Task trigger 연결도 같은 transaction에 참여한다.
- Task graph identity 연결은 기존 별도 commit 경계다. Agent 상태/checkpoint/최종 Run 상태 전체를 하나의 transaction으로 바꾸지 않는다.

40개 집중 테스트 통과(29.52초). 중간 오류/취소 rollback, 재처리, 같은 결과 6개 동시 요청, 사용자/프로젝트/세션 권한, 사용자 trigger rollback, 외부 reader의 부분 결과 비노출, batch와 개별 log writer 경합, commit 뒤 batch 재사용 거절, 기존 짧은 DB 세션 검증을 포함한다. 최초 경합 실험에서 새 batch가 세션 row lock을 잡은 채 로그 lock을 기다리는 교착을 발견했고, Run 로그 lock을 먼저 얻도록 수정한 뒤 회귀 테스트로 고정했다.

동일 결과(assistant 메시지 4개 + 시작 로그) 기준 새 저장: SQL 65→44 / 실제 engine commit 9→1. 재처리: SQL 37→16 / commit 1→1. 절감 21회는 반복 세션 검증·잠금 28회를 공유 검증·잠금 7회로 바꾼 부분이다. 로그/이벤트/메시지 개수와 저장 payload·순서는 유지한다.

전체 회귀: 664 passed, 53 warnings, 2 subtests passed (306.90초). 경고는 기존 checkpointer 없는 하위 그래프의 durability 경고다. 패키지 검증: 변경된 운영 모듈 8개가 wheel과 바이트 단위로 일치하고 테스트 패키지는 제외됐다. 운영 코드 변경 후의 이 suite 결과를 기준으로 소스를 고정해 A/B를 완료했다.

## HTTP A/B 결과와 해석

033 `eef0bb7` → 개선 Git tree `9778a253af4d011c51c0c077c07ed4fe0f6562f0`을 비교했다. 후자는 최종 작업 commit의 운영 코드 8개 파일과 동일하다. API 1프로세스·Run 4슬롯·LLM 호출당 5초/사용자당 4회·SSE·Workflow 승인 대기까지의 동일 조건이다. 1/10명 각각 전후 2회, 총 8회·44명·176 실행 구간이 모두 성공했다. 모든 trial의 종료 및 데이터 건수·계측을 검산했다.

| 사용자 수 | 결과 반영 평균 초 | 전체 완료 평균 초 | batch 처리량 명/초 |
|---|---:|---:|---:|
| 1 | 0.774 → 0.270 | 25.08 → 24.36 | 0.0399 → 0.0410 |
| 10 | 0.403 → 0.273 | 48.63 → 48.29 | 0.1784 → 0.1795 |

Worker SQL 281→225회/사용자(19.9% 감소), commit 메서드 호출 48→29회(39.6% 감소)가 모든 반복에서 동일했다. 메서드 호출과 실제 DB commit은 구분한다. 1명 baseline 저장 시간은 반복별 0.462/1.086초로 변동이 커 평균 65.1% 감소를 고정 효과로 일반화하지 않는다. 10명 결과 반영 평균 감소는 32.4%다. 전체 응답/처리량 개선은 작고, 큐·초기화·알림 변동도 있어 전체 시간 차이를 모두 이번 코드의 효과로 돌리지 않는다.

[상세 보고서·원본·검산](../reports/projection-batch-storage-2026-09-30/report.md).

추가 schema/config/환경변수와 Agent 흐름 변경은 없다. 기본 batch의 같은 Run 로그 writer만 직렬화하며 서로 다른 세션/Run 전체를 하나의 잠금으로 직렬화하지 않는다. 큰 결과/더 높은 동시성의 잠금 보유 시간과 CPU·메모리·DB 수용량 검증은 남아 있다. 아직 운영 처리량을 보증하지 않는다. 다음 성능 단계에서는 동시 실행 수에 따른 처리량·자원 사용량을 검증한다. 새 Agent 흐름은 2단계, 신규 운영/에러 대응은 마지막 순서라는 사용자 우선순위를 유지한다.

작업 commit은 이 기록을 포함한 feature commit이다. 베이스 병합·push·배포 미수행.

검증용 PostgreSQL 컨테이너·볼륨은 정리했다. 기존 서비스 컨테이너와 원본 체크아웃은 변경하지 않았다.
