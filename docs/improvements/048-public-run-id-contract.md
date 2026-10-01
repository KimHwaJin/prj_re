# 048. 공개 Run 식별자 run_id 통일

| 항목 | 내용 |
|---|---|
| 날짜 | 2026-10-01 |
| 작업 브랜치 | feature/public-run-id-contract |
| 기준 commit | fa33605e56ea4cb86c7dac3cb740f946d3b8c49e |
| 구현 commit | 5651121b908fcc99a4de1489b8149286e62d4505 |
| 상태 | 구현·API 회귀 571개 검증 완료 / 베이스 병합·원격 게시 확인 |

## 문제와 변경

같은 공개 실행을 요청·SSE에서는 `run_id`, REST Run 응답에서는 `id`로 표시해 클라이언트가 이름을 바꿔 연결해야 했다. 사용자와 통일하기로 한 계약을 코드에 반영했다.

- PublicRunResource의 필드를 `id`에서 `run_id`로 변경했다. 접수·재개·조회·목록·join·취소 응답과 SSE snapshot의 data에 같은 이름을 쓴다. Location·X-Run-Id·로그 조회·구독 대상도 해당 공개 ID로 연결한다.
- DB의 run_id/public_run_id/checkpoint 참조는 이미 같은 공개 실행에 연결되어 있었다. DB migration 없이 기존 ID 값을 유지한다. 여러 번 resume할 때도 공개 run_id는 고정이고 현재 resume_token만 바뀐다.
- 실제 HTTP 테스트, 현재 진단/부하 클라이언트, OpenAPI/payload schema와 응답 예제·JSONC·필드 설명을 갱신했다. 과거/현재 버전을 비교하는 benchmark만 구형 `id` 읽기도 지원한다. 기존 부하 결과 파일의 관측 레코드 `id`는 분석 도구와의 호환을 위해 유지하며 새 API의 run_id를 기록한다.

API 응답의 구형 `id` alias는 제공하지 않는다. 클라이언트는 Run 응답의 `run_id`를 읽도록 변경해야 한다. 프로젝트·세션·Workflow·Tool 등 다른 자원의 `id`, LangGraph interrupt.id, SSE 프레임의 이벤트 순번 `id`는 그대로다. 이 변경은 인터페이스 정리이며 성능 향상을 주장하지 않는다.

## 검증

- 실제 PostgreSQL 및 SSO/SSE 핵심 회귀 **130 passed**, 106.10초. Public Run·SSO 쿠키·조회·사용자·CRUD 경계를 검증했다. 여러 차례 resume한 공개 ID, Location, SSE envelope/data/X-Run-Id 일치 및 구형 id 필드 부재를 확인했다.
- 위 다섯 모듈을 제외한 나머지 API 회귀 **441 passed**, 11 warnings, 272.11초. Worker·동시성·취소·checkpoint 복구·계획/수정 승인·Executor 이벤트·SSE를 포함한다. 합계 **571 passed**, 중복 실행 없이 두 묶음으로 수행했다. 경고는 checkpointer가 없는 graph의 기존 durability 안내다.
- 두 묶음 모두 localhost의 격리된 agentic_regression_test만 사용했다. LLM·Executor는 테스트 double이며 사내 SSO SDK도 double이다.
- JSONC 24개, 주석 필드 5,161개: 주석 제거 후 대응 JSON과 동일. Run 응답 예제·snapshot을 실제 Pydantic 모델로 검증했다.
- 이전 OpenAPI/payload schema와 비교해 검증 계약 변경이 PublicRunResource의 id → run_id 한 항목뿐임을 확인했다. 문서 재생성은 idempotent하다.
- 실행용 및 문서용 Workflow schema 원문을 유지했다. LLM 프롬프트·Agent 그래프·실행 동시성·DB 풀·Executor 제출 방식은 변경하지 않았다.
- 변경한 Python 파일의 AST 파싱을 확인했다. 실제 모델/Executor 외부 E2E 및 새 성능 측정은 수행하지 않았다.

## 다음 우선순위

[044 근거 답변 검증](044-agentic-answer-grounding.md)에서 확인한 후속 설명·리포트 작성의 불필요한 모델 호출과 큰 입력 문맥을 분석하고 줄인다. 이번 ID 통일로 해당 지연이 개선됐다고 판단하지 않는다. 전처리 데이터 등록 API는 Executor 후속 구현을 기다리며 별도다.

## 통합·게시

2026-10-01에 `5651121b908fcc99a4de1489b8149286e62d4505`까지 `feature/refactor-base`에 fast-forward 병합하고 베이스·`feature/public-run-id-contract`를 origin=KimHwaJin/prj_re에 atomic push했다. 두 원격 브랜치의 SHA 일치를 확인했다. 이 확인 기록은 베이스의 후속 문서 커밋으로 보존하며 최신 베이스 HEAD는 Git 원격 참조를 따른다. 원본 사용자 checkout, 기존 브랜치와 외부 서비스 설정은 유지했다. 서비스 배포·강제 push·기존 브랜치 삭제는 수행하지 않았다.
