# 021 목록·상태조회의 불필요한 데이터 로딩 제거

상태: 구현·집중/전체 회귀/패키지 검증 완료 / 베이스 미병합·미배포
브랜치: `feature/read-query-efficiency` · 출발 commit: `5279ee5`

## 문제와 실제 변경

**이 작업은 사용자 응답을 줄이지 않고, 응답을 만들 때 버리던 데이터를 읽지 않도록 변경한 것이다.** Agent 실행 슬롯·LLM 딜레이·상태 전이·폴링 주기·DB 풀은 바꾸지 않았다.

1. **프로젝트 상세조회**: 소유 Project 하나를 반환한다. HTTP 응답에 없는 전체 Session 목록과 Message count 집계를 삭제했다. 기존 자식 목록 API는 그대로다.
2. **세션 상세조회**: 소유 Session을 한 번 읽는다. 라우트→서비스의 중복 읽기와 전체 Message 로딩/Pydantic 변환을 제거했다. 메시지는 기존 페이지 조회 API에서 가져온다.
3. **Run 목록**: 커서 페이지에는 `run_id`, `created_at` 두 열만 읽는다. 선택된 공개 Run들의 최신 invocation·Task 상태를 한 번에 읽으므로 N+1 쿼리는 없다. 빈 페이지에는 추가 snapshot 쿼리를 실행하지 않는다.
4. **Run 상태조회**: 이전 invocation ID→공개 Run ID 해석을 snapshot의 scalar subquery에 합쳐 DB 왕복 한 번을 줄였다. 최신 Run·Task를 서로 다른 시점에 읽어 조합하지 않는다.
5. **공개 상태 projection**: root·latest·Task의 전체 ORM 행(90열) 대신 응답에 필요한 24열을 읽는다. 입력·명령·Executor 요청·Redis 결과·전체 metadata를 애플리케이션으로 보내지 않는다. root metadata에서는 checkpoint 참조 한 값만 추출한다. 내부 ORM 객체를 부분 refresh하지 않도록 scalar Bundle을 사용했다.
6. 사용처가 사라진 내부 집계 DTO `ProjectRead`, `SessionRead`, `SessionSummary`를 제거했다. 공개 `ProjectResource`, `SessionResource`, `PublicRunResource`는 유지한다.

`GET Run`, `/join`, SSE 내부 상태 읽기, 생성/resume/cancel 응답은 같은 읽기 함수를 사용하므로 개선된 projection이 적용된다. SSE event 쿼리와 polling 자체는 그대로다.

## 변경 전후 실제 PostgreSQL 계측

조건: 프로젝트 1개에 세션 20개, 대상 세션에 메시지 10개 또는 1,000개, 공개 Run 25개 × invocation 3개. 각 invocation의 비공개 JSON 5개에 각각 8KiB 문자열을 저장했다. 목록은 limit=10이다. 같은 fixture와 실제 HTTP 라우트를 사용했고 변경 전은 `5279ee5` 구현이다. 외부 LLM·Executor·Redis나 기존 로컬 서비스는 호출하지 않았다.

**아래 SQL 횟수에는 사용자 인증·소유권 조회가 포함된다. 반환 행은 드라이버가 애플리케이션에 반환한 행의 합이며 DB 내부 스캔/집계 입력 행 수가 아니다.**

| API/조건 | 이전 SELECT | 이후 SELECT | 이전 반환 행 | 이후 반환 행 |
|---|---:|---:|---:|---:|
| Project 상세, 자식 Session 20개 | 3 | 2 | 22 | 2 |
| Session 상세, Message 10개 | 4 | 2 | 13 | 2 |
| Session 상세, Message 1,000개 | 4 | 2 | 1,003 | 2 |
| Run 상세/과거 invocation URL | 4 | 3 | 4 | 3 |
| 공개 Run 목록, limit=10 | 4 | 4 | 23 | 23 |
| 빈 Run 목록 | 4 | 3 | 2 | 2 |

Run 목록의 23행은 인증 1 + 세션 1 + 다음 페이지 확인을 포함한 ID 11 + 상태 10이다. 행 수가 그대로여도 첫 페이지 조회가 **35열→2열**, 상태 snapshot이 **90열→24열**로 줄었다. Project의 집계 쿼리는 Message들을 읽고 count하지만 반환은 Session당 1행이므로, 표의 22행만 보고 이전 DB 내부 작업량을 해석하면 안 된다.

메시지가 늘어도 메타 조회가 모든 메시지를 전송/객체화하지 않는 것을 확인했다. **이 수치를 전체 사용자 플로우의 응답시간 개선율로 해석하지 않는다.** 물리적 읽기 바이트·DB buffer·TPS·p95는 측정하지 않았다. JSON 일부 추출에도 DB 내부에서 해당 JSON을 읽는 비용은 있을 수 있다.

[계측 요약](../reports/read-query-efficiency-2026-09-29/summary.json) · [이전 원본 SQL/10개](../reports/read-query-efficiency-2026-09-29/before-messages-10.json) · [이후 원본 SQL/10개](../reports/read-query-efficiency-2026-09-29/after-messages-10.json) · [이전 원본 SQL/1000개](../reports/read-query-efficiency-2026-09-29/before-messages-1000.json) · [이후 원본 SQL/1000개](../reports/read-query-efficiency-2026-09-29/after-messages-1000.json)

## 검증 결과

- 변경 전 계측: 2개 fixture 통과. 변경 후 같은 HTTP 응답의 필드·공개 ID·resume token·대기 상태를 검증했다.
- 최종 집중 검증: **103 passed, 65.29초**. 새 조회 테스트 13개 + 기존 공개 Run 수명 17개 + CRUD 보호 73개다.
- 전체 회귀: **459 passed, 2 subtests passed, 0 skipped, 43 warnings, 156.98초**. 기존 checkpointer 없는 그래프의 durability 경고다. 집중 103개는 전체 결과에 포함되므로 합산하지 않는다.
- 최종 wheel을 임시 경로에 빌드하고 `python -I`로 검증: 소스 checkout import 없음, OpenAPI 34 paths, 역할별 prompt 7개, mock graph 6 steps, production builder 구성 통과.
- 양방향 커서, 동일 timestamp에서 UUID tie-break, limit 1/7/200, 날짜 범위의 inclusive/exclusive 경계, 빈 페이지, 잘못된 cursor를 검증했다. 목록 크기가 달라도 SELECT는 4회다.
- 타 사용자/관리자의 비소유 자원, 없는 ID, 헤더 누락, soft delete 자원을 확인했다.
- checkpoint metadata 값이 없음/null/빈 문자열/false이면 기존 fallback을 유지하고 명시 참조도 보존한다.
- 같은 DB Session에 dirty ORM 객체가 있어도 상태조회가 이를 refresh로 덮어쓰지 않으며 별도 transaction의 최신 완료 결과를 읽는다. 상태 읽기가 입력을 수정하지 않는다.
- 기존 공개 Run 수명 검증으로 여러 resume, 멱등성, 이전 URL 별칭, Executor 대기/완료, 취소, 복구 필요, SSE를 확인했다. CRUD 접수/삭제 경합도 함께 회귀했다.

재현: 기존 전용 `identity_test` DB fixture를 사용한다. 이 fixture는 스키마를 초기화하므로 운영 DB를 지정하면 안 된다.

```sh
DTEST_IDENTITY_TEST_DATABASE_URL='<전용 로컬 identity_test URL>' \
DTEST_QUERY_REPORT_DIR='/tmp/query-after' PYTHONPATH=src \
python -m pytest src/app/test/test_read_queries_postgres.py -q
```

현재 테스트에는 개선 후 SQL 예산 assertion도 있으므로 구 코드 전체에 그대로 적용하면 실패한다. 변경 전 계측은 동일 sample/trace_reads/HTTP 요청으로 개선 예산 assertion 추가 전에 실행했다. 기존 원본을 지우거나 이후 결과로 덮어쓰지 않았다.

## 적용·한계·후속

- 새 migration·인덱스·환경변수 없음. 공개 HTTP 경로/필드/상태/커서 의미는 유지한다. 삭제한 내부 DTO와 변경한 `SessionService.read`의 Python 호출 시그니처는 외부 Python 소비자가 있다면 변경 대상이다. 레포 내 사용처는 모두 갱신했다.
- 큰 `interrupt`, `failure`, 완료 `result`는 응답 계약상 계속 읽는다. 상태 전용 응답 API 신설이나 polling 간격 변경은 이번 범위가 아니다.
- Session/Project 목록 자체는 기존 bounded pagination이다. message 순서를 sequence 기준으로 바꾸거나 legacy Run logs/Tasks 전체 조회를 페이지로 바꾸는 것은 공개 계약 정리와 함께 후속으로 남긴다.
- 여러 Pod/실제 네트워크 부하/운영 프론트 검증은 하지 않았다. 응답시간·처리량이 몇 배 좋아졌다고 주장하지 않는다.
- 020의 `5279ee5`까지 베이스에 통합했다. 021은 파생 브랜치에서 완료 후 기록하며 원격 push·베이스 병합·배포는 수행하지 않는다. 기존 checkout/서비스는 유지하고 검증 전용 PostgreSQL은 정리했다.
