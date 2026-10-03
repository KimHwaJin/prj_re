# 064 분석 상태 수명과 노드 읽기 경계

| 항목 | 내용 |
|---|---|
| 상태 | 구현·로컬 기능/저장량 검증 / 베이스 미병합·미푸시·미배포 |
| 브랜치 | feature/analysis-state-lifecycle |
| 이전 기준 | 2622552 / 날짜 2026-10-04 KST |
| 범위 | 리뷰 6단계: 상태 타입·읽기 경계·새 요청 필드 수명 |

## 기존 문제

모든 노드가 77개 전체 상태를 읽었고 receive의 초기화 목록은 일부 실행 상태를 빠뜨렸다. 실제 분석 완료 후 FAQ에도 이전 Executor body·Operation ID·실행 단계 등이 남았다. 모델 역할에 전체 mutable 상태를 넘길 수 있었고 필드 수명을 한 곳에서 확인하기 어려웠다.

## 실제 변경

- `analysis/state.py`: 기존 평면 채널 이름/타입/last-value 의미를 유지한 8개 수명/책임 그룹, 23개 노드 입력 schema. 필요한 1~26개 채널만 읽는다.
- `planning/lifecycle.py`: 모든 Run 필드 기본값을 한 곳에서 생성한다. 기존에 빠진 9개 초기화를 보완한다. 신규 필드의 reset 누락과 mutable 객체 공유도 검사한다.
- receive는 새 요청만 초기화한다. history·소유자의 제한된 완료 분석 근거·kernel은 유지한다. 완료/report 및 HITL/Executor resume에서는 승인 원문/제출본문/receipt를 보존한다.
- 그래프 노드/Runtime version, 공개 API/SSE, prompt·모델 호출, 설정·pool/slot, Workflow 자산은 유지한다. Alembic/Store schema 변경은 없다.

## 동작과 실측

6행 실제 등록 Tool 분석 + 같은 세션 FAQ 2개를 전후 각각 실제 PostgreSQL에 저장했다. 결과/판단/보고서 상태와 MULTI double 호출 3회는 동일했다. 후속의 최신 JSON+참조 blob은 30.34→22.65KiB(약 25% 감소), 제출 JSON은 8,472→2bytes였다.

**전체 과거 포함 저장 누계는 약 2~2.5% 증가**했다. 초기 상태·version/write를 명시하고 과거 원문도 유지하기 때문이다. node input_schema로 version metadata까지 작아지지는 않았다. 이 작업의 처리량/응답 시간 개선은 측정하지 않았으며 063의 HTTP 수치를 재사용하지 않는다.

## 검증과 한계

분석 Agent/패키지 318 passed, 실제 PostgreSQL/API 50 passed. 기존 전체 읽기 방식의 네 종류 wait를 새 pool/graph로 재개했고, 실제 2622552 graph/nodes 소스로도 별도 네 wait를 검증했다. 승인/실행/receipt 불변·모델/성공 load 미재실행·연결 반환을 확인했다.

전체 PG 회귀 종료 시 asyncpg SSL 협상 Future 경고 1회가 남았다. 관련 8개 debug 분리 검사에서는 재현되지 않았으며, 유발 테스트/타이밍은 미확정이다. 로그와 후속 항목을 보존한다. 과거 pruning·긴 작업·큰 출력/반복 repair·운영 HPA/Pod·실제 외부 연계는 범위 밖이다.

[상세 검증과 저장량](../reports/analysis-state-lifecycle-2026-10-04/README.md), [상태 개발/인수인계](../agent-development/analysis-state-lifecycle.md).

## 다음 작업

대형 출력·여러 Operation/repair의 저장량과 비용을 대표 fixture로 확대 측정해 다음 성능 변경을 결정한다. 특히 version metadata는 노드 입력 축소만으로 해결되지 않았으므로 공식 체크포인트/interrupt 동작을 더 확인해야 한다. 조회/복구에 필요한 원문을 임의 제거하거나 durability를 낮추지 않는다. 모델 호출 최적화·Registry 실제 연계·Workflow CRUD·광범위 운영 기능의 기존 보류는 유지한다.
