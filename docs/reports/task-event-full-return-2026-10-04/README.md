# 072 첫 후보: 전체 Event RETURNING

DB 왕복을 줄였지만 속도 개선은 입증하지 못했다. 표준50명3회 평균 완료16.465→16.600초, API CPU15.870→15.992초다. 최종 축소 반환 후보와 별도의 측정이며 결과를 합쳐 평균하지 않는다. [최종 보고서](../task-event-insert-2026-10-04/README.md)를 따른다.

## 고정 비교와 변경

이전은 f229b9e(070과 src 동일,071 runtime 제외), 후보는 fb0fc38이다. Run→Task SELECT·순번 UPDATE·Event INSERT를 CTE 한 문장으로 묶었으며 전체 Event ORM을 반환했다. 같은 Task row lock·sequence·log 연결·트랜잭션·첫 payload 보호는 유지한다. pending new/deleted ORM 객체가 있으면 기존 경로를 쓴다. 조회/쓰기가 같은 문장에 들어가도 논리 DB 쓰기 횟수는 줄지 않는다.

## 측정

| 시나리오 | 사용자 | 이전 완료 초 | 후보 완료 초 | 이전 API CPU 초 | 후보 API CPU 초 |
|---|---:|---:|---:|---:|---:|
| standard | 1 | 1.700 | 1.757 | 0.350 | 0.377 |
| standard | 10 | 4.082 | 4.016 | 2.996 | 3.198 |
| standard | 30 | 10.059 | 9.990 | 9.543 | 9.576 |
| standard | 50 | 16.465 | 16.600 | 15.871 | 15.992 |
| large20 | 1 | 4.188 | 4.230 | 1.374 | 1.407 |
| large20 | 10 | 14.667 | 15.069 | 13.506 | 13.964 |

LLM0·HTTP Executor fixture·총한도20/CRUD풀10/checkpoint풀4/event풀4/overflow0이다. 실제 API·PG checkpoint·Redis·HITL·Worker·SSE는 실행하고 모델 추론·Executor 실제 코드 실행은 제외한다. 표준50명만3회 산술평균,1/10/30 및20 Operation 보조는 각1회다. 완료는 cohort 시작부터 마지막 terminal SSE,CPU는 API process_time이다. 프로파일2회는 속도 분모에 포함하지 않는다. 실제 Pod/HPA·지속 부하·멀티 replica·RSS는 미검증이다.

18시도424흐름 모두 완료했다. 독립 검산1,694개 통과. 표준 속도12시도382흐름,대형4시도22흐름,프로파일2시도20흐름이다. 신규3이벤트 probe SQL16→10/replay4→4,commit1은 그대로다. 표준50명 첫 trial의 worker/event_graph SQL에서 기존 Run→Task 조회1,000·sequence UPDATE1,150·Event INSERT1,150이 후보 CTE1,000·독립 UPDATE150·독립 INSERT150으로 바뀌었다. 논리 UPDATE/INSERT는 각각1,150으로 유지되고 물리 왕복2,000회 감소했다.

## 실패 기록과 재현

초기 SQLAlchemy 기본 bulk strategy에서 from_select 파라미터 처리 오류12건(smoke-initial.log)을 확인하고 dml_strategy=orm으로 수정했다. smoke-repaired.log12개 통과. 첫 넓은 회귀는 rollback 뒤 만료된 PK를 읽는 테스트 오류1건(145pass/1fail)이 있었고 PK를 rollback 전에 보관하도록 고쳤다. 최종 regression.log147개(skip0) 통과 뒤 성능을 측정했다. 개발 중 실패와 성능18회 오류0은 별도 분모다.

attempts.json은 전체 성능 시도,raw/*.json.gz는 원본,measurements.json/comparison.json은 개별/평균,sql-purpose.json은 목적별 SQL,profiles.json은 exclusive CPU,source-audit.json/environment.json은 소스·환경,projection-probe.json은 동일 입력 SQL을 보존한다. verify.py는 raw SHA·고정 Git archive·동일 harness·HTTP/Run/command drain·계산을 검산한다.

```sh
python docs/reports/task-event-full-return-2026-10-04/verify.py
```

이 후보는 채택 보류이며 베이스 미병합·미푸시·미배포다. 두 번째 후보는 JSON을 다시 받지 않고 생성 ID/Task/순번/시간만 받는 별도 검증이다. 테스트 전용 자원 정리는 최종 보고서 cleanup.json에 기록한다.
