# 리팩토링 저장소와 브랜치 작업 안내

리팩토링 소스와 독립 Git 이력을 사용하는 저장소는 [KimHwaJin/prj_re](https://github.com/KimHwaJin/prj_re)다. GitHub 기본 브랜치와 개발 기준 브랜치는 `feature/refactor-base`다. 원래의 `main`·`feature/total_merge_v1` 이력과 합치지 않고 고아 기준 commit `745a112738a6a4d272af8a993ef320417e96955e`부터 이어진 개선 이력을 유지한다.

## 새 환경에서 시작

```sh
git clone --branch feature/refactor-base https://github.com/KimHwaJin/prj_re.git prj_re
cd prj_re
git fetch origin
```

실제 `.env`, 개인 인증 정보, 가상 환경과 로컬 데이터는 Git에 포함되지 않는다. `.env.example`과 config 템플릿을 참고해 환경별 값을 별도로 준비한다. 실행·기동 설정은 [기동 가이드](configuration-bootstrap.md), Agent 개발 위치와 현재 이행 상태는 [Agent 개발 안내](agent-development/README.md)를 참고한다. 새 Workflow·HITL 계약은 아직 서비스에 연결되지 않은 초안이다.

## 이후 작업과 통합

```sh
git switch feature/refactor-base
git pull --ff-only
git switch -c feature/next-work-item
# 작업·검증·해당 개선 기록을 작성하고 커밋한 후 실행
git push --set-upstream origin feature/next-work-item
```

`feature/next-work-item`은 설명용 이름이다. 실제 개선 항목을 설명하는 브랜치 이름을 사용한다. 기존 파생 브랜치는 당시 검증 결과를 재현하기 위한 이력이며 최신 개발 기준이 아니다. 새 구현은 갱신한 베이스에서 분기한다.

검토·검증 후 기준 브랜치로 통합한다. 베이스에 다른 변경이 들어왔으면 최신 베이스와 충돌을 해결하고 영향 검증을 완료한 뒤 통합한다. 강제 push나 기존 브랜치 삭제를 기본 작업에 포함하지 않는다. 작업 완료 후 문제·변경·검증·남은 범위를 [개선 기록](improvements/README.md)에 남긴다.

## 기존 로컬 checkout

현재 작업은 `/Users/a10054/.codex/worktrees/refactor-bootstrap/dtest-agent`의 `feature/refactor-base`에서 이어진다. 폴더 이름을 바꾸거나 원본 checkout의 사용자 변경을 이동하지 않는다.

기존 Git 저장소를 공유하는 checkout·worktree에서는 원격 설정도 공유된다. 새 `origin`은 `https://github.com/KimHwaJin/prj_re.git`, `legacy-origin`은 이전 `https://github.com/EunyoungKim-777/dtest-agent.git`이다. 이전 브랜치의 upstream은 `legacy-origin`에 유지하고 이번에 게시한 리팩토링 브랜치는 새 `origin`을 추적한다.

원래 `/Users/a10054/SKAX_PROJECT/dtest-agent` checkout은 사용자 변경을 가진 `feature/total_merge_v1` 상태로 보존한다. VS Code에서 원래 폴더를 열었다면 리팩토링 worktree 폴더를 별도로 열거나 위 clone 명령으로 새 작업 폴더를 만든다. 브랜치 전환을 위해 기존 사용자 변경을 강제로 덮어쓰지 않는다.

## 게시 대상 브랜치

2026-09-30 이관 대상은 베이스 1개와 관련 파생 36개, 총 37개다. 아래 commit은 이관 시점의 파생 브랜치 끝이다. 베이스에는 저장소 정리 문서 커밋이 추가되므로 고정 SHA 대신 현재 기준 브랜치로 표시한다. 이후 작업으로 이동하는 최신 HEAD는 원격 브랜치를 확인한다.

| 브랜치 | 이관 시점 commit |
|---|---|
| `feature/agentic-workflow-contract` | `f6db511` |
| `feature/api-agent-boundaries` | `08a6809` |
| `feature/benchmark-short-db-transactions` | `6edaade` |
| `feature/benchmark-total-refactor` | `851786b` |
| `feature/crud-execution-guards` | `5279ee5` |
| `feature/executor-async-http` | `26a9295` |
| `feature/initial-checkpoint-recovery` | `6dcea76` |
| `feature/log-event-atomicity` | `8836df6` |
| `feature/process-concurrency-benchmark` | `a1e2f82` |
| `feature/projection-batch-storage` | `c3534f0` |
| `feature/projection-db-roundtrips` | `eef0bb7` |
| `feature/public-run-lifecycle` | `d20872f` |
| `feature/read-query-efficiency` | `43f950f` |
| `feature/refactor-agent-async-llm` | `7b1ab0d` |
| `feature/refactor-agent-builders` | `ab814f4` |
| `feature/refactor-agent-flow-validation` | `fb89dbc` |
| `feature/refactor-agent-layout` | `07c5a8f` |
| `feature/refactor-agent-middleware` | `c13a541` |
| `feature/refactor-base` | 현재 기준 브랜치 |
| `feature/refactor-bootstrap-config` | `c2d83e3` |
| `feature/refactor-graceful-shutdown` | `64ad96f` |
| `feature/refactor-graph-lifecycle` | `7d0cc53` |
| `feature/refactor-preserve-workflow-package` | `8c3e0b6` |
| `feature/refactor-remove-azure` | `4021b96` |
| `feature/refactor-run-cleanup` | `a68e656` |
| `feature/refactor-run-concurrency` | `70e1c9b` |
| `feature/refactor-session-ownership` | `feba2d2` |
| `feature/refactor-short-db-transactions` | `4bb5c2f` |
| `feature/refactor-unify-analysis-workflow` | `3c828c1` |
| `feature/refactor-user-identity` | `eaab388` |
| `feature/resume-checkpoint-recovery` | `207869b` |
| `feature/run-model-selection` | `009e996` |
| `feature/run-sse-notifications` | `039d516` |
| `feature/runtime-latency-profile` | `4963250` |
| `feature/state-recovery-review` | `9ebbc20` |
| `feature/task-diagnostics` | `403c77d` |
| `feature/token-event-buffer` | `ea871a1` |

`main`, `feature/load_test_v1`, `feature/runtime-hardening`, `feature/total_merge_v1`은 리팩토링 고아 이력 밖에 있어 새 저장소의 게시 대상에서 제외했다. 로컬 및 이전 원격 참조는 보존한다.
