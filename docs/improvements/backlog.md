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

051에서 project_memory 항목 저장·읽기·미들웨어와 선택적 현재 발언 원문 추출을 구현했다. [051 기록](051-project-memory-runtime.md)의 추출 정책을 유지하며 저장·접근 구조는 [052 공식 Store 전환](052-langgraph-project-memory-store.md)을 따른다. 053에서 현재 사용자 원문을 근거로 한 짧은 주제 정리와 지속성/역할/입력 예산 정책을 추가했다. 전체 메모리의 자동 요약·Executor 결과 자동 공유는 미구현이며 현재 기능으로 표시하지 않는다. [현재 개발 계약](../agent-development/agent-runtime-contract.md), [공유 메모리 설계](../design/agentic-workflow-contract/README.md)를 따른다.

검토할 범위는 프로젝트의 공유 배경·분석 선호·공유 가능한 근거를 부분별로 저장하고 다음 세션의 Agent 문맥에서 읽는 것이다. system_prompt, 현재 세션의 대화 이력, 완료 분석의 실제 수치 근거와 역할을 구분한다. 세션 전용 데이터·수치·결론은 명시적 공유 없이 프로젝트 메모리에 자동 확산하지 않는다. 완료 분석의 원본 관찰은 메모리 요약으로 대체하지 않는다.

저장 형식·owner/source 검사·항목별 버전·동시 갱신은 052를, 설정 가능한 한도·역할별 입력·지속적인 갱신은 [053 기록](053-project-memory-policy.md)을 기준으로 한다. 기본 manual, 선택적 auto_context의 의미와 한계는 [현재 계약](../project-memory.md)을 따른다.

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
