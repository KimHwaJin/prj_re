# 실행 구조 개선 작업 기록

최신 작업: [072 TaskEvent 원자 저장·생성값 반환](072-task-event-atomic-insert.md). 왕복 감소는 확인했으나 처리량 이득 미입증으로 두 후보 채택을 보류한다. 표준50명3회 평균 완료 16.452→17.132초(4.13% 증가), API CPU 15.771→16.242초(2.99% 증가). 최종161회귀·18시도424흐름·검산1,694개, 첫 후보까지36시도848흐름을 보존한다. 베이스 미병합·미푸시·미배포이며066/067/071 보류를 유지한다.

2026-10-03 리뷰의 1단계 배포·설정은 [058](058-deployment-config-unification.md), 2단계 Run 실행 경계는 [059](059-run-execution-boundaries.md), 3단계 DB 명령 원장·공통 Agent Worker는 [060](060-unified-agent-command-worker.md)에서 구현·격리 검증했다(베이스 미병합·미배포). 깨우기/유휴 조회는 061, 통합 검증은 062에서 진행했다. 최신 저장 후보·복구 경계는 068과 후속 목록을 따른다. [구현 계획](../design/review-implementation-plan-2026-10-03.md), [후속 목록](backlog.md)을 따른다.

고아 기준 브랜치: `feature/refactor-base`

작업 원격 저장소: [KimHwaJin/prj_re](https://github.com/KimHwaJin/prj_re). 기본 브랜치는 `feature/refactor-base`이며 베이스와 관련 파생 브랜치를 보존한다. [저장소·브랜치 작업 안내](../repository-workflow.md)를 따른다.

현재 개발 기준: `feature/refactor-base`. 057에서 Executor HTTP 제출부터 Redis 결과 재개·리포트/SSE 완료까지 service-only 처리량을 측정하고 local wake·짧은 세션 인계·호출별 중복 투영을 개선했다. 동일50명·한도32 평균23.622→22.686초, SQL13.8% 감소이며 0초 유예39회는 별도로 구분한다. [057 기록](057-executor-service-throughput.md), [상세 결과](../reports/executor-throughput-2026-10-03/README.md)를 따른다.

이전 단계: `feature/refactor-base`. 056에서 LLM을 제외한 현재 서비스 처리량을 분석하고 SQL 준비 캐시·이벤트 batch·로그인 pool·SSE frame 정리를 개선했다. 실행 한도16의 시간차이는 작으며 한도32의 설정효과와 분리한다. [056 기록](056-service-throughput-tuning.md), [보고서](../reports/service-throughput-2026-10-03/report.html), [설정 가이드](../service-throughput-settings.md)를 따른다. 실제 모델·Executor 흐름 검증은 [055](055-authenticated-executor-flow.md), memory 정책은 [053](053-project-memory-policy.md)이며 남은 범위는 [후속 목록](backlog.md)에 보존한다.

057 통합 이력: 구현 `6f0b1ee4ce657092fbe4bbae24c50db2a1d1e1c3`를 베이스에 fast-forward 병합하고 베이스·`feature/executor-service-throughput`을 origin에 atomic push했다. 26회/686사용자·388검산·관련112회귀 및 최종capture13개를 확인했다. [057 기록](057-executor-service-throughput.md)을 따른다.

056 통합 이력: 구현 `2822ed58887803ab9aa822a1b48ef0e31daaf7ae`를 베이스에 fast-forward 병합하고 베이스·`feature/service-throughput-tuning`을 origin에 atomic push했다. 주 비교29회/856사용자·관련 회귀182개와 독립 검산377개를 확인했다. 설정은 opt-in이며 기존 서비스 재기동·배포는 수행하지 않았다. [056 기록](056-service-throughput-tuning.md)을 따른다.

055 통합 이력: 구현 `2cd081c1d83f31dd99ab13fada08b131ce72ecc5`를 베이스에 fast-forward 병합하고 베이스·`feature/api-executor-flow-verification`을 origin에 게시했다. 실제 모델·Executor·후속 문맥·Store와 132개 관련 회귀를 확인했다. 데이터 입력 누락 및 로컬 커널 의존성 문제는 보고서·후속 목록에 남겼다. [055 기록](055-authenticated-executor-flow.md)을 따른다.

054 통합 이력: 구현 `66ffaedbf4ad3fdc1dcb3c4f3487b2932e77b5d8`를 베이스에 fast-forward 병합하고 베이스·파생 브랜치를 origin에 게시했다. 전체 884회귀와 이전 소스 PostgreSQL HITL 재개·wheel·Studio HTTP 검증을 통과했다. [054 기록](054-agent-runtime-cleanup.md)을 따른다.

053 통합 이력: 구현 `aa3416b1845d72309deea0c873f82b25cbce6b29`를 베이스에 fast-forward 병합하고 베이스·파생 브랜치를 origin에 게시했다. 원격 구현 SHA 일치를 확인했다. [053 구현·검증·게시 기록](053-project-memory-policy.md)을 따른다.

052 통합 이력: 구현 `bf82d9357351293f4300319b00b5324aac8f66a9`를 베이스에 fast-forward 병합하고 베이스·파생 브랜치를 origin에 게시했다. 원격 구현 SHA 일치를 확인했다. [052 구현·검증·게시 기록](052-langgraph-project-memory-store.md)을 따른다.

051 통합 이력: 구현 `6e9222364ba777cb406e1e1d4ebd1d01c5d4c1c2`, 게시 기록 `cba215f4adce281ff21f9f62ccd5d0e751bec02d`. 당시 전용 PostgreSQL 저장소·932회귀·실제 모델 세 사례·wheel을 검증했다. 현재 backend는 052가 대체한다.

050 통합 이력: 050 후속 답변의 짧은 근거 선택·수치 분리 개선을 `5e22b9868a33fb5c2f52e4e855e7592dca2ba68a`까지 fast-forward 병합하고 베이스·파생 `feature/grounded-answer-efficiency`를 origin에 게시했다. 원격 두 SHA 일치, 전체 회귀 908개와 subtest 2개 통과, 최종 경계 84개·실제 모델 A/B·신규 계획·wheel 검증을 확인했다. 설명은 평균 28.20→10.76초, 호출 2→1이었고 보고서는 정정 1회가 남았다. 기존 보고서 검증 실패도 별도로 기록했다. [050 변경·측정·게시 기록](050-compact-answer-facts.md)을 따른다. 049 게시 이력은 [049 기록](049-conversation-performance.md)에 보존한다.
039 구현·검증 기록 commit: `065ec2599a1e6e3762461fe127cf9a909b2c49f6`. [039 작업 결과](039-agentic-executor-runtime.md).
040 구현 commit: `3b18207cad5c583c347a8cbcf64579dd5028c543`. 구현·검증 기록: [작업 결과](040-agentic-execution-repair.md).
041 구현 commit: `7035a0edb93ec354a13acf3114e8f800ede84c8c`. [작업 결과](041-agentic-plan-revision.md).
042 계약 구현 commit: `5beefcfefa23b8350e17c7253cbad0abf9891dd6`. [작업 결과](042-dataset-registry-contract.md).
043 구현 commit: `cad68b5196dd8731768a5201a4d7564e9d4eb0af`. [작업 결과](043-session-analysis-context.md).
044 구현 commit: `d3bf72e6a4e7b35dc6699413710dd6f36104441e`. [작업 결과](044-agentic-answer-grounding.md).
045 구현 commit: `07e0397aa45dc9e43e7716a934683b3649a2d545`. [서비스 구현·검증·사내 연결 안내](045-sso-authentication.md).
046 문서 commit: `fb21bba18cc90b808276d7e9fd0d223ad3638bb3`. [문서·예제·검증 기록](046-api-workflow-reference.md).
시작일: 2026-09-28  
출발 브랜치: `feature/load_test_v1`  
출발 commit: `dad1d6c27e32e2aeb0a616bfeb8368cab1fd6e6b`

2026-09-28부터 독립 이력으로 작업한다. `feature/runtime-hardening`의 작업 파일을 보존하고 별도 worktree에 부모 없는 기준 commit `745a112738a6a4d272af8a993ef320417e96955e`를 만들었다. [고아 브랜치 기준 기록](refactor-branch-baseline.md)을 참고한다. 완료 항목은 파생 브랜치에서 검증·기록하고 기준 브랜치에 통합한 뒤 다음 항목 브랜치를 만든다. 004의 구현·기록 commit `7d0cc53`까지 기준 브랜치에 fast-forward 반영하고 `feature/refactor-agent-layout`을 분기했다. 005 구현·기록 commit `07c5a8f`까지 사용자 요청으로 `feature/refactor-base`에 fast-forward 통합했다. 충돌이나 코드 변경 없이 반영했으며 파생 브랜치는 보존했다. 원격 push는 수행하지 않았다. 이후 `856e008`에서 `feature/refactor-agent-async-llm`을 분기해 006을 구현·검증했다. 006 구현·검증·기록 commit `7b1ab0d`까지 사용자 요청으로 `feature/refactor-base`에 fast-forward 통합했다. 충돌이나 추가 코드 변경 없이 반영했고 파생 브랜치는 보존했다. 원격 push 및 배포는 수행하지 않았다.

사용자 요청으로 007~011을 포함한 `c13a541`까지 `feature/refactor-base`에 fast-forward 병합했다. 충돌·추가 코드 변경 없이 반영했고 `feature/refactor-agent-flow-validation`을 분기해 012를 수행했다. 이후 사용자 요청으로 012의 `fb89dbc`까지 베이스에 fast-forward 병합하고 `feature/refactor-run-concurrency`를 분기해 013을 구현·검증했다. 013 자체는 아직 베이스에 병합하지 않았다. 원격 push·배포는 수행하지 않았다.

**개선 항목을 하나씩 진행하고, 실제 작업을 끝낼 때 문제점·개선 내용·검증 결과를 기록한다. 설계나 예상 효과를 구현 완료로 표시하지 않는다.**

시작 시 작업 트리에 이전 테스트·진단·설계 변경이 존재했다. 이를 보존한 채 브랜치만 생성했으며, 기존 변경을 이번 개선의 성과로 포함하지 않는다. 시작 상태는 [기준 상태 기록](baseline.md)에 남겼다. 이 기록은 소스 백업이나 commit을 대신하지 않는다.

**작업 목록**

| ID | 항목 | 상태 | 완료일 | 기록 |
|---|---|---|---|---|
| 058 | 단일 프로젝트 배포·공통 설정 | 구현·격리 Docker 검증 완료 / 미병합·미배포 | 2026-10-03 | [작업 기록](058-deployment-config-unification.md) |
| 057 | Executor 연계 서비스 처리량·세션 인계·중복 투영 | 베이스 병합·origin 게시 완료 / 미배포 | 2026-10-03 | [작업 기록](057-executor-service-throughput.md) |
| 000 | 개선 브랜치와 기록 환경 준비 | 완료 — 문서·환경 준비 | 2026-09-28 | [준비 결과](000-workspace-setup.md) |
| 001 | 취소 감시 정리 중 Run 실행기 정체 | 구현·격리 PostgreSQL 검증 완료 / 자동 복구·배포 미완료 | 2026-09-28 | [변경·검증·제한](001-run-cleanup-stall.md) |
| 002 | 기동·설정 기반 통합 | 로컬 기반 구현 완료 / 실제 Gaia 통합 검증 대기 | 2026-09-28 | [변경·검증·제한](002-bootstrap-configuration.md) |
| 003 | 사용자 식별·역할·최초 관리자 | 구현·격리 PostgreSQL 검증 완료 / 배포 미수행 | 2026-09-28 | [변경·검증·제한](003-user-identity.md) |
| 004 | 그래프·체크포인트 자원 수명과 점유 토큰 | 구현·격리 PostgreSQL 검증 완료 / checkpoint fencing·배포 미완료 | 2026-09-28 | [변경·검증·제한](004-graph-resource-lifecycle.md) |
| 005 | 분석 Agent 패키지 집약·리소스 경로 정리 | 1차 이동·검증 완료 / 비동기 전환·전체 구조 이행 미완료 | 2026-09-28 | [변경·검증·제한](005-agent-package-layout.md) |
| 006 | Agent·LLM 비동기 호출·취소 전파 | LLM 경로 구현·검증 완료 / 전체 I/O 전환·배포 미완료 | 2026-09-28 | [변경·검증·제한](006-agent-async-llm.md) |
| 007 | 불필요한 Azure 모델·설정 제거 | 베이스 병합 완료 / 배포 미수행 | 2026-09-28 | [변경·검증·제한](007-remove-azure.md) |
| 008 | 역할별 Agent 선언·독립 프롬프트 패키지 | 베이스 병합 완료 / 미들웨어는 011에서 통일 | 2026-09-28 | [변경·검증·제한](008-agent-builders-layout.md) |
| 009 | 기존 app/workflow 유지보수 패키지 복원 | 구현·검증 완료 / 위치 해석은 010에서 정정 | 2026-09-28 | [변경·검증·제한](009-preserve-workflow-package.md) |
| 010 | 분석 Workflow 처리 코드·기존 자산 패키지 통합 | 베이스 병합 완료 / 배포 미수행 | 2026-09-28 | [변경·검증·제한](010-unify-analysis-workflow.md) |
| 011 | 역할별 create_agent·공통 문맥·프로젝트 prompt 미들웨어 | 베이스 병합 완료 / 메모리 저장·배포 미수행 | 2026-09-28 | [변경·검증·제한](011-agent-middleware.md) |
| 012 | Agent 업무 흐름 회귀 정상화·동기 I/O 취소 수명 | 구현·오프라인/패키지 검증 완료 / 실제 DB·배포 미수행 | 2026-09-28 | [변경·검증·제한](012-agent-flow-validation.md) |
| 013 | 프로세스별 Run 동시 실행·대기 세션 보호·Executor 완료 반영 | 구현·격리 PostgreSQL/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [변경·검증·제한](013-run-concurrency.md) |
| 014 | API Run·Executor 이벤트 공통 세션 실행 소유권 | 구현·격리 PostgreSQL/패키지 검증 완료 / 자동 복구·배포 미수행 | 2026-09-29 | [변경·검증·제한](014-session-execution-ownership.md) |
| 015 | 서비스 종료 시 새 점유 중단·현재 호출 drain | 구현·실제 SIGTERM/격리 PostgreSQL 검증 완료 / 배포 미수행 | 2026-09-29 | [변경·검증·제한](015-graceful-shutdown.md) |
| 016 | Agent 실행과 서비스 DB 트랜잭션 분리 | 구현·격리 PostgreSQL/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [변경·검증·제한](016-short-db-transactions.md) |
| 017 | DB 수명 변경 실제 HTTP·PostgreSQL A/B 부하 검증 | 80회 비교·원본·독립 검증 완료 / 운영 배포 미수행 | 2026-09-29 | [측정·결과·제한](017-db-scope-load-comparison.md) |
| 018 | 최초 total_merge_v1 → 현재 전체 변경 영향·5초 LLM 부하 비교 | 1·10·30·50명 비교·원본 검산 완료 / 100명 사용자 요청 중단 | 2026-09-29 | [결과·변경별 영향](018-total-refactor-comparison.md) |
| 019 | 공개 Run ID·재개·상태·이벤트 수명 통합 | 구현·격리 PostgreSQL/회귀/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](019-public-run-lifecycle.md) |
| 020 | CRUD 미종료 작업 보호·접수/삭제/이동 경합 | 구현·격리 PostgreSQL/전체 회귀/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](020-crud-execution-guards.md) |
| 021 | 목록·상태조회 불필요한 데이터 로딩 제거 | 구현·격리 PostgreSQL 계측/전체 회귀/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](021-read-query-efficiency.md) |
| 022 | Task 진단 조회·페이지 및 명령 Runs 통일 | 구현·격리 PostgreSQL/전체 회귀/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](022-task-diagnostics.md) |
| 023 | Run별 모델 선택·HITL/Executor 모델 고정 | 구현·격리 PostgreSQL/전체 회귀/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](023-run-model-selection.md) |
| 024 | Executor HTTP 비동기 호출·연결 수명·불확실 제출 보호 | 구현·로컬 HTTP/격리 PostgreSQL/전체 회귀/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](024-executor-async-http.md) |
| 025 | API·Agent 패키지 경계 및 공통 규격·연동 분리 | 구현·격리 PostgreSQL/전체 회귀/체크포인트 재개/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](025-api-agent-boundaries.md) |
| 026 | Run SSE 변경 알림·공유 조회 | 구현·격리 PostgreSQL/실제 HTTP 비교/전체 회귀/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](026-run-sse-notifications.md) |
| 027 | LLM 토큰 버퍼 상한·시간 기준 저장 | 구현·격리 PostgreSQL/전체 회귀/오프라인 비교/패키지 검증 완료 / 배포 미수행 | 2026-09-29 | [작업 기록](027-token-event-buffer.md) |
| 028 | 현재 실행 경로 LLM·큐·내부 처리 지연 분해 | 1·10·30·50명 계측·원본 검산·보고서 완료 / 운영 코드 변경·배포 미수행 | 2026-09-29 | [측정·판단·다음 방향](028-runtime-latency-profile.md) |
| 029 | 상태 저장·재시도·프로세스 장애 복구 검토 | 격리 재현·기존 보호 장치 검증·보고서 완료 / 운영 결함 수정·배포 미수행 | 2026-09-29 | [검토·증거·다음 우선순위](029-state-recovery-review.md) |
| 030 | 사용자 resume 입력 재사용 방지·checkpoint 기반 저장 복구 | 구현·격리 PostgreSQL/회귀/wheel 검증 완료 / 베이스 병합 완료·배포 미수행 | 2026-09-29 | [변경·검증·제한](030-resume-checkpoint-recovery.md) |
| 031 | 최초 입력 재전달 방지·checkpoint 기반 결과 저장 복구 | 구현·격리 PostgreSQL/회귀/wheel 검증 완료 / 베이스 병합 완료·배포 미수행 | 2026-09-30 | [변경·검증·제한](031-initial-checkpoint-recovery.md) |
| 032 | Agent 로그·대응 이벤트 원자 저장·멱등 복구 | 구현·격리 PostgreSQL/마이그레이션/회귀/wheel 검증 완료 / 베이스 병합 완료·배포 미수행 | 2026-09-30 | [작업 기록](032-log-event-atomicity.md) |
| 033 | 결과 저장 DB 왕복 축소·동일 조건 A/B | 1차 개선·1/10명 A/B·전체 회귀/wheel 검증 완료 / 베이스 병합 완료·배포 미수행 | 2026-09-30 | [작업 기록](033-projection-db-roundtrips.md) |
| 034 | 결과 단위 검증 공유·배치 저장 | 구현·격리 PostgreSQL/전체 회귀·1/10명 A/B/wheel 검증 완료 / 베이스 병합 완료·배포 미수행 | 2026-09-30 | [작업 기록](034-projection-batch-storage.md) |
| 035 | API 프로세스·Run 동시 실행 수 비교 | 21회 실측·독립 검산·보고서 완료 / 베이스 병합 완료·배포 미수행 | 2026-09-30 | [작업 기록](035-process-concurrency-benchmark.md) |
| 036 | Agent Workflow JSON 계약 초안 | 설계·오프라인 계약 검증 완료 / 런타임 미구현·베이스 병합 완료 | 2026-09-30 | [작업 기록](036-agentic-workflow-contract-draft.md) |
| 037 | 계획 승인 화면·수정 요청·Executor 제출 계약 | 개발용 prototype·오프라인/소규모 Tool/Executor 요청 모델 검증 완료 / 서비스 미연결·베이스 병합 완료 | 2026-09-30 | [작업 기록](037-plan-interaction-executor-contract.md) |
| 038 | 실제 모델 기반 계획·HITL·통합 Run/SSE Runtime | 계획 승인 단계 구현·실제 모델/DB/SSE/Phoenix·회귀/wheel 검증 완료 / Executor 이행 전 | 2026-09-30 | [변경·검증·제한](038-agentic-planning-runtime.md) |
| 039 | 승인 snapshot의 Executor 실행·결과 판단·decision HITL·리포트 | 구현·실제 서비스/688 회귀/wheel 검증 완료, 미배포 | 2026-09-30 | [작업 기록](039-agentic-executor-runtime.md) |
| 040 | MULTI 실패 분석·수정 승인·후속 실행 | 구현/712개 회귀·실제 Executor 8개·실제 수정 모델/wheel 검증, 베이스 병합·게시 완료 | 2026-10-01 | [040 기록](040-agentic-execution-repair.md) |
| 041 | 실행 전 자연어 재작성·추가 질문·자유 코드 계획 | 구현/727 회귀·실제 Executor 5개·실제 재작성 모델·wheel 검증, 베이스 병합·게시 완료 | 2026-10-01 | [041 기록](041-agentic-plan-revision.md) |
| 042 | 전처리 데이터 등록·조회·범위·버전 계약 초안 | 오프라인 계약/관련 127개 검증, 실제 Executor API·Agent 연계 미구현 | 2026-10-01 | [042 기록](042-dataset-registry-contract.md) |
| 043 | 완료 분석의 후속 대화 문맥·결과 판단 검증 | 구현·801개 회귀·실제 Executor/LLM·후속 대화·wheel 검증, 베이스 병합·게시 완료 | 2026-10-01 | [043 기록](043-session-analysis-context.md) |
| 044 | 후속 설명·보고서의 실제 근거·수치 | 값 근거·전체 회귀·실제 연계 완료 / 정성·후속 성능 보완 필요 | 2026-10-01 | [작업 결과](044-agentic-answer-grounding.md) |
| 045 | SSO 쿠키 인증·Redis 로그인 세션·Swagger | 서비스 구현·881개 회귀·wheel 검증 완료 / 사내 SDK 연결·실제 SSO 검증 필요 | 2026-10-01 | [작업 기록](045-sso-authentication.md) |
| 046 | Agent API·Workflow JSON 현재 명세·예제 | 문서·schema·예제·97개 회귀/wheel 검증 완료 / 베이스 병합·원격 게시 확인 | 2026-10-01 | [작업 기록](046-api-workflow-reference.md) |
| 047 | API·Workflow 전체 필드 주석 | 주석·동일성·46개 회귀/wheel 검증 완료 / 베이스 병합·원격 게시 확인 | 2026-10-01 | [작업 기록](047-contract-field-comments.md) |
| 048 | 공개 Run 식별자 run_id 통일 | 구현·API 회귀 571개 검증 완료 / 베이스 병합·원격 게시 확인 | 2026-10-01 | [작업 기록](048-public-run-id-contract.md) |
| 049 | 후속 설명·보고서 답변의 메타데이터·계획 문맥 비용 축소 | 구현·실모델 A/B 8회·실제 신규 계획·890회귀/wheel 검증·베이스 병합·origin 게시 완료 | 2026-10-02 | [049 기록](049-conversation-performance.md) |
| 050 | 후속 답변의 짧은 근거 ID·수치 분리 계약·중복 표 축소 | 구현·실모델 A/B·908회귀·최종 경계84/wheel 검증·베이스 병합·origin 게시 완료 | 2026-10-02 | [050 기록](050-compact-answer-facts.md) |
| 051 | 프로젝트 공유 메모리 저장·읽기·미들웨어·원문 추출 | 구현·932회귀·실제 모델 세 사례/wheel 검증 완료 / 베이스 병합·origin 게시 완료 | 2026-10-02 | [051 기록](051-project-memory-runtime.md) |
| 052 | 프로젝트 메모리 공식 LangGraph Store 전환·전용 저장소 제거 | 구현·937회귀·실제 LLM/Store/wheel 검증 완료 / 베이스 병합·origin 게시 완료 | 2026-10-02 | [052 기록](052-langgraph-project-memory-store.md) |
| 053 | 프로젝트 메모리 저장 한도·역할별 입력 예산·지속적인 주제 갱신 | 구현·959회귀·실제 LLM/Store 6개 사례/wheel 검증 완료 / 베이스 병합·origin 게시 완료 | 2026-10-02 | [053 기록](053-project-memory-policy.md) |
| 054 | 이전 설문형 Agent 제거·현재 graph로 개발 도구·검증 통합 | 구현·884회귀·PG 재개·wheel·Studio 검증 완료 / 베이스 병합·origin 게시 완료 | 2026-10-02 | [054 기록](054-agent-runtime-cleanup.md) |
| 055 | 현재 쿠키 인증 API·실제 모델·Executor·후속 문맥·메모리 연계 검증 | 구현·실제 연계·132관련회귀 완료 / 베이스 병합·origin 게시 확인 | 2026-10-03 | [055 기록](055-authenticated-executor-flow.md) |
| 056 | LLM 제외 서비스 처리량·SQL/풀/SSE 개선 및 opt-in 설정 | 구현·29측정·관련 회귀 완료 / 베이스 병합·origin 게시 확인 | 2026-10-03 | [056 기록](056-service-throughput-tuning.md) |
| 065 | 대형 출력·Operation·repair 체크포인트 비용 측정 | 13조건·39회·독립 검산 / runtime 변경·베이스 병합·배포 없음 | 2026-10-04 | [측정·원인·다음 후보](065-checkpoint-growth-profile.md) |
| 066 | observations 증가분 write 후보·기존 pending 복원 | 후보 구현·195회 비교 / 저장31.6% 감소·처리량 향상 미입증·미병합 | 2026-10-04 | [후보·채택 판단](066-observation-incremental-writes.md) |
| 067 | 증가분 writer의 실제 Worker 동일 부하 대조 | 24완료·중단1/검산/보고서 완료 · 최초 원인 미확정/후보 병합 보류 | 2026-10-04 | [결과·판단·제한](067-observation-worker-comparison.md) |
| 068 | Executor 이력 경로·최초 오류 추적·역방향 버전 경계 | 경로 수정/105회귀/50명2회 완료 · 기존 원인 미확정/후보 병합 보류 | 2026-10-04 | [작업·검증·제한](068-executor-event-recovery-verification.md) |
| 071 | 결과 로그 replay 확인 일괄 조회 | 후보·56회귀·18회 비교 완료 / 속도 미입증·채택 보류 | 2026-10-04 | [071 기록](071-result-log-replay-batch.md) |
| 072 | TaskEvent 원자 저장·생성값 반환 | 후보2개·161회귀·36회 비교 완료 / 처리량 미입증·채택 보류 | 2026-10-04 | [072 기록](072-task-event-atomic-insert.md) |

다음 개선 항목은 해당 문제를 논의하고 작업 범위를 정할 때 추가한다. 기존 설계의 모든 항목을 이미 착수한 작업으로 등록하지 않는다.

2026-10-02 사용자 결정: 모델 호출 횟수 최적화는 후순위로 보류한다. 보고서 재작성의 정정 호출 축소도 이 범위에 포함한다. [후속 작업 목록](backlog.md)에 현상·재개 범위·검증 기준을 기록했다. 051에서 project_memory 저장소와 미들웨어 연결을 진행했다. 051은 최초 저장 정책 이력이고, 현재 저장 구조는 052, 길이·갱신 정책과 검증은 [053 기록](053-project-memory-policy.md)을 따른다.

현재 구현 순서는 002의 공통 기반 → 사용자 식별/역할 → Runs·실행기(001 정체 수정 포함) → CRUD 정책 → Agent 프로젝트 컨텍스트/Workflow → 통합·부하 검증이다. 번호는 기록 ID이며 우선순위와 같지 않다. 프로젝트 공유 메모리의 확정 명칭은 `project_memory`다.

**항목별 기록 방법**

1. [양식](_template.md)을 복사해 `NNN-short-description.md`로 만든다. 문제·근거·개선 방향과 작업 범위를 먼저 쓴다.
2. 구현을 시작하면 이 목록과 항목 문서를 함께 `진행 중`으로 바꾼다. 기존 변경과 이번 변경의 경계를 남긴다.
3. 작업 종료 시 실제 변경 파일·동작, 수행한 검증과 결과, 남은 제한·후속 작업, 배포 여부를 기록한다. 해당 변경에 필요한 검증만 수행한다.
4. 완료 조건이 충족되면 양쪽을 `완료`로 바꾸고 날짜를 남긴다. 필수 검증이 남았으면 `검증 대기`로 기록한다.
5. commit을 만들었다면 해당 hash를 연결한다. commit이 없으면 `미커밋`으로 명시한다. 문서 작성을 위해 관련 없는 기존 변경을 함께 stage/commit하지 않는다.

상태: `착수 전` → `진행 중` → `검증 대기` 또는 `완료`. 중단·보류는 사유와 재개 조건을 기록한다. `완료`가 배포 완료를 뜻하지는 않으므로 코드·검증·배포 상태를 따로 적는다.

**공통 기준**

- 2026-09-29 최신 결정: Message CUD 정리는 후순위로 보류한다. Task 조회는 진단용으로 유지하고 실행 명령/SSE는 Runs로 통일한다. 관리자 진단은 소유자 조회와 별도 권한 경로로 제공한다.

- 2026-09-29 사용자 결정: 실행 종료가 불확실한 세션의 운영 복구는 관리자 API로 제공한다. 복구 CLI 구현은 추진하지 않는다. 관리자 API 구현은 구조 개선 이후 후속으로 보류하며, 그 전까지 기존 소유권 토큰·recovery_required 보호를 유지한다. 운영 배포 전에 복구 절차/API를 갖추고 검증해야 한다.

- 사용자 최종 확인에 따라 기존 skills·tools·workflows 하위 구성은 `src/agent_service/agents/analysis/workflow/`로 통합한다. 009의 app/workflow 경로 고정 해석은 정정한다. 자산은 한 곳에서 관리하고 저장된 이전 경로는 resolver로 호환한다.

- [CRUD 최종 결정](../design/crud-final-decisions-2026-09-28.md): Message CUD 공개 API 제외, 미종료 작업이 있는 사용자 삭제 거절, 전체 작업에 같은 공개 run_id 유지, 문자열 공개 사용자 ID와 내부 UUID 연결을 확정했다. 사용자 등록 role은 admin/user를 선택한다. Project system_prompt를 유지하고 모든 Agent 실행에 추가하며 별도 project_memory를 프로젝트 내 세션들이 공유한다. 일반 사용자도 Workflow 승격 가능하며 Executor 실행 성공은 필수 조건이 아니다. 템플릿과 실제 실행 검증 상태를 분리한다. 초기 보고서의 미확정/권장 문구보다 이 결정을 우선하며 구현 완료 기록은 아니다.

- CRUD 사용자 리뷰: 실행 중 세션 이동/삭제 제한, 상세 조회의 불필요한 하위 조회 제거, 기본 프로젝트 DELETE 거절 방향을 승인했다. Message CUD는 편의 API이며 운영 필수 사용 시나리오에서 제외한다. 메시지 저장은 Runs/내부 실행에서 수행한다. 사용자 삭제 시 하위 데이터도 함께 숨기는 정책을 유지한다. 공개 실행 기능은 Runs로 집중하고 별도 Tasks API는 필요성 검토 후 기능 이관/제거 후보로 둔다. 내부 전체 작업 상태·재개 연결·세션 점유 책임을 검토 없이 삭제하지 않는다. [항목별 리뷰](../reports/crud-api-review-2026-09-28.md)를 참고한다.

- Agent 최초 접수 시 main_model_name이 없으면 기본 LLM, 있으면 등록된 모델을 선택하고 Task/resume에 고정한다. 나머지 플랫폼 선택 옵션은 이번 범위에서 제외한다.
- 2026-10-01 SSO 요청으로 다음 초기 결정은 대체되었다. 현재는 로그인 쿠키·CSRF·SSO 최초 일반 사용자 등록을 사용한다. [현재 계약](../sso-authentication.md)을 우선한다. 이전 결정: 사용자 식별은 플랫폼과 독립적인 X-User-Id 공통 헤더로 통일한다. 비밀번호·로그인·토큰 발급은 만들지 않는다. 등록/활성 여부와 DB의 admin/user 역할·자원 소유권을 검사한다. /users/me는 조회이며 사용자 등록/수정/비활성화는 관리자 전용, 최초 관리자는 배포 초기화에서 생성한다. [확정 인터페이스](../design/platform-user-api-contract-2026-09-28.md)는 설계 기록이며 구현 완료가 아니다. 내부 데모는 운영 요구/필수 호환 범위에서 제외한다.
- 플랫폼의 workflow 목록·`/api/v1/{workflow}/run`·src/workflows의 일반 객체 ainvoke 호출은 사용자 설명이며 필수 준수 규격이 아니다. 우리 API의 durable 접수·Worker·resume·상태 계약을 우선한다. 플랫폼도 연계할 경우 공통 접수 adapter로 연결하고 실제 graph 실행은 Worker에서만 수행한다. async 메서드라는 사실과 접수 후 background 실행의 보장은 구분한다.
- API·Agent 소스는 api_service와 agent_service 형제 패키지로 분리하는 것을 권장 구조로 한다. agent_service 안에 공통 runtime과 업무별 agents를 둔다. 양쪽이 상대 구현을 직접 import하지 않도록 공통 계약·저장 port와 bootstrap 주입 경계를 둔다. 소스 분리를 별도 Pod/프로세스 배포로 해석하지 않는다.
- 업무 Agent를 같은 레포에 추가하고 API에서 선택할 수 있게 공통 API·Agent 계약/registry·Runtime·업무 패키지를 분리한다. Agent별 Worker/풀을 복제하지 않는다. 선택된 agent_id/버전/checkpoint 참조를 task에 고정하고 사용자/이벤트 resume가 같은 대상을 사용한다. 동일 세션 잠금은 Agent 종류를 바꿔도 유지한다. [다중 업무 Agent 확장 설계](../design/extensible-agent-runtime-2026-09-28.md)를 초기 런타임 교체에 반영한다.
- 서비스 설정 우선순위는 사용자 요청대로 선택된 config 명시값 > 환경변수 > 기본값이다. dev/stg/prd 선택은 기존 플랫폼 환경 변수로 수행한다. false/0도 명시값으로 보존하며 잘못된 config 값은 하위 소스로 대체하지 않고 검증 오류로 처리한다.
- 환경변수/YAML 로딩·검증·주입 통합을 Gaia 통합 골격과 함께 초기 리팩토링 범위에 포함한다. 중앙 설정 snapshot을 API·Agent·Worker에 전달하고 각자의 .env 재로딩·암묵적 대상 변경 fallback을 제거한다. 여러 DB/풀 사용과 설정의 단일 해석은 별개다. [설정 통합 설계](../design/configuration-unification-2026-09-28.md)는 요구사항/코드 조사 결과이며 구현 완료가 아니다.
- 폐쇄망 Gaia 템플릿을 최종 실행 기준으로 한다. 사용자 확인에 따라 루트 app.py는 수정 가능한 진입점이며 서비스 bootstrap을 호출하도록 구성한다. get_routers로 플랫폼이 생성한 FastAPI에 라우터를 연결하고 플랫폼 core/common/lib 수정은 기본안에서 제외한다. 플랫폼 main의 필수 초기화를 보존하면서 lifespan 덮어쓰기로 Worker 수명이 누락되지 않게 통합한다. 실제 템플릿 원본 호환 검증은 미완료이며, 핵심 실행기 구현 전에 [플랫폼 통합 골격](../design/gaia-template-integration-2026-09-28.md)을 먼저 검증한다.
- 관측 사실, 원인 가설, 확정 원인을 구분한다. 입증하지 않은 성능 향상 수치를 쓰지 않는다.
- 검증에는 명령/방법, 조건, 기대 결과, 실제 결과를 남긴다. 미실행은 미실행이라고 쓴다.
- `.env`, 인증정보, 실제 사용자 입력 등 민감한 원문은 기록에 복사하지 않는다. 증거는 필요한 범위를 비식별화하거나 안전한 산출물을 링크한다.
- 성능·부하 검증에서는 이미지/코드 버전, Pod/프로세스/슬롯 수, LLM·Executor mock 여부와 지연, DB 조건, 유입률·폴링 조건을 남긴다.
- 현재 배포 전제는 단일 Deployment·단일 컨테이너 Pod, 자원 사용량 기반 확장이다. 내부 실행 동시성·공유 상태·안전한 종료를 우선한다.
- replica 수는 우리가 조정할 수 없고 플랫폼이 결정한다. 최소·최대 replica 조정을 개선의 전제로 삼지 않는다. Pod 내부 동시성 및 Pod 수와 독립적인 전역 LLM/DB 사용량·접수 한도를 설계한다. Pod별 DB pool 상한만으로 전체 연결 수가 제한된다고 해석하지 않는다.
- DB는 프로젝트 전용이며 API·Agent·Executor·추가 예정 배치 API가 함께 사용한다. 별도 인프라 운영 여력 제약으로 PgBouncer/중앙 pooler 추가는 이번 범위에서 제외한다. 기존 환경의 풀 재사용·연결/overflow 제한·짧은 transaction·대기 기한·조회 비용·계측을 우선한다. replica 수와 무관한 전체 연결 수의 강한 상한은 현재 보장하지 못하는 제약으로 남긴다. 자체 분산 연결 관리기를 새로 만드는 방식으로 범위를 확대하지 않는다.
- 동일 세션의 Agent가 실행 중이면 일반 사용자 입력·새 실행·resume를 잠근다. 프론트뿐 아니라 API도 원자적으로 검사한다. 동시 실행 개선은 다른 세션 간에만 적용한다. 사용자 HITL 대기에서는 현재 요구한 응답만 허용하며, Executor 대기로 실행 슬롯을 반환한 것을 사용자 입력 허용으로 취급하지 않는다.
- Executor 실제 작업은 1주 이상 지속될 수 있다. 외부 실행 대기는 슬롯·DB 연결·실행 lease를 점유하지 않아야 하며, Pod 교체·배포 후에도 checkpoint/binding/receipt로 재개해야 한다. Run 정리 기한과 외부 작업 기한을 구분하고, 장기 timeout·보존·이벤트 누락 복구·취소 확인·그래프 버전 호환을 검증한다. 요구사항 추가이며 구현 완료 기록이 아니다.
- Executor 제출 body에는 이미 idempotency 필드가 있다. 기존 Agent의 키 전달을 활용하고 새 계약 도입은 요구하지 않는다. 같은 논리 요청의 재시도·Pod 복구 시 키/payload 유지와 접수 결과 복구를 검증하고, 이미 보장되는 기능은 중복 구현하지 않는다.
- 실행 중 생성하는 Workflow JSON은 사용자가 공유 PV에 연결할 계획이다. 기존 파일 저장 방식을 유지하고 DB/object storage 이전은 필수 개선에서 제외한다. 실제 저장 경로의 공유 마운트, 다중 노드 접근, Pod 교체 후 조회를 배포 검증 사항으로 남긴다.

**기존 분석·설계**

- [CRUD·실행 연계 API 45개 검토](../reports/crud-api-review-2026-09-28.md) — 현재 코드·격리 검증 기반 분석. 인증, 메시지 접수, 실행 중 수정/삭제, 멱등성, 조회 비용의 개선 방향이며 구현 완료가 아니다.
- [정체 원인 분석](../reports/run-delay-diagnosis-2026-09-28.md)
- [조회·대기 지연 분석](../reports/polling-delay-diagnosis-2026-09-28.md)
- [기본 실행 구조 설계](../design/agent-runtime-target-architecture-2026-09-28.md)
- [현재 Kubernetes 배포 제약을 반영한 설계](../design/kubernetes-runtime-efficiency-2026-09-28.md)
- [폐쇄망 Gaia 템플릿 통합 설계](../design/gaia-template-integration-2026-09-28.md)
- [환경변수·설정 로딩 및 주입 통합](../design/configuration-unification-2026-09-28.md)
- [공통 API·Runtime과 여러 업무 Agent의 분리](../design/extensible-agent-runtime-2026-09-28.md)

- 2026-09-30 우선순위 변경: 처리량·성능 구조 → Agent 로직 변경 → 기능·디테일 검증 → 종합 성능 검증 → 에러 처리·운영성 순으로 진행한다. 에러 처리 신규 개선은 마지막 단계로 보류하고 기존 보호 장치는 유지한다. 1단계의 변경 효과 확인용 작은 A/B는 수행한다.
