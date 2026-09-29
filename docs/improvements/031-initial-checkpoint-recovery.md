# 031 — 최초 입력 재전달 방지·checkpoint 기반 결과 저장 복구

- 상태: 구현·격리 PostgreSQL·전체 회귀·wheel 검증 완료. 베이스 병합·배포 미수행.
- 브랜치: `feature/initial-checkpoint-recovery`
- 출발 commit: `207869b` (030 사용자 resume 복구).
- 날짜: 2026-09-30
- 작업 commit: 이 문서를 포함한 feature commit에 기록한다.

## 문제와 범위

030은 사용자 resume만 보호했다. 최초 Agent 호출은 결과 checkpoint를 이미 저장해도 서비스 저장 실패 시 초기 입력을 다시 전달할 수 있었고, 최종 Run 상태 저장은 일반 오류 처리 밖에 남아 있었다. 이를 최초 호출에도 확장했다. 모델/Agent 업무 흐름 재설계는 보류한 상태를 유지한다.

## 구현

1. 최초 접수에 내부 protocol/전송 표시를 저장하고 클라이언트 metadata 주입을 차단했다.
2. 첫 입력 처리 노드가 결과와 receipt를 함께 checkpoint에 남긴다. 입력 checkpoint와 처리 완료를 구분한다.
3. 재시도 시 같은 명령 receipt와 정상 대기/종료를 확인하면 Agent/LLM 호출 없이 서비스 저장만 복구한다. 변경된 프로젝트 문맥도 다시 읽지 않는다.
4. 최종 Run/Task/event 저장까지 예외 처리 범위에 포함했다. 실제 commit 성공 후 응답 유실은 DB를 재조회한다.
5. 처리 여부/중간 진행이 불명확하면 해당 Task를 recovery_required로 보호한다. 저장 오류 기록 자체가 실패하면 Worker가 완료로 간주해 owner를 해제하지 않도록 보완했다.
6. resume와 공통인 snapshot 변환/저장 예외를 graph_recovery로 이동했다. 새 DB migration, 설정, Worker, 큐는 없다.

[상세 판정·Agent 개발 규칙·배포 제한](../architecture/initial-request-recovery.md).

## 검증

- 최초 호출 PostgreSQL 장애 주입 15개: 질문 대기/즉시 종료 각각의 서비스 저장·최종 event 저장·graph 응답 유실 후 runtime/pool 재생성. 처리 횟수는 첫 노드/모델 각 1회이고 이후 resume 가능.
- 입력/후속 노드 오류, 전송 표시 commit 응답 유실, legacy protocol 없음, receipt 불일치, retry 소진은 자동 입력 재전달 없이 recovery_required.
- 최종 commit 응답 유실은 성공 이벤트 1개. 동일 세션 취소 후 새 Run은 새 입력으로 처리.
- 오류 기록 실패 시 owner 유지 및 process 추가 claim 중단.
- 기존 사용자 resume 장애 주입 13개와 함께 28개 통과(33.15초).
- 실제 분석 Agent의 첫 receipt와 네 단계 HITL 회귀 포함 전체 소스 **633 passed, 53 warnings, 2 subtests passed** (252.58초). 경고는 checkpointer 없는 하위 그래프의 durability 설정 관련이다.
- 최종 검토에서 기존 `graph.invoke` 시간 계측을 새 최초 실행 경로에도 보존했다. 이 보완 후 관련 **37개 통과** (33.16초). 전체 633개를 이 계측 보완 이후 다시 실행한 것은 아니다.
- 최종 wheel 재빌드: 공통 계약·Agent runtime·API 복구 신규 모듈 4개가 최종 소스와 일치하며 test 패키지는 제외됨.

임시 PostgreSQL 17에서 API·Worker·실제 PostgreSQL checkpointer를 검증했다. LLM/Executor는 mock이며 외부 Redis·Executor·기존 앱 컨테이너에는 접속/변경하지 않았다. 실제 프로세스 강제 종료가 아닌 graph/pool 재생성과 예외 주입 검증이다.

## 남은 작업

로그/이벤트 원자성(R3), 강제 종료 후 owner 복구(R4/R5), 관리자 복구 API는 후속이다. protocol 없는 이전 대기 작업 및 구·신 Worker 혼재는 자동 호환하지 않는다. 기존 최종 배포 환경/Gaia 통합, 운영 성능 A/B, Kubernetes rollout은 이번에 검증하지 않는다.

검증용 임시 PostgreSQL 컨테이너와 볼륨은 정리했다. 베이스 병합·push·배포는 하지 않았다. 원본 체크아웃/기존 앱 컨테이너를 그대로 두고 작업용 worktree만 수정했다.


## 검증 기록과 재실행

[집계·최종 소스 hash](../reports/initial-checkpoint-recovery-2026-09-30/validation.json), [전체 회귀 결과](../reports/initial-checkpoint-recovery-2026-09-30/full-suite-before-timing-preservation.txt), [최종 관련 회귀 결과](../reports/initial-checkpoint-recovery-2026-09-30/final-targeted-suite.txt), [wheel 확인](../reports/initial-checkpoint-recovery-2026-09-30/wheel-check.txt).

`DTEST_IDENTITY_TEST_DATABASE_URL`에 폐기 가능한 localhost의 `identity_test` DB를 지정한다. fixture가 schema를 재생성하므로 운영 DB를 지정하지 않는다. 실제 설정/비밀번호는 기록하지 않았다.

```sh
PYTHONPATH=src python -m pytest src -q --tb=short
PYTHONPATH=src python -m pytest \
  src/api_service/test/test_initial_request_recovery_postgres.py \
  src/api_service/test/test_user_resume_recovery_postgres.py \
  src/agent_service/agents/analysis/tests/test_user_resume_receipt.py \
  src/api_service/test/test_agent_project_context.py -q --tb=short
```
