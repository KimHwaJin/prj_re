# 038. 실제 모델 기반 계획·HITL·통합 Run/SSE

| 항목 | 내용 |
|---|---|
| 상태 | 계획 승인 저장 단계 구현·실제 모델/DB/SSE/Phoenix·회귀/wheel 검증 완료 |
| 시작일 / 완료일 | 2026-09-30 / 2026-09-30 |
| 브랜치 | feature/agentic-analysis-runtime |
| 기준 commit | 680c6d2 — feature/refactor-base에서 분기 |
| 구현·검증 commit | 0eb34ed314668743de1354f1433e12733144a97a — 코드 및 검증 기록 |
| 배포 상태 | 기존 Compose 변경 없음, 테스트용 서버 종료, 미배포 |

**문제와 영향**

036~037은 설계·prototype이었고 실제 API는 여전히 이전의 고정 설문 단계 Graph를 실행했다. 입력/재개 경로도 통합 설계와 달랐고, 내부 JSON/model 토큰·legacy 이벤트를 프론트가 승인 화면과 구별하기 어려웠다. 모델의 Skill 탐색 횟수와 계획 의미는 실제 연동에서 별도로 확인할 필요가 있었다.

**확인된 원인과 변경**

| 영역 | 변경 전 | 변경 후 |
|---|---|---|
| Agent | 단계별 고정 질문·분류·후보 생성 | 단일 create_agent가 질문 또는 등록 자산 기반 후보 제안 |
| HITL | 이전 generic command와 설문 응답 | typed edit_plan/approve_plan, 파라미터·단계 제외·정책 검증 |
| 요청 API | 시작 POST와 별도 resume POST | input/command XOR인 동일 POST, POST stream 추가 |
| SSE | task/agent/JSON 토큰에 의존 | message/activity/interaction/run envelope, durable cursor |
| 승인 결과 | 계획 UI·실제 소스가 별도 계약 | 서버 내부 source/hash/dataset 해석까지 승인 snapshot 고정 |
| Phoenix | CLI 중심 선택 초기화 | 중앙 설정과 API lifespan에 연결, batch export |

기존 durable queue·SKIP LOCKED·세션 보호·실행 슬롯·체크포인트 pool·receipt·이벤트 멱등성을 사용한다. 이를 새로 만든 성과로 계산하지 않는다. 디렉토리와 역할별 프롬프트는 이전 합의를 유지하며 기존 workflow 자산 패키지를 보존한다. Workflow 정의 schema/검증·계획 projection은 설치 가능한 service_contracts로 옮겨 실행 코드가 docs/scripts를 import하지 않게 했다.

실제 연동 첫 시험에서는 read_skill/search_tools의 같은 조회가 계속 반복되어 240초 안에 계획이 끝나지 않았다. Phoenix의 반복 ChatCompletion과 tool_call output에서 확인했다. 조회 round 제한·중복 결과 재복사 차단·최종 합성 전환 middleware와 recursion 제한을 추가했다. 다음 시험은 형식·승인이 통과했지만 준비된 데이터 대신 레거시 placeholder 추출/변환을 선택했다. 해당 3개 Tool에 test_only 가용성 정책을 명시하고 새 Runtime의 실행 후보에서 제외했다. 등록 YAML 재생성 시 정책을 보존한다. 최종 시험에서 요청한 공개 dataset_id와 data_load Step을 확인했다.

**실제 모델 검증**

[최종 결과 JSON](../reports/agentic-planning-real-validation-2026-09-30.json) 기준 단일 사용자 여정이다. 실제 qwen38-27b-nvfp4, 로컬 API 18090, 별도 PostgreSQL CRUD/checkpoint DB, Phoenix collector를 사용했다. Host alias는 테스트 프로세스에서 적용했고 기존 서비스 설정·hosts·Compose는 변경하지 않았다.

- 계획 제안: 44.121초, 후보 1개, 실제 모델 호출 6회.
- 선택한 Tool: data_load, profile_data, compute_statistics, detect_outliers.
- 기존 Parquet의 default-nce 공개 참조를 그대로 사용하는 것을 검증.
- 사용자 승인 저장: 0.268초, 공개 Run ID 유지, final_response.status=plan_approved.
- SSE opened/resolved·메시지·활동·상태 snapshot 전달, Phoenix trace 7개 실제 조회 확인.
- Executor 제출 0회. 실제 파일 load/품질 분석/이상치 검출/리포트 생성은 하지 않았다.

이는 기능 검증이며 처리량·평균/p95 개선 수치가 아니다. 한 모델 응답이 수 초라도 metadata 탐색·JSON 생성·검증 수정 호출이 여러 번 발생하므로 계획 전체 시간과 다르다. 모델마다 실제 행동이 달라 충분한 평가와 후속 성능 검증이 필요하다. 형식만 통과했던 [중간 결과](../reports/agentic-planning-initial-validation-2026-09-30.json)는 의미 검증 실패로 표시했다.

**회귀·패키지 검증**

[검증 요약 JSON](../reports/agentic-planning-verification-2026-09-30.json)에 실행별 결과를 기록했다.

- 전체 API/Agent 회귀 첫 실행: 665 passed, 7 failed, 2 subtests passed, 286.55초. 실패 7개는 제거된 resume 경로·변경된 SSE 이벤트명·공개하지 않는 내부 토큰·테스트용 typed resume wrapper에 대한 이전 기대값이었다.
- 이 기대값을 새 계약에 맞게 수정하고 관련 파일 및 새 계획 테스트를 함께 재실행: **83 passed**, 65.84초. 첫 실행 실패 7개를 모두 포함한다. 두 실행은 서로 겹치므로 665+83을 서로 다른 테스트 수로 합산하지 않는다. 테스트 수정 후 전체 suite를 다시 한 번 실행한 결과라고 주장하지 않는다.
- 메모리 및 실제 PostgreSQL에서 편집→새 interrupt→Graph/pool 재시작→승인, 두 번째 후보 선택, 멱등 재전송, 오래된 token/revision, 잘못된 제외, 데이터 범위, 소스 비노출, GET/POST SSE 재생을 검증했다. 기존 취소·동시 실행·복구·짧은 transaction 검증도 첫 전체 회귀에 포함했다.
- 내부 구조화 모델 토큰이 공개 writer를 호출하지 않음을 검증했다. 명시적 token buffer의 실제 DB row lock/timeout/손실 없는 drain 테스트는 유지해 통과했다.
- 설치 wheel 검사: source_checkout_imported=false, role prompt 8개와 create_agent builder 8개 생성, JSON schema/mock fixture 포함, 승인 snapshot/data binding 검증 통과.

실제 HTTP/LLM/Phoenix 검증과 자동 회귀는 분리했다. 이전 설문 Graph의 회귀가 통과한 것을 새 Executor E2E 통과로 계산하지 않는다.

**경계·다음 작업**

이번 완료 지점은 답변/계획 승인 저장이다. Agent API는 새 Runtime이며 이전 CLI/Graph/builders/Executor event Worker는 아직 이전 흐름으로 남아 있다. 기존 업무 DB의 Run이나 이전 checkpoint를 자동 이행하지 않는다. feature/refactor-base에 병합하거나 새로운 기능 브랜치를 push하지 않았다. 원본 feature/total_merge_v1 사용자 변경은 보존했다.

다음은 고정된 승인 snapshot으로 Executor 최초 제출·결과 관찰·조건/인자 판단·Operation 추가·리포트·Finalize를 연결하는 단계다. pgvector 추천/Workflow 관리·정식 PVC dataset catalog·project_memory·자유 코드 수정 정책·첨부/VLM·Gaia 등록 adapter는 아직 미구현이다. 데이터 선언은 테스트용 서버 설정이며 정식 데이터 카탈로그 구현이라고 주장하지 않는다. repair_level 화면과 검증은 있지만 실제 수정 행위는 Executor 단계와 함께 구현한다. 자연어로 후보 전체 재계획하는 HITL 명령은 후속이며 지금은 cancel 후 새 입력으로 다시 제안받는다.

**완료 판단**

계획 승인 단계의 코드와 실제 연동 검증을 완료했다. 전체 분석 Agent의 E2E 완료나 배포 완료를 의미하지 않는다.
