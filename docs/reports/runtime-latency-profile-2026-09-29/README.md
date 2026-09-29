# 현재 실행 경로 지연 프로파일 — 2026-09-29

주 보고서: [report.html](report.html). 현재 소스 `ea871a1`, 1프로세스·4슬롯·LLM 호출당 5초, 1·10·30·50명 동시 시작 배치다. 이전 코드와의 비교가 아니다. 전체 흐름은 Workflow 승인 대기에서 종료했고 Executor는 호출하지 않았다.

## 원본과 정의

- `summary.json`: 겹치는 구간을 제거한 사용자별/단계별 시간 집계, 서비스 SQL·풀·이벤트 루프·공유 자원 정보.
- `flow-*/raw.json.gz`: 합성 테스트 원본. 홈 경로만 `<user-home>`로 치환했고 수치는 보존했다. SHA-256은 `evidence.json`에 있다.
- `verification.json`: 별도 검산 스크립트 결과. 91명/364개 실행 구간/364회 모델 호출과 원본 시간 일치 확인.
- `artifact.json`: 보고서 전체 텍스트·차트·표·출처·데이터. `report-datasets.sqlite`는 집계 결과를 렌더러가 요구하는 실제 SQL로 추출하기 위한 로컬 보고서 저장소다. 서비스 DB나 서비스 변경사항이 아니다.
- HTML 패키지 검증은 통과했다. 설치된 호환 Chromium을 찾지 못해 구조·원본 일치·fallback 검증만 완료했으며 실제 브라우저의 차트/다크모드/모바일/출처 모달 시각 검증은 미수행이다. 추가 브라우저는 설치하지 않았다.

전체 시간은 사용자 코루틴 시작부터 마지막 SSE 입력 대기 상태 관측까지다. 사용자 등록·프로젝트 설정·연결 준비는 제외하고 세션 생성은 포함한다. 각 resume 사이 think time은 0.2초다. DB `identity_test.public.agent_runs`의 created_at→started_at를 큐 대기로 사용한다. Worker 시간은 execute_claimed 경계, 모델 시간은 ChatOpenAI._agenerate 경계이며 HTTP wire 시간만 측정한 값이 아니다.

Worker 구간의 타임라인을 LLM → persistence.* → runtime.* → checkpoint.* 순으로 겹침 없이 나눈다. 나머지는 기타 내부 처리다. 체크포인트 비용은 배경 실행의 총합이나 모든 저장 시간과 동일하지 않다. 구간 밖 잔여 시간은 전체−Worker−큐로 정의하며, 접수/생성·사용자 생각 시간·SSE·작은 측정 경계 차이를 포함한다. DB 접수 시간은 큐 대기와 겹칠 수 있으므로 별도 더하지 않는다.

최종 커밋부터 클라이언트 관측까지의 수신 지연은 같은 머신의 perf_counter를 사용한다. 클라이언트 관측 시각은 POST 완료 시각−해당 POST 소요시간+단계 소요시간으로 복원하므로 함수 호출 경계 정도의 오차가 있다. 수신 지연은 전체 시간의 일부이고 독립 합산 항목이 아니다.

SQL 통계는 SQLAlchemy 서비스 엔진만 포함한다. checkpoint·bridge의 psycopg SQL은 포함하지 않는다. commit은 AsyncSession.commit 호출 횟수이며 실제 COMMIT 명령 또는 변경 트랜잭션 개수가 아니다. pool 획득 시간에는 연결 생성·ping 비용도 포함된다. 이벤트 루프 지연은 0.1초 sleep의 초과 시간으로 샘플링했다. 진단/계측 오버헤드와 localhost의 짧은 DB RTT를 고려해야 한다.

## 재현

repo의 `scripts/benchmarks/runtime_profile/run.py`를 사용한다. Python은 프로젝트 의존성이 설치된 가상환경을 사용한다. DTEST_BENCH_DATABASE_URL은 폐기 가능한 전용 **localhost identity_test** DB여야 한다. 스크립트는 매 조건 public schema를 삭제 후 재생성하므로 기존 개발 DB를 사용하면 안 된다.

```sh
python scripts/benchmarks/runtime_profile/run.py --matrix flow --users 1 10 30 50 --ref ea871a1 --output /tmp/runtime-profile-new
python scripts/benchmarks/runtime_profile/analyze.py /tmp/runtime-profile-new --output /tmp/summary.json
python scripts/benchmarks/runtime_profile/verify.py /tmp/runtime-profile-new /tmp/summary.json --output /tmp/verification.json
python scripts/benchmarks/runtime_profile/build_report.py /tmp/summary.json --output /tmp/artifact.json
```

원본 아카이브만으로 재집계할 때는 analyze.py/verify.py의 folder에 이 보고서 디렉토리를 넣으면 된다. verify.py는 이번 기준 버전 ea871a1의 322 SQL/61 commit 호출까지 대조하는 전용 검산이다. 후속 변경 버전의 의미 있는 쿼리 감소를 검증할 때 그 기대값을 무조건 재사용하면 안 된다.

서버 DB pool 10/overflow 0, checkpoint pool 1~4, bridge pool 4, Worker poll/cancel poll 0.25초, SSE 기본 coalescing 0.5초를 사용했다. Task reconciler와 Executor event worker는 껐고 LLM 재시도도 0이다. 로컬 HTTP mock은 기존 total_refactor/mock_llm.py를 재사용하며 프롬프트를 인식하지 못하면 422를 반환한다. `.env`를 읽지 않고 명시 설정만 사용한다. 실행 소스는 git archive로 고정한다. 출력 경로는 매번 새 경로를 사용한다.

각 배치는 새 프로세스로 실행하므로 그래프 최초 로딩이 한 번 포함된다. 서버는 일반 Uvicorn test wrapper에서 실행했고 모든 SSE 종료 후 SIGTERM으로 정리했다. 이를 운영 DrainServer의 종료 경합 검증으로 해석하지 않는다. 해당 기능은 기존 별도 테스트 범위다.

## 보고서 구성 및 검증 메모

기술 보고서 구조: 결론 → 측정 정의 → 전체 대기 구성 → 내부 시간 → 짧은 재개 → DB → 결과 수신 → 검증 방법 → 한계 → 다음 검토사항. 정의는 그래프 해석 전에 배치했다. 운영 질문은 마지막 단계에 통합했다.

차트는 네 사용자 조건의 이산 범주를 비교하는 두 개의 누적 막대다. 첫 차트는 전체시간의 분해, 둘째는 내부처리 확대이며 모두 0 기준이다. 데이터는 각 16행이고 범주별 이름/범례/정확한 수치 표를 함께 제공한다. 컬러는 shared renderer categorical palette를 사용하고 색상만으로 의미를 구분하지 않는다. 작은 차트에서 내부 비용이 묻히므로 별도 확대 차트를 둔다. 단계/DB는 정확한 값 비교를 위해 표로 제공했다. source SQL은 실제 로컬 보고서 저장소에서 실행된 추출 쿼리다.

이번 측정으로 확인되지 않은 것: 실제 LLM stream/token 경로 성능, 지속 유입, 운영 RTT, 여러 Pod, 실제 Executor, 취소·오류 경로, 생산 환경 SLA. 각 cohort 1회이므로 신뢰구간이나 인과적인 개선율을 제시하지 않는다.
