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


## 기능 테스트 화면 086

[086](086-functional-test-console.md)에서 기존 demo와 별개인 [독립 HTML](../../tools/test-console/index.html)을 만들었다. 현재 API·SSE/HITL·관리 기능은 이 화면으로 수동 확인할 수 있다. Node/실제 HTTP 연계는 검증했지만 Mac 잠금으로 실제 브라우저 레이아웃·클릭·다운로드·SSO 왕복은 미검증이다. UI를 직접 확인하고 실제 SDK/모델/플랫폼에서 연결을 검증해야 한다. 보고서 파일 등록·데이터 registry·파라미터 노출 정책 등 기존 후속을 화면 구현으로 완료 처리하지 않는다. 운영 프론트 제품화·전체 접근성/브라우저/성능 검증은 별도다.


## Tool 파라미터 노출·편집 정책 087

[087](087-tool-parameter-review.md)에서 085의 statistics.columns 누락을 해결했다. 등록된 사용자 파라미터와 실제 AST 기본값을 공통 계획 검토에 적용하고 Workflow 제약·input/decision·편집 검증을 연결했다. 코드/데이터/출력/문맥 참조 편집은 유지해서 금지한다. [작성 가이드](../tool-parameter-policy.md)를 따르며 기타 레거시 Tool의 정책은 개발자가 추가할 수 있다.

관련134회귀·격리PG4·HTML8·실제HTTP/Executor12 통과. 최신 진단 화면18101을 사용한다. 다음 기능 검증은 실제 모델의 명시 데이터/선택 인자 자동 채움과 새 폼의 자연어→편집→승인 품질, 실제 브라우저/SDK·커널 가용성이다. 074 SQL 구성 재사용 후보·최종 지속부하/Pod 자원 확인은 남으며 모델 호출 수 최적화·Registry/Workflow CRUD·Artifact/운영 후순위를 유지한다. 정책이 asset revision에 포함되므로 기존 대기 계획의 배포 이행은 별도다. 미병합·미푸시·운영 미배포.


## 실제 모델 기능 검증 088

[088](088-real-model-parameter-validation.md)에서 실제qwen·API·DB·Worker·Executor/Jupyter로 자동 입력·편집 반영·통계 이후IQR 선택·실행 없는 후속 답변을 확인했다. 미정 데이터 null 오류와 보고서 본문 누락을 수정·재검증했고 초기 실패는 보존했다. Agent341회귀·현행API15·실제모델 구조28+재검증11 통과이며 이것을 모든 모델 품질 또는 성능 향상으로 일반화하지 않는다.

다음 기능 검증은 실제 브라우저/프론트 연결·폐쇄망SDK·허용 커널 의존성과 다양하고 불명확한 자연어 요청/보고서 의미 검토다. 사용자 언급 컬럼을 채우는 것은 실제 컬럼 존재 검증이 아니며 Dataset Registry 외부 구현·보고서 Artifact·Workflow CRUD는 여전히 후속이다. 모델 호출 수/metadata 추가 조회·JSON 재시도 비용 최적화는 후순위 목록에 유지한다. 기존074 SQL 구성 재사용 후보·지속 부하/Pod 자원 검증도 남는다.

제출 전 모델 구조 응답 검증이 소진됐을 때 recovery_required·cancel409로 분류되는 사례는 운영 복구 검토에 추가한다. 이번 작업은 안전 검사나 복구 정책을 완화하지 않았다. 소표본 반복과 일부 본문 확인만으로 정성 해석의 진실성을 보장하지 않는다. 임시 진단API/DB는 제거했고18101은 기존고정모델 화면이다. 미병합·미푸시·운영 미배포.


## 실제 모델 콘솔·브라우저 검증 — 089

[089](089-real-model-test-console.md)에서 테스트 직원 로그인과 실제 모델을 분리하고, 실제 브라우저로 HITL 편집·승인·Executor 결과·SSE/세션/재로그인 복원을 검증했다. 목록 상태 잔존·POST SSE 접수 시 입력 잠금/종료 표시를 수정했다. 현재18102는 테스트 로그인+실제 모델+실제 Executor, 이전18100/18101은 고정 모델이다.

다음 Agent 기능·디테일 우선 검토는 **최종 승인 계획과 보고서 해석의 일치**다. 사용자가 max_val/x를 max_val로 바꿔 승인하고 실제 통계도 max_val만 수행했으나, 보고서가 x를 원래 목표로 설명하고 미산출을 언급했다. 실행 위반은 확인되지 않았고 최종 계획/설명문/보고서 입력 문맥의 어느 지점이 원인인지는 미확정이다. 승인 내용 변경 후 대상 컬럼·Tool 제외·사용자 의도가 보고서와 후속 답변에 반영되는지 실제 모델로 검증한다. 이는 모델 호출 횟수 최적화와 별도 기능 정합성 검토다.

콘솔 Markdown 표/inline formatting 개선은 사용성 후속이다. Dataset Registry·보고서 Artifact·Workflow CRUD·폐쇄망 SSO/Gaia/배포·기존 성능 후보와 운영 보류를 유지한다. 이번 single-user E2E를 처리량 개선이나 전체 기능 검증 완료로 해석하지 않는다.


### 최종 승인 범위와 보고서·후속 문맥 — 090

[090](090-approved-plan-report-context.md)에서 원인을 확인하고 수정했다. 실제 실행은 최종 승인값을 사용했지만 report와 session 분석 문맥은 최초 goal만 전달했다. 현재 유효한 승인 Step/파라미터·사용자 제외와 실제 상태를 함께 전달하고 원래 목표를 requested_goal로 분리한다. API·DDL·Executor 제출 규격은 변경하지 않는다. Agent345·진단13·wheel 및 실제 연계 시도별 검증은 완료 기록/상세 보고서를 따른다. 원문 의미 전체의 자동 검증, 과거 기록에서 없던 승인 범위 복구, 처리량 개선으로 일반화하지 않는다.

추가 기능 검토: 실제 모델이 workflow_input 참조에 편집 가능한 Step parameter_controls를 중복 선언한 계획을 세 번의 JSON 교정 후에도 반환해 승인 전 검증에 실패했다. 090의 보고서 수정과 별개인 **계획 생성 계약 준수/교정 품질** 문제다. validator를 완화하거나 참조를 임의 literal로 바꾸지 않는다. 잘못된 필드/바인딩을 명확히 알려주는 피드백, 계획 예제/동적 prompt, 반복 자연어 요청 평가를 다음 Agent 기능 검토 후보로 남긴다. 모델 호출 횟수/재시도 비용 최적화는 계속 후순위다.

보고서 정성 후속: 090 실모델에서 DataFrame head의 표시 제한을 통계 Tool의 전체 입력/산출 범위 제한처럼 서술한 사례가 남았다. observation preview가 잘렸다는 것과 Tool이 표본만 계산했다는 것은 다르다. 실행 근거의 표시 범위·입력 범위·통계 방법의 한계를 구분하는 prompt/관찰 계약과 실제 평가가 필요하다. 최종 승인 범위의 이번 구조 검산으로 완료 처리하지 않는다.


## 예시 자산과 공통 Agent의 독립성 — 091

사용자는 현재 예시 Skill·Tool에 핏한 공통 구현을 금지하고 지속적인 자산 유지보수를 재확인했다. [091](091-asset-independent-planning.md)에서 현재 2.0 실행 경로의 특정 함수명 검사·공통 prompt 예시를 등록 연결 정책과 metadata 기준으로 바꿨다. 한 파일의 여러 Tool, 변경 정책/소속의 revision, 반환 힌트, 별칭의 정책 상속, 심볼릭 링크 자산 root를 보완했다. 기존 예시 없이 두 풀의 그래프·실제 함수 실행, 실제 LLM 계획/HITL, 18 Skill·100 Tool 등록/조회, API/패키지 회귀를 확인했다. [계약 가이드](../agent-development/skill-tool-contract.md)를 따른다.

현재 1.3 Workflow 관리·컴파일 지원에는 특정 로드 구성 가정이 남는다. Workflow CRUD/2.0 이행 검토 시 그 공통 가정을 제거하되, 사용자 요청대로 workflow 자산 패키지와 유지보수 경로를 보존한다. source/metadata revision이 달라지는 배포와 기존 HITL/실행 중 복구의 drain/호환 이행은 운영 후속이다. 두 실제 모델 성공을 090의 계획 교정 문제 전체 해결이나 실무 자산 의미 품질의 보장으로 해석하지 않는다. 관찰 preview와 실제 처리 범위의 구분, 다양한 입력/결과 기반 조건의 모델 평가가 남는다. Dataset Registry·보고서 Artifact·Workflow CRUD·SSO/Gaia/Pod 성능·운영/모델 호출 수의 기존 후순위를 유지한다. 베이스 미병합·미푸시·운영 미배포다.

## 092 Workflow 표준 이후 남은 이행

공개 규격과 직접 POST/PATCH/승격·공통 실행 계획 연결은 [092](092-workflow-standard-contract.md)에서 구현했다. pgvector 검색/embedding 재색인·등록 template conversation 연결은 [094](094-workflow-hnsw-retrieval.md)에서 구현했다. 남은 작업은 실제 embedding 품질/동시 부하·ANN tuning, 내부 생성 계획의 공개 규격 exporter,1.0/1.3 명시 마이그레이션, 기대 산출물의 실제 파일/Artifact/Dataset 연계다. 순차 공개 계획의 Agent 재계획·오류 수정은 기존 상한과 승인 정책을 유지한다. 실제 사내 LLM·Executor HTTP/Redis 연계와 Pod 배포 검증은 별도이며 double 실행 검증을 대체 근거로 사용하지 않는다.


## 093 다중 쿼리 Workflow 검색 이후

[093](093-workflow-retrieval-benchmark.md)에서 50/500개 쿼리 집중의 후보 소진과 HNSW 반복 제외의 속도·그룹 TOP5 누락을 검증했다. [설계](../design/workflow-retrieval-and-registration.md)를 따르며 고정 overfetch 배수나 결과5개 확보를 정확성 보장으로 쓰지 않는다. 실행 정의2.0과 현행CRUD는 유지한다.

바로 다음은 근사 추천의 허용 품질/정확한 전역TOP5 요구, 실제 embedding 모델·차원·현업 정답 쿼리·corpus/지연 목표 확인이다. 이를 바탕으로 user_queries 등록·검색 revision·pending/ready/failed·POST/PATCH/승격/복제·검색 응답 계약을 확정하고 Agent 추천 풀에 연결한다. 정확 fallback의 전량 비용과 개수5개 확보 시에도 후보 누락을 감지하지 못하는 문제를 함께 다룬다. 신규 API·migration·실제 embedding·자연어 추천/E2E·동시부하는 아직 미구현/미검증이다.

HTML의 구조·출처는 검증했으나 Chrome 시각 검증은 시간 초과로 미완료다. 모델 호출 수·Dataset Registry·Artifact·운영 후순위와 기존 성능/배포 과제는 이번 검색 진단으로 완료 처리하지 않는다.


### 093 후속 사용자 결정 — HNSW 사용

전량 거리 계산 기본안 대신 HNSW를 사용하기로 했다. [구현 방향](../design/workflow-retrieval-and-registration.md#사용자-결정-hnsw-사용--구현-방향)에 활성 검색용 인덱스·Workflow 제외 반복 탐색·발견 후보 내 대표 점수 재정렬·검색 예산/부분 결과·Agent 적용 가능성 판단을 기록했다. 후보 확보 수와 UI의 추천+신규 계획 최대 수를 분리한다. 후보 내 재정렬과 실제 임베딩/동시 부하는 아직 미측정이며 전역 TOP5를 보장하지 않는다. 다중 user_queries 등록/수정·DDL·검색 계약·Agent 연결은 [094](094-workflow-hnsw-retrieval.md)에서 구현했다. 실제 embedding 품질·동시 부하·검색 튜닝을 다음 검증으로 남긴다. 이번 결정 기록만으로 해당 기능을 구현 완료로 표시하지 않는다.


## 094 이후 — 실제 임베딩 검증

[등록·검색 계약](../workflow-registration-and-search.md)이 현재 구현 기준이다. 실제 embedding BASE_URL/MODEL/DIMENSIONS 제공 후 운영 후보 모델 공간의 index provision·기존 자산 쿼리 등록/재색인, 자연어 E2E/부분 요청 scope와 적용 가능성 평가, 중복/집중별 그룹 Recall·동시 부하·threshold/ef_search/scan memory/문맥 예산 튜닝을 진행한다. 동일 벡터500개의3개 그룹 중1개만 찾은 smoke 한계를 완료로 닫지 않는다. 전체 exact fallback은 사용자 HNSW 결정에 따라 넣지 않았다. 운영 서버/사내 모델/Executor는 이번 작업에서 변경하지 않았다.
