# 079 Run 요약 목록·상세 분리와 중복 join 제거

- 날짜: 2026-10-04
- 브랜치: `feature/run-read-contract`
- 기반: 078 `feature/session-availability` / `6d0e4cd`
- 상태: 구현·PG53·전체src681·wheel·문서 주석 검증 완료. 베이스 미병합·미푸시·미배포.

## 문제와 승인

Run 목록이 상세와 같은 PublicRunResource를 반환하고, 목록 SQL도 전체 Agent 결과·실패·interrupt 본문을 가져왔다. 최근 실행 목록만 필요한 요청에 큰 본문이 섞일 수 있었다. `/runs/{run_id}/join`은 완료를 기다리지 않고 단건 GET과 동일한 함수를 즉시 호출하는 별칭이었다.

사용자는 목록·상세 분리와 불필요한 join 삭제를 승인하고 로그의 필요성을 질문했다. 이번 구현은 승인한 두 변경만 진행했다. 로그 저장·조회는 유지하고 관리용 조회의 별도 정책은 미확정으로 기록했다.

## 구현

- 목록 GET은 Page[PublicRunSummary]를 반환한다. 공개 run_id/session_id, status, 모델 alias/버전, recovery_required, 생성/최종 변경/시작/완료 시각의10필드다. 결과·실패·HITL 본문/token·내부 실행 참조·시도·취소 세부 정보는 단건에서 읽는다.
- immutable SQL builder에서 root→최신 invocation→Task 결합을 공유하되 summary 전용 scalar projection은17columns만 읽는다. 큰 result/failure를 SELECT하지 않고 interrupt는 Executor 대기 boolean만 계산한다. 전체 metadata/checkpoint도 읽지 않는다. 목록은 기존 ID cursor page + 한 배치 summary 조회이며 N+1이 없다.
- 상세와 summary의 공통 header 계산을 공유해 status·시각·모델·복구 값을 같은 snapshot 규칙으로 만든다. 상세 GET·접수/재개/취소·SSE snapshot은 기존 PublicRunResource를 유지한다. writable ORM/사용자 상태를 캐시하지 않는다.
- 즉시 단건 조회와 중복인 join route/function을 삭제했다. 실제 코드 사용 검색은 해당 라우터·회귀 테스트와 문서에서만 확인했으며 외부 클라이언트 사용을 증명한 것은 아니다. 해당 경로는404이고 사용자가 있었다면 단건 GET으로 전환한다.
- 목록 token으로 resume하던 내부 회귀 클라이언트는 선택한 Run의 상세 GET으로 token을 가져오게 수정했다. 목록 pagination·정렬·날짜 필터·소유권·공개 ID는 유지한다.
- 주 문서, scoped OpenAPI(10paths), payload schema, 목록 JSON/JSONC 예제와 모든 필드 주석, wheel 검사도 갱신했다.

## 로그 검토

현재 AgentRunLogService는 run/event_key의 중복 여부와 저장 Log/TaskEvent 연결을 확인한다. 로그와 재전송 가능한 이벤트를 같은 짧은 transaction에서 저장하고 기존 로그에 대응 event가 없는 경우 보완한다. SSE 재생은 TaskEvent를 읽으며 로그 GET을 호출하지 않는다. 따라서 저장 계층을 삭제하는 것은 별도 재설계이고 이번에 삭제하지 않았다.

별도 `/logs` 조회는 일반 프론트의 중간 메시지·진행·HITL·최종 결과 표시에 필수가 아니다. 기존 Run SSE/상세 GET이 해당 역할을 수행한다. 관리 화면/개발 진단에서 어느 Agent·node·event와 payload가 기록됐는지 보는 용도는 있다. 권장 방향은 저장 유지, 일반 화면은 SSE, 조회는 진단용으로 페이지 상한·공개 필드·권한을 정리하는 것이다. 현재 logs는 기존 owner-scoped 전체 배열 응답 그대로이며 새 관리자 route나 pagination을 구현했다고 표시하지 않는다.

## 검증

- 격리 PostgreSQL17 localhost56076의 identity_test만 사용: **53 passed**. 9상태의 목록/상세10필드 일치, 100KB짜리 결과·실패·interrupt가 목록에 포함/조회되지 않음, 단건 전체 정보 유지, join404, limit1/7/200/양방향 cursor, HITL·멱등·구간 전체 SSE/로그·소유권·checkpoint 재시작·작은 DB pool의 연결 수명을 확인했다.
- 목록의 SELECT budget은 인증/소유권/ID 페이지/summary 배치의4회로 유지했다. 정상 상세3회, 세션2회 등 기존 예산 검사도 통과했다. 25개의 공개 Run·3개씩의 invocation에서 pagination 중복/누락이 없다. 이 결과는 DB CPU·물리 I/O·실제 사용자 시간 개선의 측정이 아니다.
- 최종 전체 src: **681 passed, 441 skipped, 74 warnings**. opt-in PG53은 별도 실행이며 합산하지 않는다. 기존 no-checkpointer durability warnings는 유지했다. 실제 SSO 인증은 기존 identity double이며 실제 LLM·Redis·Executor를 호출하지 않았다.
- 깨끗한 staging wheel/isolated Python 검증 통과: current36paths, PublicRunSummary10필드·목록 schema 연결, join 없음, 기존 세션 availability/settings·리소스·역할5개 조립, checkout 미참조.
- 주석 생성기: **25files / 5,452주석 / 6inline blocks**, JSON/JSONC 동등성 통과. OpenAPI/schema의 이번 명세 변경은 생성 단계에 반영했고 이후 주석 단계는 검증 규칙을 변경하지 않았다.
- git diff --check 통과. 새 DB migration·환경변수·의존성 없음. 부하/처리량 전후 측정은 하지 않았다.

초기 검사 DB의 고정55555포트가 사용 중이라 시작이 실패했다. 기존 포트 사용자를 변경하지 않고 이번 컨테이너에 임의의 loopback 포트를 배정해56076에서 검사했다. 테스트가 끝난 뒤 해당 일회용 컨테이너와 임시 스크립트를 제거했다. 기존 앱/Executor/Redis/DB 컨테이너와 원래 checkout의 사용자 변경, .env는 변경하지 않았다.

## 적용 시 확인

목록에서 result/interrupt/token을 읽던 외부 클라이언트는 단건 GET을 추가하고 join 사용처도 바꿔야 한다. 입력/재개/SSE 규격은 동일하다. 관리자 로그 권한·보존/정리·레거시 로그 GET 상한은 후속 합의 항목이다. 일반 목록 개편과 로그 저장 계층 삭제를 같은 작업으로 다루지 않는다.
