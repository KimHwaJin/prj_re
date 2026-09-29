# 029 — 상태 저장·재시도·프로세스 장애 복구 검토

- 상태: 코드 검토·격리 재현·기존 보호 장치 검증·보고서 완료. 운영 결함 수정은 미수행.
- 브랜치: `feature/state-recovery-review`
- 출발 commit: `4963250` (028). 베이스는 `ea871a1`이며 028·029는 아직 베이스 미병합.
- 날짜: 2026-09-29
- 작업 commit: 이 문서를 포함한 feature commit에 기록한다.

## 범위와 이유

사용자는 모델 호출과 실행 단위 재설계를 Agent 전체 흐름 피드백 이후로 보류했다. 그와 독립적으로 checkpoint·서비스 DB·이벤트·Executor 상태의 저장 경계와 장애 복구를 검토했다. 운영 코드나 배포 설정은 바꾸지 않았다.

[상세 보고서·근거·수정 우선순위](../reports/state-recovery-review-2026-09-29/report.md).

## 확인 결과

1. **P1:** 다음 interrupt까지 checkpoint가 진행한 뒤 서비스 저장이 실패하면, 사용자 resume가 일반 자동 재시도로 다음 질문에 재사용된다. 사용자 호출 1회가 두 질문의 답변으로 들어가는 것을 실제 checkpoint로 재현했다.
2. **P1:** 최종 상태 이벤트 저장 실패 시 checkpoint는 입력 대기지만 API Run은 running에 남는다. 프로세스는 healthy이고 점유는 풀렸지만 해당 Run은 재큐잉되지 않는다.
3. **P2:** 로그 선행 commit 뒤 이벤트 저장이 실패하면 같은 key 재호출로 이벤트가 복구되지 않는다.
4. 실제 API Worker 프로세스 종료 후 owner를 유지하는 fail-closed 정책으로 자동 재개가 안 된다. 의도된 보호이지만 관리자 복구 경로가 필요한 운영 제약이다.
5. **P2:** 이벤트 실행자 프로세스 종료 후 남은 owner는 Task RUNNING 전용 reconciler에서 감지되지 않는다. API가 waiting_executor로 남고 recovery 표시도 없다.

정상 접수/claim transaction, 공통 세션 점유, 불확실 Executor 제출 보호, 이벤트 receipt/outbox는 유지할 기반이다. 전면 재작성보다 실행 재시도와 결과 저장 재시도의 계약을 명확히 하는 것이 우선이다.

## 실제 변경

- `scripts/diagnostics/state_recovery/probe_boundaries.py`: 현재 실패 결과를 재현하는 5개 진단. 수정 회귀 테스트와 혼동하지 않도록 명시적으로 분리했다.
- `docs/reports/state-recovery-review-2026-09-29/`: 상세 검토·5개 JSON 증거·검증 결과.
- 이 작업 기록과 개선 목록 갱신. API/Agent 운영 소스 수정 없음.

## 검증과 제한

임시 PostgreSQL 17에서 진단 5개 통과, 기존 보호 장치 35개 통과. 프로세스 종료 후 API 상태 assertion 추가 후 관련 2개 재실행 통과. 외부 LLM/Executor/Redis 호출 없음. 실제 프로세스 사망은 검증했지만 Kubernetes 종료 통합 검증은 아니다. 비즈니스 Agent 대신 작은 typed graph를 사용했으므로 실제 Workflow 승인 우회·Executor 중복 제출을 재현했다고 해석하지 않는다. 과거 user_request 오류의 원인 확정도 아니다.

진단 통과는 현재 결함 재현 성공을 뜻하며 해결 완료가 아니다. 기존 앱/DB 컨테이너와 원본 체크아웃은 변경하지 않았다. 전용 임시 DB 컨테이너·볼륨 정리 완료. 원격 push·베이스 병합·배포는 수행하지 않았다.

## 다음 우선순위

사용자 resume의 예상 interrupt/command 소비 증거를 고정하고, checkpoint 진행 후에는 서비스 저장만 복구하도록 분리한다. 이어 최종 상태 반영 실패, 로그/이벤트 원자성, owner 공통 진단을 보완한다. 관리자 복구 API는 기존 보류 결정을 유지한다. 모델 호출·실행 단위 변경과 성능 A/B는 이번 범위에서 제외했다.
