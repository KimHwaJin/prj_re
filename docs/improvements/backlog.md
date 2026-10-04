# 후속 작업 목록

2026-10-02 사용자 결정에 따라 모델 호출 횟수 최적화를 후순위로 보류한다. 이 목록은 미완료 작업과 재개 조건을 기록하며, 기존 개선 기록의 구현 완료 상태를 변경하지 않는다.

## 2026-10-03 리뷰 반영 후 현재 실행 순서

상태: 058·059·060·061에서 1~4단계 구현·로컬 검증, 062에서 5단계 동일 총한도 HTTP fixture 검증 완료(베이스 미병합·미배포). [구현 계획](../design/review-implementation-plan-2026-10-03.md)과 [D-01~D-12 및 C-01~C-04](../reviews/2026-10-03-decisions.md)를 따른다.

1. 단일 컨테이너·app.py 정본 및 배포/설정 정합성(D-01/D-02)은 [058](058-deployment-config-unification.md)에서 구현했다. 사내 배포 검증과 기존 Worker 전환은 별도다.
2. Run 접수/실행/취소와 공통 GraphInvocation/상태 반영은 [059](059-run-execution-boundaries.md)에서 구현했다. DB 준비/결과 session을 분리하고 구 facade·private method 호출을 제거했다.
3. [060](060-unified-agent-command-worker.md)에서 DB 명령 원장·공통 총한도·세션 순서·원자 접수/routing을 구현했다. 기존 분리 DB의 실제 이관·운영 전환은 미완료다. 이전 Redis 실행 파일·구 전용 메서드/설정 삭제는 사용자 승인 후 완료했다.
4. [061](061-agent-command-wakeup.md)에서 공용 LISTEN/NOTIFY·retry timer·재연결·유휴 주기 scan과 full 슬롯 hint 합치기를 구현했다. 신호 없이도 확인하며 Worker/SSE 구독 수명은 독립이다.
5. [062](062-agent-worker-e2e-performance.md)에서 동일 총한도 20·1/10/30/50명·결과 집중/혼합·후속 설명/메모리·notify 대조 59회를 완료했다. 로컬 fixture 결과이며 실제 Pod/HPA·지속 부하·Artifact 등록은 미검증이다.
6. [063](063-checkpoint-pool-concurrency.md)에서 정상/후속 checkpoint 저장량·잠금 대기와 pool 상한 유지 병렬 접근을 검증했다. 별도 PG repair 후보 복구도 확인했다. 저장량 자체를 줄이지 않았다. [064](064-analysis-state-lifecycle.md)에서 새 요청 필드 초기화·상태 수명 타입·23개 노드 입력 경계와 기존 PG wait 재개를 검증했다. 최신 후속 상태는 작아졌지만 누계 저장량은 약 2~2.5% 증가했다. [065](065-checkpoint-growth-profile.md)에서 대형 출력·다단계/반복 repair의 13조건·39회와 독립 검산을 완료했다. 큰 미리보기/20 Operation은 observations 누적 복사가 약70%, 작은 관찰은 checkpoint JSON·metadata가 약77%였다. [066](066-observation-incremental-writes.md)에서 표준 reducer/Delta5의 195회 저장·복원 비교와 기존 누적 pending write 호환을 검증했다. 표준 후보는 큰 미리보기20 Operation 저장31.6% 감소지만 수행 평균+5.5%로 처리량 향상을 입증하지 못했다. Delta5는 추가 조회/수행 비용으로 runtime 기본안에서 제외했다. [067](067-observation-worker-comparison.md)에서 같은 Worker/풀/0초 LLM의25시도 중24완료·1중단을 검증했다. 대형50명 저장30.6%/압축 column9.7% 감소지만 완료91.30→91.46초로 속도 향상은 없다. 중단 첫 원인 미확정으로 병합 보류다. [068](068-executor-event-recovery-verification.md)에서 root/history 경로 정합성은 수정·실제 HTTP/PG로 검증했다. 최초 오류 계측을 켠50명2회(100흐름/2,100 POST)는 모두 완료하여 기존 원인은 미확정으로 남긴다. 새 tagged pending write는 구 LastValue reader와 역방향 비호환임을 실제 PG로 확인했다. **남은 작업:** 후보 채택·버전별 실행 고정/drain/rollback 계약 결정과 과거 최초 원인 증거 확보. 다음 성능 후보는 API CPU sampling이며 모델 호출 수 최적화는 보류한다. 후보는 미병합·미푸시이며 durability 및 승인/receipt/원문 보존은 유지한다.

아래 057 이후 목록의 후속 대화·메모리 성능 범위는 유지하되, 바로 다음 작업의 순서는 위 계획으로 갱신한다. 모델 호출 수·prompt 최적화, Dataset Registry 실연계, Workflow CRUD, 광범위 운영 기능의 기존 보류는 유지한다. 번호가 붙은 개선 기록은 실제 착수 시 추가한다.

## 보류된 모델 호출 횟수 최적화

상태: 보류. Agent 기능·흐름 연결과 디테일 검증을 먼저 진행하며 재개 시점은 별도로 정한다.

현재 확인된 사례는 완료 분석의 후속 보고서 재작성이다. 050의 실제 모델 시험에서 두 번 모두 첫 응답의 본문 수치가 검증에 거절되고 정정 응답이 통과했다. 호출은 각 2회, 완료 시간은 평균 34.48초였다. 후속 설명은 두 번 모두 첫 응답에 통과했으며 평균 10.76초였다. 각 조건 두 회의 고정 입력 시험이므로 일반적인 호출 횟수나 성공률을 보장하는 수치가 아니다. [050 변경 기록](050-compact-answer-facts.md), [측정 보고서](../reports/answer-efficiency-2026-10-02.md)를 근거로 삼는다.

재개할 때 다룰 범위:

- 보고서의 거절된 첫 응답과 모델 입력을 대조해 수치 작성의 원인을 확인한다. 이전 대화의 수치 표 복사 가능성은 아직 가설이다.
- 검증 가능한 중복 근거만 모델용 문맥에서 정리하고, 정성 해석문과 근거 선택의 출력 계약을 검토한다. 사용자 요청과 원래 공개 대화 이력을 보존한다.
- 실제 값·source Run·소유권·완료 관찰 검증을 유지한다. 수치 검증을 느슨하게 만들어 호출 횟수를 줄이지 않는다.
- 계획 수립·결과 판단 등 다른 역할의 호출 횟수는 각 호출 목적과 필수 근거를 계측한 뒤 검토한다. 최초 Executor 실행 보고서와 후속 보고서 재작성을 별도 경로로 다룬다.
- 첫 응답 통과율, 호출 횟수, 성공·실패별 시간, 입력·출력 토큰, 사용자 요청 반영 여부를 같은 조건으로 비교한다. 중간 후보와 검증 실패를 최종 성공 평균에 섞지 않는다.

현재는 추가 prompt 수정이나 실제 모델 재측정을 진행하지 않는다.

## 프로젝트 메모리 이행 상태

051~053은 이전 항목 저장·공식 Store 전환·갱신 정책의 이력이다. 사용자 승인에 따라 [075](075-project-memory-document.md)에서 프로젝트당 단일 Markdown content/version과 GET/PUT/DELETE API로 대체했다. 공식 LangGraph Store는 유지하며 middleware의 고정 섹션 부분 갱신, 전체 문서 CAS, 초기화 버전 경계와 기존 데이터 migration을 구현·격리 검증했다. 공개 항목 ID/entries는 제거했다. 기본 manual, 선택적 auto_context와 설정/제약은 [현재 계약](../project-memory.md)을 따른다.

남은 범위는 이전 writer 중지 후 0028 migration·설정 제거·실제 배포 검증, 실제 LLM의 대상 섹션 의미 보존/사용자 의도 정성 평가다. 전체 문서 자동 요약·Executor 결과 자동 공유는 미구현이며 현재 기능으로 표시하지 않는다. system_prompt, 세션 이력, 원본 수치 근거와 메모리를 구분하고 세션 전용 요청/결론을 자동 확산하지 않는다. 원본 관찰은 메모리로 대체하지 않는다. 실제 모델·처리량 전후 측정은 이번 작업에 포함되지 않으며 기존 모델 호출 수 최적화 보류도 유지한다.

## 기존 보류 사항

- Executor 전처리 데이터 등록·조회 연계는 실제 API 구현 이후 진행한다. [042 계약](042-dataset-registry-contract.md).
- Workflow CRUD·pgvector 추천 풀 이행은 기존 후순위 결정을 유지한다. [현재 1.3 CRUD와 2.0-draft 실행 계약의 차이](../workflow-json-reference.md).
- 시스템 에러 처리·운영성 신규 개선은 사용자 우선순위에 따라 기능·디테일 검증과 종합 성능 검증 이후 진행한다. [우선순위 기록](README.md).

## 054 이후 현재 흐름 검증

이전 설문형 graph와 전용 역할을 제거하고 CLI·Studio·시각화를 현재 PlanningRuntime으로 통합했다. 현재 개발은 [054 기록](054-agent-runtime-cleanup.md)을 따른다. 과거 고정 commit의 벤치마크는 당시 재현용이며 현재 SSO·typed HITL·Executor 연계 부하 시험을 대신하지 않는다.

055에서 실제 Runs API의 cookie/CSRF→실제 모델 계획/편집/승인→실제 Executor→후속 질문/Markdown→official Store/새 세션 참조를 연결해 검증했다. [055 기록](055-authenticated-executor-flow.md)을 따른다. 056에서 현재 인증·HITL·접수 모드를 지원하는 서비스-only harness와1/10/30/50명 비교를 구현했다. 실제 Executor 포함 용량은 이 결과와 구별한다. 모델 호출 최적화·Registry·운영 보완의 기존 보류는 유지한다.

## 055에서 확인한 입력·커널 점검

우선 Agent 기능·디테일 검증 항목이다. 운영 장애 대응 신규 개발이나 모델 호출 횟수 최적화와 구분한다.

- 명확히 지정한 trusted dataset의 input_values 자동 채움: 첫 실제 계획에서 default-nce 요청에도 필수 dataset_id가 비었고 approval은 422로 차단됐다. 다음 실제 계획에서는 채워졌다. 단순 ID 언급을 선택·승인으로 자동 간주하거나 API 필수값 검증을 풀지 않는다. prompt/schema/응답 검증의 원인과 required/has_value=false의 프론트 제출 UX를 함께 검토한다. 현재 진단의 테스트 사용자가 빈 참조를 명시하여 승인하는 동작과 모델 자동 채움을 분리한다.
- 커널 프로파일 의존성: 로컬 default Jupyter의 sklearn 부재로 isolation_forest가 실패했고, 승인한 level 1·1회 범위에서 함수 원문 유지/method=zscore 재실행이 성공했다. 실제 배포 kernel spec/library와 등록 Tool의 조건부 라이브러리를 대조한다. Agent/API 이미지의 package 설치를 Jupyter 가용성으로 해석하지 않는다. 코드/Tool/커널 정보 어디에서 가용성을 제공할지 검토하고 Executor 이미지 수정은 별도 요청 범위로 정한다.
- 보고서 Artifact/노트북 셀 등록은 별도 정책 결정이 필요하며 현재 completion은 artifact_registration=deferred다. Markdown 응답·Run 문맥 보존과 파일 등록을 구분한다.

## 056 서비스 처리량 후보 이후 배포·연계 용량 검증

상태: 로컬 service-only 코드·설정 후보 완료, 운영 용량 미확정. [056](056-service-throughput-tuning.md)·[상세 근거](../reports/service-throughput-2026-10-03/README.md)·[설정](../service-throughput-settings.md)을 따른다. 모델 호출 최적화는 기존 보류를 유지한다.

- 실제 Kubernetes Pod CPU/memory 제한에서 단일process 한도16/32를 비교한다. 모델 병목은 별도 구분하고 실제모델 동시 허용량을 확인한다. 플랫폼 replica수를 제어하는 정책을 전제로 삼지 않는다.
- 같은PostgreSQL 인스턴스에 붙는 모든 pool/API·Agent·Executor·배치·replica 연결 예산을 확인한다. pool10/overflow0만으로전체상한이생기는것은아니다.
- 057에서 실제 HTTP Executor 제출·Redis 결과event·리포트/SSE까지 fixture로 서비스 비용을 측정했다. [057](057-executor-service-throughput.md). 실제 Executor는 각1건 기능 대조이며 계산 부하 용량은 미확정이다. 후속질문·보고서 재작성·Store/memory 읽기·자동갱신 비용은 다음 서비스 성능 범위로 남긴다.
- 실제유입률과계획편집·후속질문·동시탭비율에맞는지속부하를확인한다. 유한50명burst처리량을안정도착률로환산하지않는다.
- 현재flow SQL약390회/사용자의목적별분포를검토한다. 추가비용이입증되면권한·원자성·멱등성을보존하는개선을선택한다. 설정한도확대와SQL개선을같은성과로합산하지않는다.

본시험용profile은 자동적용/배포하지않았다. 원래checkout·.env·기존컨테이너유지.


## 057 이후 처리량 우선순위

- 승인→Executor HTTP→Streams 결과→Event Worker 재개→리포트/SSE 서비스 경로를 완료했다. 동일50명·Agent32의시간개선은4.0%, CRUD SQL13.8%감소다. 0초39Defer재전달개선은정상비용비교와구분한다.
- 보존된 후속 성능 범위: 모델응답을fixture로고정하고 후속설명/보고서·project_memory manual/auto_context 읽기·갱신의 서비스 SQL/CPU/DB연결수명을 확인한다. 위 리뷰 반영 계획의 통합 전후 검증 단계에서 다루며 모델호출횟수·prompt최적화는보류한다.
- 실제Pod자원제한·model허용량·전체DB연결예산·여러Pod결과인계/주기scan은현로컬결과로확정하지않는다. Agent16/32는트래픽별차이가있고 Event한도를8/16으로자동확대하지않는다.
- 보류10명에서Agent/Event0·psycopg반환은확인했으나첫표본CRUD0~1의1개소유자는기록되지않았다. 추가owner진단표본은모두0이다. 재관찰시소유구간을측정하고장기누수로미리판정하지않는다.

- **운영 검증 후속(우선순위 유지):** 064 PG 회귀 종료 시 asyncpg SSL upgrade Future 경고 1회. 상태/풀 관련 8개 debug 검사에서는 미재현. 어떤 개별 취소/종료 타이밍인지 미확정이며 [검증 로그](../reports/analysis-state-lifecycle-2026-10-04/README.md)에 기록했다.

## 069 이후 처리량 우선순위

[069](069-service-cpu-profiling.md)에서 저장 후보와 독립적으로 CPU를 진단하고 워커 claim 구문을 재사용했다. 50명3회 평균 시간3.1%·CPU3.2% 감소였고10명은 거의 차이가 없다. 다음은 상태 snapshot의 반복 SQL 구조 생성 비용을 따로 검증한다. 프로파일러는 속도 시험에서 끄고 owner·동일 세션 순서·공개 응답·결과 무결성을 유지한다. 모델 호출 횟수와 기존 보류 항목을 앞당기지 않는다. 베이스 병합/푸시와 운영 배포는 별도다.

## 070 이후 처리량 우선순위

[070](070-public-run-query-reuse.md)에서 공개 Run 상태 조회문을 재사용했다. 동일 한도·표준50명3회 평균 완료22.8%·API CPU23.9% 감소였고,20 Operation의1명은 개선이 없었다. 다음은 잔여 SQL 실행/ORM 처리와 DB roundtrip을 목적별로 분해하여 실제 개선 가능한 단일 구간을 확인한다. 조회·승인·권한·멱등성·세션 순서·공개 결과 무결성을 유지하고 모델 호출 수/Registry/Workflow CRUD/운영 보완의 기존 보류를 유지한다. 베이스 병합·원격 push·운영 배포는 별도다.

## 071 이후 처리량 우선순위

[071](071-result-log-replay-batch.md)에서 같은 Run·transaction의 로그/이벤트 완성 여부를 일괄 조회했다. 동일 입력3이벤트 신규SQL16→14·replay4→2, 표준50명3회 평균 시간·CPU는 사실상 같아 속도 개선을 입증하지 못했다. 후보 채택을 보류하고 다음 성능 작업은89f65ea(070) 소스에서 분기한다. 필요한071 검증 기록만 가져가며 후보 runtime을 자동 승계하지 않는다. 다음은 잔여 INSERT/sequence/Task 식별·메시지/권한 비용과 안전한 묶음 처리의 효과 대조다. 장기 history의 lookup RSS 상한도 미검증으로 남긴다. 모델/Registry/Workflow CRUD/운영 보완·066·067 보류 및 미병합/미푸시/미배포 상태를 유지한다.

## 072 이후 처리량 우선순위

[072](072-task-event-atomic-insert.md)에서 원자 CTE와 축소 RETURNING을 별도 구현·회귀·측정했다. 표준50명3회 평균 완료 16.452→17.132초(4.13% 증가), API CPU 15.771→16.242초(2.99% 증가). 신규3이벤트16→10/replay4→4와 실제 시간을 분리하며 두 후보 모두 채택 보류다. 다음 작업은 f229b9e(070 runtime+071 기록)에서 분기하고072 문서만 가져간다. 결과 재개의 권한·상태·메시지 조회를 목적별로 대조하여 실제 중복과 CPU 비용부터 검증한다. 필수 보호를 제거하지 않는다. 실제 Pod/지속 유입/원격 DB/RSS·모델/Registry/Workflow CRUD/운영·066/067/071 보류와 미병합·미푸시·미배포 상태를 유지한다.

## 073 조회 목적·동일 트랜잭션 중복 정리

[073](073-result-resume-query-audit.md)에서 준비/Executor 반영의 같은 행 재조회12회/흐름을 제거했다. 권한 발견/재확인·User/Project 보호·Message dedup·서비스 replay 복구는 유지했다. 50명3회 평균 완료 16.458→16.226초(1.41% 감소), API CPU 15.784→15.576초(1.32% 감소). 관련158회귀·22회446흐름·검산1,846개, 후보 유지 권장이나 큰 성능 개선으로 표현하지 않는다. 다음은 모델 호출 수를 건드리지 않고 CPU 프로파일의 SQL 실행 준비와 ORM 결과 생성 비용을 분리해 더 큰 비용을 선택하는 검토다. 073 이후 기준 runtime은 이 후보를 유지하는 방향이며071/072 구현을 되가져오지 않는다. 실제 Pod/지속 유입/원격 DB/RSS·모델/Registry/Workflow CRUD/운영·066/067/071/072 보류 및 미병합·미푸시·미배포를 유지한다.

## 074 SQL 준비·ORM 결과 비용 분해

[074](074-sqlalchemy-cost-diagnosis.md)에서 서비스 소스를 변경하지 않고 caller CPU 진단을 수행했다. 표준10명 SQL expression/cache0.734초·compiler0.078초 대 ORM loading 모듈0.104초, Log/Event 쌍410 SELECT·Task 연결230 SELECT를 확인했다. driver/JSON/builtin을 포함한 전체 읽기 비용이나 불필요 중복 횟수로 해석하지 않는다. 다음 후보는 Log/Event 및 공통 User/Session 조회의 SQL 구조 재사용을 한 묶음으로 검토하고 profiler off 전후 비교로 채택을 판단하는 것이다. 권한/잠금/복구·매 호출 실제 SELECT를 유지한다. 4시도22흐름 오류0·진단 테스트3개·원본 검산 완료이며 속도 개선 주장은 없다. 073 runtime과066/067/071/072 보류, 모델/Registry/Workflow CRUD/운영 후순위를 유지한다. 베이스 미병합·미푸시·미배포다.

## FK·역참조 인덱스 검토 — 2026-10-04

[40개 FK 검토·격리 16개 probe](../reports/foreign-key-review-2026-10-04/README.md)를 완료했다. 서비스/DDL 변경과 성능 개선 측정은 없다. 핵심 FK는 유지하며 성능 후보로 `task_events(task_id,sequence)`의 중복 일반 인덱스 제거를 단독 검증할 수 있다. 누락 7개 역참조 인덱스는 물리 정리/조회 패턴에 따라 선택하고 일괄 추가하지 않는다. Command/Run/Task 소속 검증, Log→SSE CASCADE 및 Workflow 출처 보존·레거시 테이블 정리는 별도 기능/정책/운영 후속이며 기존 우선순위와 074 후보를 자동 대체하지 않는다.

## 관리 API 리뷰 — 2026-10-04

[075](075-project-memory-document.md)에서 프로젝트 메모리를 단일 문서/API로 정리했다. 다음 세션 API 리뷰에서 프로젝트 간 세션 이동은 요구사항이 없다는 결정으로 [076](076-session-project-fixed.md)에서 제거했다. 이동/복사 기능은 현재 개발 항목이 아니다. [077](077-session-settings-contract.md)에서 settings를 kernel_profile로 제한하고 생성 시 기본값·허용 목록을 검증/저장했다. Run 모델/승인 실행 정책과 분리했으며 기존 데이터 소급 보정·실제 원격 커널 가용성은 미검증이다. 남은 관리 API 리뷰는 프론트의 입력 가능 여부·실행 Run 조회 연결이며 API/DB 상태 표현을 먼저 확정한다.


## 세션 관리 API 리뷰 반영

076에서 프로젝트 간 세션 이동을 제거했고, 077에서 kernel_profile만 생성 시 검증·확정하도록 제한했다. 078에서 세션 공개 Run/대화 availability와 실제 새 요청·resume 정책을 통일했다. 현재 계약은 [세션 API](../session-api.md)다. 프론트의 초기/복귀 GET→기존 Run SSE 연결과 busy/blocked/allowed_actions 표시를 실제 UI에서 확인하는 것은 후속 통합 검증 범위다. 이름 변경/삭제 권한을 availability로 대체하지 않는다. 기존 Message CUD·Workflow CRUD의 후순위 결정은 유지한다.


## Run 조회 API 리뷰 — 079

[079](079-run-read-contract.md)에서 목록 요약과 상세 결과를 분리하고 단건 GET과 중복인 join을 제거했다. 현 명세는 [Run API](../public-run-api.md#run-목록과-상세-조회--079)다. [080](080-run-diagnostic-logs.md)에서 logs를 owner-scoped 진단 조회로 확정하고 페이지 기본50·최대200개 및 정확 일치 필터를 추가했다. 저장/TaskEvent 원자 생성은 유지한다. 관리자 전체 조회·로그 보존/정리·payload 바이트 상한은 이번 범위가 아니다. 프론트 진행/재접속에는 기존 SSE를 사용하며 별도 로그 GET이 필수는 아니다. 목록 token/result나 join을 사용한 외부 프론트의 이행 확인은 후속 통합 검증이다.


## 진단 로그 계약 — 080

logs GET의 배열 응답을 items/page로 바꾸었으므로 외부 관리/진단 화면의 response.items 적용을 확인해야 한다. 일반 채팅은 기존 SSE와 Run 상세 GET을 사용한다. Agent 호출 횟수 최적화, Message CUD·Workflow CRUD 후순위, 실제 UI 검증 등의 기존 우선순위는 유지한다. 이번 로그 조회 정리를 로그 저장 축소나 운영 trace 구현으로 해석하지 않는다.


## Task 조회를 공개 Run 하위로 통합 — 081

[081](081-run-diagnostics-contract.md)에서 Task 독립 공개 목록/상세를 제거하고 소유자/관리자의 Run diagnostics·invocations로 통합했다. 내부 Task/Worker/checkpoint/점유 책임은 유지한다. [현재 계약](../run-diagnostics-api.md)을 따른다. 기존 Task path와 public_run_id·평면 Task 응답을 쓰는 외부 관리 화면은 Run 주소 및 nested task/session_work로 전환해야 한다. Run 연결이 없는 orphan Task 탐색은 별도 관리자 후속이며 이번에 추가하지 않았다. 사용자 입력 제어는 Session availability와 Run 상세/SSE를 사용한다. Message CUD·Workflow CRUD·운영 복구·모델 호출 수의 후순위는 유지한다.


## 관리자 사용자 읽기 계약 — 082

[082](082-admin-user-read-contract.md)에서 관리자 GET /users의 요약 페이지·검색·role/status 필터와 삭제 사용자 상세 읽기를 구현했다. SSO 일반 사용자/기본 프로젝트 자동 생성·삭제 사용자 자동 복구 금지·관리자 보호·소유권을 유지한다. 관리 화면은 [현재 사용자 계약](../user-identity-api.md)의 공개 user_id·목록 is_active·상세 delete_yn을 사용한다. 이번 검증은 격리PG37·전체src681·wheel·필드 주석이며 사내SDK 실연결·실제 프론트 이행·대규모 사용자 부분 검색의 성능 측정은 남아 있다. 사용자 복구 API는 이번 요구사항이 아니며 추가하지 않았다. Message CUD·Workflow CRUD·운영 복구·모델 호출 수의 후순위는 유지한다. 베이스 미병합·미푸시·미배포다.


## 프로젝트 기본 CRUD 계약 — 083

[083](083-project-crud-contract.md)에서 프로젝트 요약 목록과 상세를 분리하고 요청의 알 수 없는 필드·명시적 null·빈 PATCH를 거절했다. 지침의 빈 문자열 초기화·내용 변경 시 버전 증가, 새 Run 실행 시 snapshot·기존 재개 고정을 문서화하고 PG/Worker/checkpoint·middleware로 검증했다. 삭제 결과 DTO를 제거하고 기본 프로젝트/미종료/점유/소유권 보호는 유지한다. [현재 계약](../project-api.md)을 따른다. 실제 프론트는 목록의 지침/버전 접근을 상세 GET으로 바꾸고 미변경 PATCH 필드를 생략해야 한다. SSO SDK 실연결·실제 모델의 지침 준수·Pod 성능/이행 확인은 후속 통합 검증이며 속도 향상 측정은 이번 범위가 아니다. Message CUD·Workflow CRUD·운영 복구·모델 호출 수의 후순위와 미병합·미푸시·미배포 상태는 유지한다.


## 미사용 인프라 관리 API 정리 — 084

[084](084-unused-infrastructure-apis.md)에서 실행 경로와 연결되지 않은 Jupyter registry3개·Redis ping1개 operation과 전용 코드·설정을 제거했다. [설정/클라이언트 이행](../infrastructure-api-cleanup.md)을 따른다. 기존 jupyter_servers 테이블은 이력·데이터 보존을 위해 남기고 autogenerate의 우발적 삭제 제안만 제외한다. 실제 물리 정리는 데이터 보존/복원 검토 이후 별도 migration이다. 다음은 SSO→프로젝트→세션→Run→HITL/resume→Executor 결과/SSE의 통합 계약 검증이다. 실제 외부 프론트 이행·사내SDK 연결·Executor 데이터 registry 계약은 미완료이며 Message CUD·Workflow CRUD·운영 복구·모델 호출 수 최적화는 기존 후순위를 유지한다. 이번 API 삭제를 Worker 구조/처리량 개선이나 실제 배포로 해석하지 않는다. 베이스 미병합·미푸시·미배포다.


## 현재 API 통합 검증 후속 085

[085](085-api-contract-flow-verification.md)에서 실제 서버 연계14항목과 PG 회귀81개를 확인했다. 다음 우선 검토는 사용자에게 필요한 Tool 선택 인자/기본값을 계획·typed form에 얼마나 노출할지다. 현재 arguments와 편집 정책에 선언한 인자만 수정하며 모든 Python 인자를 자동 노출하지 않는다. statistics.columns 수정422를 실제 확인했고 운영 정책을 임의로 확대하지 않았다. 입력/출력 참조와 사용자가 수정할 파라미터를 구분하여 생성 규칙·Workflow 작성 가이드·UI를 맞춘다.

승인 계획의 사용자 제외와 최종 skipped_steps의 실행 중 조건/의존성 skip은 별개다. UI는 interaction.resolved/승인 계획을 함께 사용한다. 실제 프론트 적용과 사내SDK 왕복·실제 모델 의도 분류/보고서 품질은 여전히 미검증이다. Markdown Run 결과는 제공하지만 Artifact POST·파일/노트북 셀 저장은 미확정 후속이다. 데이터 registry 외부 구현과 모델 호출 최적화·장애 대응의 후순위 결정을 유지한다. 이번 기능 검증 시간은 처리량/성능 개선 수치가 아니다.
