# 현재 API·Agent·Executor 사용자 흐름 검증

현재 소스에서 **로그인 쿠키·CSRF → 실제 모델의 계획 → 편집·승인 → 실제 Executor/Jupyter → 결과 설명·Markdown 재작성 → 프로젝트 메모리·새 세션 참조**를 완료했다. 최초 실제 계획 한 회는 지정된 데이터 입력이 비어 승인 422로 중단됐고, 다음 실제 계획은 값을 채워 성공했다. 성공만 남기거나 최초 응답이 항상 자동 확정된다고 설명하지 않는다.

[검증 결과 JSON](api-executor-flow-verification-2026-10-02.json), [055 변경 기록](../improvements/055-authenticated-executor-flow.md)을 따른다. 기준은 054까지 포함한 dd867af이며 부하·성능 A/B가 아니다. 배포 서비스·.env·사용자 checkout은 유지했다.

## 무엇을 실제로 연결했는가

현재 FastAPI 앱을 임시 loopback 포트 18091에 띄웠다. 사내 SDK의 직원 검증 verdict만 명시적 fixture이며 /auth/login/sso, 신규 일반 User/기본 Project, localhost Redis 로그인 key·TTL, cookie·/users/me·CSRF와 소유권 의존성은 production 코드다. 인증 dependency override와 X-User-Id 우회는 없다. 실제 사내 SDK·사내 브라우저 SSO 왕복 성공이라는 의미가 아니다.

전용 localhost agentic_runtime_test/agentic_checkpoint_test에서 Alembic upgrade head를 적용했다. 현재 API 큐·Worker·LangGraph 체크포인트·official Store·실제 Redis Streams를 연결했다. 기존 Compose executor의 localhost:8000 API로 INLINE 코드를 제출하고 실제 Jupyter에서 등록 Tool을 실행했다. model/phoenix hostalias는 사용자 제공 주소를 적용했으며 credentials는 private 파일만 사용했다.

## 확인한 사용자 동작

| 구간 | 확인 결과 |
|---|---|
| 로그인 | 쿠키 없는 요청 401, CSRF 없는 생성 요청 403; 일반 사용자·기본 프로젝트 자동 등록 |
| 계획·HITL | data_load/profile_data/compute_statistics/detect_outliers 제안. 같은 Run에서 입력을 명시하고 MULTI 수정 수준 1·최대 1회로 정책 편집, 계획 version 1→2 |
| 로그인 갱신 | 로그아웃 후 조회 401, 재로그인 뒤 같은 Run·interaction·resume_token 보존. 이전 토큰 승인 409 |
| Executor 대기 | waiting_executor에서 새 동일 세션 요청 409, 사용자 resume_token 없음. 완료 뒤 같은 세션 새 요청 202 |
| 실제 실행 | 원래 Tool 4개, outliers 실패·1회 수정 재실행 포함 Operation 3개. 최종 Executor SUCCEEDED·터미널 이벤트 수신 |
| SSE | 실제 완료 Run의 31개 이벤트, 중간 cursor=16에서 이후 15개만 재전송. 단조·유일 sequence와 공개 응답의 private 코드 제외 |
| 완료 리포트 | Markdown status=ready, 6217자. 실제 완료 관찰과 검증된 Step 기반 근거 포함 |
| 후속 설명 | 같은 source Run·원본 관찰로 fact_ids 18개 해석·공개 표 반영. 새 Executor 제출 없음 |
| 후속 Markdown | fact_ids 16개 해석·원본 값 반영, source Run 확인. 새 Executor 제출 없음 |
| 프로젝트 메모리 | 수동 report_preferences.audience v1, 명시적인 지속 선호 요청으로 같은 topic v2·source Run/원문 quote 저장. 새 세션 모델 입력에서 같은 프로젝트 항목 참조, 입력 1002자 |
| Phoenix | 실제 시험 namespace에 24개 trace 존재. trace 수는 모델 호출 횟수가 아님 |

기존 완료 분석 문맥은 11685자, 누락 관찰/보고서 잘림 없음이었다. outliers는 FAILED와 SUCCEEDED 이력이 함께 있어 Step ID가 두 번 보인다. 모델의 후속 fact_ids는 현재 소유자/source의 성공 관찰로 해석한다. 문장 전체의 인과 추론과 업무 해석까지 자동으로 증명한 것은 아니다.

## 시간

| 구간 | 실제 모델 시험 |
|---|---:|
| 최초 계획 → 승인 대기 | 30.443초 |
| 승인 접수 → Executor 대기 | 0.384초 |
| 승인 후 분석·수정·리포트 완료 | 58.504초 |
| 완료 결과 설명 | 14.605초 |
| Markdown 재작성 | 15.610초 |

승인 이후 58.504초에는 실제 Operation 3개, 실행 오류, 모델 수정 및 재실행, 결과 판단·보고서가 포함된다. 순수 모델 추론 시간이나 Worker 큐 대기로 해석하지 않는다. 사용자의 HITL 읽기·입력 시간은 포함하지 않는다. 한 회의 성공 시험이며 평균/백분위·1~100명 처리량을 주장하지 않는다.

명시적인 mock 모델+고정 계획 시험은 계획 0.362초, 승인→대기 0.371초, 승인 후 완료 4.272초, Operation 2개로 통과했다. 이 비교는 모델·계획·수정 조건이 달라 성능 개선율로 쓰지 않는다.

## 발견한 쟁점

### 데이터 자동 채움이 한 번 누락됨

첫 실제 계획은 user request에 default-nce가 있었지만 Proposal.input_values에 해당 값을 넣지 않았다. 필수 dataset_id를 빈 채 승인하면 422였다. 이후 실제 계획에서는 Agent가 값을 채웠다. 프론트는 has_value=false·required를 확인해 빈칸을 표시하고 값과 승인을 함께 보내야 한다. 모델의 자동 확정 신뢰성은 후속 개선 대상이며, 입력 검증을 풀거나 알려지지 않은 데이터로 자동 대체하지 않았다. 두 사례로 일반적인 누락률을 계산하지 않는다.

진단 도구는 해당 고정 시나리오의 테스트 사용자가 default-nce를 확인·제출하게 한다. 누락은 initial_plan_unfilled_inputs로 남기므로 모델이 모두 알아서 채웠다는 시험으로 변환하지 않는다.

### 로컬 커널에 sklearn이 없음

실제 모델이 isolation_forest를 선택했고 detect_outliers 실행에서 ModuleNotFoundError: sklearn이 발생했다. 승인한 수준 1/최대 1회에서 함수 원문은 동일하게 유지하고 method를 zscore로 수정하여 재실행에 성공했다. 실패·재실행 source의 detect_outliers 함수 AST가 동일한 것을 확인했다. 의존성 부족이 해소됐다는 뜻은 아니다.

실제 기본 커널 이미지의 의존성 목록과 Tool/method 실행 가능 범위를 점검해야 한다. 에이전트 API 컨테이너의 pyproject에 라이브러리가 있어도 Jupyter 커널에 설치된 것과 같지 않다. Executor 이미지·사내 커널스펙은 이번에 수정/설치하지 않았다.

### 보고서 파일 등록은 아직 별도

현재 완료 response는 artifact_registration=deferred다. 보고서 Markdown 반환·Run/checkpoint 문맥 보존을 검증한 것이며, Artifact POST나 노트북 report-cell append를 수행했다고 주장하지 않는다. 관련 API 정책과 Executor Dataset Registry의 기존 보류를 유지한다.

## 나머지 검증과 코드 영향

쿠키 계약으로 이행한 기존 계획 재작성 진단 4개, 수정 수준/거절/한도/disabled/SINGLE 진단 8개를 실제 Executor로 통과했다. 이 시험의 초기 계획·revision/repair Agent는 fixture이며 실제 모델 자유 코드 작성 품질을 검증한 결과가 아니다.

관련 API·SSO·실제 PostgreSQL/Redis·Run/HITL·메모리·근거 회귀 132개가 36.59초에 통과했다. 기존 durability warning 11개를 숨기지 않았다. 이전 전체 884개 결과와 합산하지 않는다. 변경은 source용 진단 클라이언트와 문서이며 production API/Agent/계약/설정/의존성/DB migration 소스는 유지한다.
