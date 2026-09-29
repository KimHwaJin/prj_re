# 030 — 사용자 resume 입력 재사용 방지·checkpoint 기반 저장 복구

- 상태: 구현·격리 PostgreSQL·회귀·wheel 검증 완료. 베이스 병합·배포 미수행.
- 브랜치: `feature/resume-checkpoint-recovery`
- 출발 commit: `9ebbc20` (029 검토). 베이스 병합은 이번에 수행하지 않는다.
- 날짜: 2026-09-29
- 작업 commit: 이 문서를 포함한 feature commit에 기록한다.

## 문제

029에서 다음 질문까지 checkpoint가 저장된 뒤 서비스 저장만 실패해도 같은 사용자 응답을 최신 질문에 재전달하는 것을 재현했다. 최종 상태 저장 실패가 일반 예외 처리 밖에 있어 API가 running으로 남는 문제도 있었다. 모델 호출/실행 단위 재설계는 보류하고 사용자 resume의 저장·재시도 경계만 수정했다.

## 구현

1. 이전 Run의 실제 interrupt ID를 내부 metadata에 저장하고 다음 resume 접수 때 고정한다. 내부 Run ID를 명령 ID로 재사용한다. 클라이언트가 내부 제어 metadata를 주입하지 못하도록 제한한다.
2. 공용 `record_user_resume`/`user_interrupt`가 입력을 받은 노드 결과와 처리 receipt를 함께 반환한다. 분석 Agent의 6개 사용자 HITL 노드에 적용했다.
3. 호출 직전 `_resume_started`를 짧은 DB transaction으로 저장하고, 주소가 지정된 Command를 sync durability로 실행한다.
4. 이미 소비한 명령은 checkpoint를 읽어 메시지/로그 등 서비스 반영만 재실행한다. 다음 질문에 동일 입력을 전달하지 않는다.
5. 최종 Run/Task/event 저장 오류도 resume의 재시도 범위로 옮겼다. commit 응답 유실은 DB 상태를 재조회해 완료 여부를 판정한다.
6. receipt 불일치·소비 여부 불명·그래프 진행 중 실패·복구 시도 소진은 중단이 확인된 호출은 해당 Task만 recovery로 보호한다. 다른 세션의 Worker 처리는 계속 가능하다. Task 보호를 저장하지 못하거나 실행 종료 자체가 불명확하면 기존 프로세스·owner 보호를 유지한다. 무조건 재실행하거나 새 세션 입력을 허용하지 않는다.

공개 resume body, SSE/GET 계약, Agent 업무 순서와 모델 호출은 그대로다. DB migration·새 큐·Worker·환경변수는 추가하지 않았다.

[Agent 개발 규칙·복구 알고리즘·배포 제한](../architecture/user-resume-recovery.md).

## 검증 계획 및 결과

- 실제 PostgreSQL checkpoint + API + Worker에서 projection/최종 event 저장 실패를 각각 주입한다. 그래프/pool 재생성 후 재시도해도 첫 답변은 한 번만 처리되고 두 번째 답변은 비어 있어야 한다. 이후 실제 두 번째 사용자 응답으로 완료할 수 있어야 한다.
- 명령 전송 표시 commit 응답 유실, 소비 후 노드 실패, 후속 노드 실패, 잘못된 receipt/interrupt, legacy target 없음, 재시도 소진은 자동 재전달 없이 recovery_required가 되어야 한다.
- 최종 transaction commit 응답 유실은 이벤트 1개로 수렴하고 재큐잉하지 않아야 한다.
- 실제 분석 Agent의 데이터 선택→분석 조건→후보 선택→승인에서 receipt가 저장되고 envelope가 채팅 메시지에 섞이지 않는지 확인한다. LLM/Executor는 mock이다.
- 전체 소스 회귀: `614 passed, 53 warnings, 2 subtests passed` (234.88초). 경고는 checkpointer 없는 하위 그래프의 durability 설정 관련이다.
- 이후 세션 단위 quarantine와 보호 저장 실패 시 기존 fail-closed 유지 검증을 추가했다. 이 최종 보완 이후 관련 회귀 **69개 통과** (58.36초). 전체 614개를 최종 보완 후 다시 돌린 것은 아니며, 변경된 경로와 기존 종료/취소/Executor/패키지 보호를 재검증했다.
- wheel을 네트워크/추가 설치 없이 빌드해 신규 공통 계약·Agent runtime·API 복구 모듈 포함 및 테스트 제외 확인 완료.

## 제한·후속

기존 사용자 대기 Run에 interrupt 식별 정보가 없으면 자동 이관하지 않고 복구 필요로 둔다. 구·신 Worker 혼재 배포 안전성을 보장하지 않는다. 프로세스 강제 종료 후 owner 복구, 관리자 복구 API, 최초 호출의 최종 상태 저장 실패, 로그/이벤트 원자성은 후속이다. 외부 Redis·실제 Executor·Kubernetes 배포 테스트와 성능 A/B는 이번에 수행하지 않는다.

원본 체크아웃/기존 앱 컨테이너는 변경하지 않았다. 전용 임시 PostgreSQL 컨테이너·볼륨은 검증 후 제거했다. 개선 worktree에서만 작업했고 베이스 병합·push·배포는 하지 않았다.


## 검증 기록과 실행 방법

[검증 집계·최종 소스 hash](../reports/resume-checkpoint-recovery-2026-09-29/validation.json), [전체 회귀 결과](../reports/resume-checkpoint-recovery-2026-09-29/full-suite-before-local-quarantine.txt), [최종 관련 회귀 결과](../reports/resume-checkpoint-recovery-2026-09-29/final-targeted-suite.txt), [wheel 확인](../reports/resume-checkpoint-recovery-2026-09-29/wheel-check.txt).

`DTEST_IDENTITY_TEST_DATABASE_URL`에 폐기 가능한 localhost의 `identity_test` DB를 설정한다. fixture가 schema를 재생성하므로 운영 DB는 사용할 수 없다. `.env`나 운영 인증정보는 보고서에 복사하지 않았다.

```sh
PYTHONPATH=src python -m pytest src -q --tb=short
PYTHONPATH=src python -m pytest \
  src/api_service/test/test_user_resume_recovery_postgres.py \
  src/api_service/test/test_run_cleanup_postgres.py \
  src/api_service/test/test_session_execution_postgres.py \
  src/api_service/test/test_executor_async_postgres.py \
  src/api_service/test/test_public_run_postgres.py \
  src/agent_service/agents/analysis/tests/test_user_resume_receipt.py \
  src/api_service/test/test_package_boundaries.py -q --tb=short
```

신규 PostgreSQL 회귀 13개와 실제 분석 Agent 검증 1개를 추가했다. 기존 작은 검증용 그래프들도 생산 코드와 같은 사용자 resume 규약을 적용했다. 프로젝트 컨텍스트 legacy backfill 검증은 helper에서 유지하며, 서비스의 식별자 없는 직접 resume 경로는 허용하지 않는다. 029의 진단 스크립트는 당시 commit에서 실패 현상을 관측하는 용도임을 명시했다.
