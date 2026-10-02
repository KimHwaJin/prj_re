# 055 현재 쿠키 인증 API·실제 Agent·Executor 사용자 흐름 연계 검증

| 항목 | 내용 |
|---|---|
| 상태 | 구현·연계·관련 회귀 검증 완료, 베이스 통합·게시 예정 |
| 시작일 / 완료일 | 2026-10-02 / 2026-10-03 |
| 브랜치 | feature/api-executor-flow-verification |
| 출발 commit | dd867afc9c0f4e612fd248d07f4af212994fb930 |
| 배포 상태 | 미배포; 원래 사용자 checkout·.env·기존 컨테이너 유지 |

## 문제와 범위

054가 현재 PlanningRuntime으로 개발 경로를 통합한 뒤 실제 서비스 API의 새 계획→편집→승인→Executor→후속 답변·메모리를 연결해 검증해야 했다. 기존 세 연계 진단은 SSO 적용 전 X-User-Id와 관리자 bootstrap에 의존하여 현재 서비스에서 실행되지 않았다. 후속 근거 관찰도 이전 facts만 보고 최신 fact_ids를 수집하지 않아 제대로 검증할 수 없었다.

실제 사내 SDK는 폐쇄망 밖에 제공할 수 없다. 해당 SDK의 verified employee verdict만 loopback 진단 fixture로 주입하고, 실제 서비스 로그인·Redis session·cookie·CSRF·소유권·DB·큐·Worker·현재 모델·Executor/Jupyter·Phoenix를 사용했다. 운영에 가짜 인증 endpoint나 dependency override를 추가하지 않는다.

## 실제 변경

- scripts/diagnostics/cookie_auth.py: 임시 localhost 앱에만 직원 verdict fixture와 인증 origin/namespace를 설정. /auth/login/sso→/users/me로 최초 일반 User/기본 Project를 만들고 cookie·CSRF를 사용한다. raw 결과 파일은 0600으로 저장한다. 패키지/운영 진입점에는 포함되지 않는다.
- verify_agentic_executor_http.py: 실제 인증 경계의 401/403, typed 입력/정책 편집, 계획 revision, logout/login 후 대기 보존, 이전 resume 409, 완료 SSE cursor 재전송, 최신 fact_ids 해석·원본 값 반영, 수동/auto_context 메모리·새 세션 참조를 검증한다. 최초의 빈 필수 입력을 별도로 기록한다. 고정 시나리오의 사용자가 지정한 default-nce를 확인·제출하며 model auto-fill로 포장하지 않는다.
- verify_agentic_plan_revision_http.py / verify_agentic_repair_http.py: 같은 actual cookie/CSRF를 사용하도록 기존 진단 호출을 이행했다. 기존 Agent fixture·수정 단계·승인·Executor 검증의 의미는 유지한다.
- 현재 연계 안내의 X-User-Id 설명을 바로잡고 옵션·설정·실제 SDK 한계·DB/부하 검증 범위를 정리했다. [검증 보고서](../reports/api-executor-flow-verification-2026-10-02.md)와 [결과 JSON](../reports/api-executor-flow-verification-2026-10-02.json)에 성공·실패 사례를 함께 남긴다.

production API/Agent/계약/설정/모델 prompt/의존성/DB migration 소스는 변경하지 않는다. Executor 소스·커널 이미지·원천 Parquet를 수정하거나 의존성을 설치하지 않았다. 테스트는 새 UUID/namespace의 실행 파일과 노트북을 생성하며 기존 Executor 실행 기록은 수정하지 않는다. 전용 Agent 테스트 DB는 회귀 fixture가 초기화할 수 있으므로 private 결과·Executor manifest를 근거로 보존한다.

## 검증 결과

### 실제 쿠키·DB·Worker·Executor + mock 모델

고정된 등록 품질 계획으로 실제 서비스 로그인·편집·승인·실행·터미널 결과를 통과했다. 계획 0.362초, 승인 후 완료 4.272초, Operation 2개. 정책 편집 version 1→2, logout/login 대기 보존·이전 토큰 409, 실행 중 동일 세션 입력 409/완료 후 202, SSE 26개 중 cursor=14 이후 12개만 수신을 확인했다. 최초 no-op 값 편집의 version은 증가하지 않는 것이 정상이라 실제 정책 변경으로 진단을 수정했다. private writer·repair history capture를 추가한 최종 소스에서도 같은 actual Executor smoke를 재실행해 통과했다.

### 실제 모델 + 실제 Executor/Phoenix

fixture plan 없이 qwen38-27b-nvfp4가 등록 Tool 4개로 계획했다. 최종 실제 계획은 데이터 입력값을 채웠고 편집/승인 뒤 실제 Jupyter와 Redis 결과 이벤트로 완료했다. 계획 30.443초, 승인→대기 0.384초, 승인 후 완료 58.504초. 후속 결과 설명 14.605초/사실 18개, Markdown 재작성 15.610초/사실 16개를 원본 source·성공 관찰로 해석·공개 응답에서 확인했다. 후속 요청은 Executor를 새로 제출하지 않았다.

SSE 31개·cursor=16 이후 15개 재전송, 초기 Markdown report ready/6217자, 공식 Store manual topic v1→auto_context v2, 원문 quote·source Run 저장, 다른 세션의 같은 프로젝트 참조 1002자, Phoenix namespace trace 24개를 확인했다. Trace 수는 모델 호출 횟수가 아니다.

첫 실제 계획(39.037초)은 데이터 입력을 비워 approval 422로 중단됐다. 이후 실제 계획에서 자동 채움이 됐지만 누락 가능성을 해소했다는 증거는 아니다. 성공률/평균으로 합치지 않는다. API는 필수값을 검사하며 입력 검증을 느슨하게 바꾸지 않았다.

실제 실행은 outliers가 isolation_forest를 선택했을 때 로컬 커널의 sklearn 부재로 실패했다. 허용한 수정 수준 1·최대 1회에서 같은 detect_outliers 함수 원문을 유지하고 method=zscore로 수정 재실행하여 성공했다. 실패/재실행 함수 AST 동일, Operation 3개·실패 관찰 보존을 확인했다. 58.504초는 이 수정/재실행까지 포함하며 순수 LLM 추론 시간이나 큐 대기가 아니다.

### 관련 회귀·다른 진단

- 계획 재작성 fixture 4개(registered/free/clarification/modified)를 actual Executor에서 cookie/CSRF로 통과했다.
- 기존 수정 정책 fixture 8개(binding/source/replan/custom/reject/exhausted/disabled/single)를 actual Executor에서 통과했다. 이들의 Agent 응답은 고정 fixture이며 실제 모델 수정 성공률 검증이 아니다.
- 관련 API·SSO·실제 PostgreSQL/Redis·Run/HITL·메모리·근거 회귀 **132 passed, 11 warnings, 36.59초**. 실제 연계 프로세스를 종료한 뒤 순차 실행했다. 이전 전체 884개 결과와 합산하지 않으며 전체 재실행으로 표현하지 않는다.
- 마지막 mock actual-Executor smoke와 private JSON roundtrip/0600을 통과했다. 변경 Python AST·새 Markdown 링크·보고서 credential 값 제외, src/production config 375개 파일 원본 hash 동일, 현재 OpenAPI 38개 path와 documented 12개 path/28개 schema validation 동일을 확인했다. 원래 checkout HEAD/status/.env/기존 파일 316개도 동일했다.

## 제한과 다음 점검

[후속 목록](backlog.md)에 두 항목을 추가한다. 첫째, 명확히 지정한 데이터/인자의 input_values 자동 채움 신뢰성과 has_value=false의 화면/제출 처리를 검토한다. 둘째, 실제 Jupyter kernel profile의 의존성과 Tool/method 가용성을 대조한다. Agent API의 pyproject와 Jupyter 커널 설치 목록은 별개다. 사내 SDK·Gaia 전체 템플릿·Kubernetes·1주 실행·수십 GB 데이터·전체 부하는 이번 시험의 대상이 아니다.

현재 보고서는 Markdown 응답·Run/context 보존이다. artifact_registration=deferred이며 Artifact POST/노트북 report-cell append를 검증하지 않았다. Executor Dataset Registry·Workflow CRUD 이행·모델 호출 최적화·운영 에러 보완의 기존 보류를 유지한다. 이번 소스 수정이나 시험으로 성능 개선율을 주장하지 않는다.

## 적용·통합·게시

추가 dependency·환경변수·Alembic migration·운영 config 변경이 없다. 진단의 SSO 설정과 auto_context는 해당 임시 프로세스에만 주입한다. 기준 베이스에서 feature/api-executor-flow-verification으로 작업하며 검증 완료 후 fast-forward 병합·origin 게시 SHA를 기록한다.
