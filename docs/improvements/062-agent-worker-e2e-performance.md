# 062 공통 Worker 전체 시나리오 기능·성능 대조

| 항목 | 내용 |
|---|---|
| 상태 | 로컬 fixture 검증·독립 검산 완료 / 베이스 미병합·미배포 |
| 측정 기간 | 2026-10-03~04 (KST) |
| 브랜치 | feature/agent-worker-e2e-performance |
| 기준 | 이전 faca5b1 (059 완료) / 현재 runtime 32f443e (060·061 반영) |
| 근거 | 리뷰 구현 계획 5단계 / D-09 |

## 기존 문제와 검증 방향

060의 8건 준비 queue 비교와 061의 6초 유휴 조회만으로는 전체 사용자 흐름의 성능을 판단할 수 없었다. 기존 057은 최초 계획만 5초인 fixture였으므로 그 절대 시간을 새 비교에 섞지 않았다. 통합 직전 사용자 16자리+결과 4자리와 현재 공통 20자리를 같은 **총한도 20**에서 비교했다. 전체 리팩토링 시작 브랜치와의 비교는 아니다.

## 실제 변경

`scripts/benchmarks/worker_e2e`에 테스트 전용 HTTP runner·모델 전송 fixture·계측 hook·집계/독립 검산·재현 도구를 추가했다. 운영 Agent·Worker·API·prompt·호출 수·pool 기본값·배포 설정은 바꾸지 않았다.

기본 네 시나리오는 1/10/30/50명이고 50명은 각 3회, 두 번째 반복에서는 순서를 뒤집었다. 후속 설명 두 개와 프로젝트 메모리 manual/auto_context 8회, 현재 notify OFF 3회를 추가해 **59 trial·1,722 사용자 시나리오**를 완료했다. 측정 내 모든 모델 호출은 각각 5초이며 정상 전체 분석은 4콜/20초다.

실제 API·SSO cookie/CSRF·CRUD·create_agent/middleware·checkpoint·PostgreSQL Store·Executor HTTP submit/continue/finalize·manifest/checksum·Streams/Inbox·SSE를 거쳤다. 직원 검증 verdict·모델 응답·Executor 코드 실행/출력은 fixture다. 리포트 완료는 Agent Markdown 응답과 ready 상태이며 별도 Artifact POST 등록은 포함하지 않는다. Workflow 저장·pgvector 검색과 Task reconciler는 비활성 동일 조건이다.

## 사용자 흐름과 결과

전체 분석은 프로젝트/세션 생성부터 계획 편집·승인, Executor 결과 재개, 최종 success SSE까지다. 로그인/등록/warm-up과 사용자 생각 시간은 제외했다.

| 사용자 | 기존 평균 초 | 현재 평균 초 | 평균 단축 | 각 조건 반복 |
|---:|---:|---:|---:|---:|
| 1 | 22.71 | 22.23 | 2.1% | 1 |
| 10 | 35.10 | 24.62 | 29.9% | 1 |
| 30 | 78.02 | 34.06 | 56.3% | 1 |
| 50 | 141.27 | 55.46 | 60.7% | 3 |

50명에서 전체 batch 종료는 **172.73→62.01초**였다. 준비된 결과만 일제 도착한 경우 평균은 103.72→25.33초, 결과 25명+새 분석 25명 혼합은 105.06→37.08초, 승인 대기는 36.58→27.68초였다. 준비 결과 시나리오는 계획 준비를 제외하므로 전체 분석과 절대 시간을 섞지 않는다.

주된 효과는 비어 있는 사용자/결과 실행 자리를 다른 종류의 명령이 사용하는 것이다. 50명 전체 분석의 XADD→결과 handler 시작 대기는 30.22→4.09초였다. Agent Python 소스 122개 hash와 dependency 파일은 동일했다. 1명에서 변화가 작은 것은 모델 대기 20초가 그대로 있기 때문이다.

## 비용과 제한

50명 전체 분석의 사용자당 API CPU는 0.629→0.487초, CRUD SQL은 632.97→610.88회였다. 풀 획득 p95는 30.28→53.84ms로 늘었다. 승인 대기 등에서는 새 원장 때문에 SQL이 늘 수 있다. API CPU/CRUD asyncpg SQL만 계측했고 checkpoint·Store·Event의 psycopg SQL과 DB/Redis CPU는 포함하지 않았다. 진단 hook 비용도 API CPU에 포함된다.

현재 50명 승인 대기 notify ON/OFF는 평균 27.68/27.61초, 빈 claim 평균 37.33/58회였다. 알림의 확인된 효과는 빈 조회 감소이며, 이번 포화 시험에서 추가 시간 단축을 주장하지 않는다.

24개 trial의 Executor 대기 표본 72개에서 실행 자리는 모두 0이고 각 trial에서 CRUD checkout 0이 관측됐다. 표본 3개는 순간 1~2개 연결 사용이 있었고 이후 0으로 돌아왔다. 모든 표본의 연결 0을 요구하던 집계 판정을 이 관측 정의에 맞춰 보완했다. 해당 순간의 쿼리 owner는 계측하지 않아 확정하지 않는다. 원문을 제외하거나 시험을 다시 수행하지 않았다.

단일 로컬 프로세스·유한 일제 유입·별도 모델 호출 한도 없음의 결과다. 실제 Pod CPU quota/HPA·멀티 Pod·지속 유입·장기 원장/1주 Executor·실제 모델/사내 SSO/PVC/MinIO/Jupyter는 검증 밖이다. 현재 20을 최적 운영 한도로 확정하지 않는다.

## 검증과 정리

- 59 trial 전부 성공. HTTP 오류·누락/중복 성공·한도 초과·결과 Defer 0.
- 모든 역할별 호출 수·설정 5초·Run/Command/Event 고유성·continue/finalize·관찰 4개·report ready·종료 owner/recovery/Inbox/Outbox/CRUD checkout 확인.
- gzip 원문/SHA, 평균·p95·반복 편차·처리율·SQL/사용자 독립 검산 및 전체 비교 조건 누락/중복 검사 통과.
- 보존 원문으로 검증의 음성 테스트 **11 passed**.
- HTML canonical payload·package·구조/semantic 표 검증 통과. 설치 Chrome의 packaged 화면 검증은 시간 초과로 미완료(`structural_only`). 브라우저를 다운로드하거나 별도 HTML renderer를 만들지 않았다.
- 준비 단계 설정 충돌 smoke 두 개는 성능 모집단에서 제외하고 진단/정리 내역을 남겼다. 시험 전용 DB·Redis 키·자식 프로세스·private config 0을 확인한 뒤 이번 전용 컨테이너 두 개만 제거했다. 기존 서비스는 변경하지 않았다.

[HTML 보고서](../reports/agent-worker-e2e-2026-10-03/report.html), [근거 안내](../reports/agent-worker-e2e-2026-10-03/README.md), [재현 절차](../../scripts/benchmarks/worker_e2e/README.md).

## 다음 작업

리뷰 구현 계획 6단계: 대표 흐름의 checkpoint_blobs/checkpoints/checkpoint_writes를 thread/channel/구간별로 실제 측정한다. 큰 저장 비용을 확인한 뒤 해당 항목만 최적화한다. 모델 콜 수·prompt, Dataset Registry 실연계, Workflow CRUD, 광범위 운영 보완의 보류는 유지한다.
