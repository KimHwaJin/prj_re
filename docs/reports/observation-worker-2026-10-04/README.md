# Observations 증가분 write의 Worker 부하 검토

**판단: 저장 효율은 개선됐지만 처리 시간은 개선되지 않았다. 올바른 fixture 계약의 후보에서 중단 1건이 발생했고 최초 원인은 미확정이므로 베이스 병합은 보류한다.**

[읽기용 상세 보고서](report.html) · [채택 판단](decision.json) · [독립 검산](validation.json) · [전체 시도](attempts.json)

## 범위와 시간 결과

기준5ca22a5(065)와 후보63b2b6b(066)를 비교했다. 최초 리팩토링 이전 total_merge_v1과의 비교가 아니다. 1·10·30·50명, 기본4 Tool/2 Operation과 대형20 Tool/20 Operation 두 흐름이다. 50명 완료 trial은 각3회, 나머지 각1회다. **아래 시간은 완료 trial의 조건부 평균이다. 중단의 손실을 포함한 전체 처리량으로 해석하지 않는다.**

| 사용자 | 기본 기존→후보(초) | 대형 기존→후보(초) | 반복/방식·시나리오 |
|---:|---:|---:|---:|
| 1 | 1.72 → 1.76 | 4.18 → 4.20 | 1 |
| 10 | 3.85 → 4.27 | 16.76 → 16.51 | 1 |
| 30 | 12.08 → 12.47 | 53.82 → 54.30 | 1 |
| 50 | 20.77 → 20.77 | 91.30 → 91.46 | 3 |

전체 경계는 프로젝트/세션 생성→새 Run→계획 편집→승인→HTTP Executor 제출/continue/finalize→Redis Streams→공통 Worker 재개→최종 리포트/SSE다. 로그인·등록·warmup·기동/종료·행 캡처는 제외했다. 별도 보고서 Artifact POST/노트북 셀 등록은 포함하지 않는다.

**모든 모델 transport 지연0초**·실제 create_agent/스킬 조회/middleware다. 기본 모델4콜, 대형23콜을 유지했다. Executor는 HTTP/이벤트/manifest 파일 fixture이며 제출 Python을 실행하지 않는다. PostgreSQL/Redis/API/권한/Worker/checkpoint/Store/checksum/SSE는 실제 경로다.

API1 process·공통자리20·CRUD10/overflow0·checkpoint4·bridge4·Store2, SSE0.5초·claim/cancel0.25초·Ingress0.2/idle2초·notify ON을 맞췄다. 독립 유저/프로젝트/세션의 유한 동시 유입이며 같은 프로젝트 메모리 경합·지속 유입·CPU quota/HPA/멀티 Pod·실 LLM/Jupyter/PVC/MinIO·1주 실행은 측정하지 않았다. 기존5초 모델 측정과 절대 시간을 합치지 않는다.

## 저장량과 성능 효과 분리

| 대형50명/사용자 | 기존 | 후보 | 변화 |
|---|---:|---:|---:|
| 논리 저장량 | 9.787MiB | 6.793MiB | -30.6% |
| observations pending write | 3.309MiB | 0.316MiB | -90.5% |
| 압축 column 크기 합 | 1.368MiB | 1.235MiB | -9.7% |
| 저장 함수 누계 | 30443.653ms | 30724.870ms | +0.9% |
| 읽기 함수 누계 | 7064.977ms | 7252.001ms | +2.6% |
| checkpoint 수 | 156 | 156 | 0% |

논리량은 JSON text/고유 blob/retained writes, column은 pg_column_size 합이다. column 합을 heap/index/WAL/TOAST 전체 디스크로 해석하지 않는다. 로그는64KiB 반복 문자이고 reader 미리보기는16000자여서 실제 로그보다 압축이 잘 된다. 원본/승인/receipt/본문을 삭제하지 않았고 full snapshot·읽기·노드/모델/재개 횟수는 그대로다. 함수 누계는 동시 작업의 겹치는 시간이며 E2E에서 빼거나 합산하지 않는다. CRUD SQL 변화는 폴링/진행시간 변동이며 이번 SQL 최적화 성과가 아니다.

대형50명 API CPU 누계는 기존90.73초/측정92.80초, 후보90.95초/측정92.93초다. 두 방식 모두 약98% 한코어 환산이며 전체 호스트 CPU 사용률이 아니다. 계측 wrapper/메모리 counter 비용을 포함하며 운영 CPU 점유율의 확정값이 아니다. CPU의 어떤 함수가 차지했는지는 이번 call-clock 계측으로 확정하지 않았으며 다음 sampling profile 후보로 남긴다.

## 중단 사건 — 성공 재시험으로 삭제하지 않는다

올바른 계약의 총25시도/814시나리오 중24시도/764시나리오 완료, 후보의 대형50명1시도 중단이다. 첫 실패 handler는 Operation16 완료 이벤트 처리 중 ExecutorOutcomeUnknown이었다. 관측 당시20개 명령RECOVERY·43개READY, Worker active0, 새 claim 중단이었다. 검증 실패 때문에 종료한 것이 아니라 이미 멈춘 Worker의 메모리/DB/이벤트 증거를 확보한 뒤 전용 runner를 SIGINT하여 정리했다. 시간 초과의 성공 분포를 임의로 만들지 않았다.

후속 기존/후보 대형50명은 각3회 완료했다. 재현에서는 benchmark exception chain 출력만 추가했고 성공 경로의 설정/소스/슬롯/풀/행위는 유지했다. 첫 사건의 원래 예외 사슬/POST 상태가 없어서 HTTP 전송/응답·요청 생성·후속 저장 어디가 최초 원인인지 미확정이다. reducer 변경 때문이라고도, fixture 해프닝이라고도 확정하지 않는다. [중단 원본](incidents/candidate-large20-u50-r1-failure-live.json.gz), [증거 SHA](incident-index.json)를 보존한다.

후보는 저장 목적의 효과만 확인했다. 처리량 향상으로 병합할 근거가 없고 첫 사건 원인·역방향 pending write 혼합 버전 경계 확인이 필요하므로 보류한다. 승인/소유권/idempotency/durability를 약화해 통과시키지 않는다.

## 잘못된 첫 시험과 실제 연계 후속

처음12시도/164시나리오의 matrix는 fixture 이력(events/last_sequence)과 Worker(items/has_more), root base+상대 events 경로의 mismatch를 발견해 **세트 전체 제외**했다. 유효 모집단25시도와 구분한다. 준비 smoke2회도 제외했다. [제외 사유](excluded-attempt.json), [68개 원본·로그 SHA](excluded-index.json)를 보존했다.

실제 Executor의 GET은 /api/v1/executions/{id}/events, 응답은 items/next_cursor/has_more다. 이번 양쪽 시험은 기존 설정을 사용해 base=/api/v1, Agent paths=/executions...로 명시했다. 운영 기본 root base+Worker /executions... 조립의 prefix는 그대로 별도 후속 문제다. production 코드의 URL 조립을 이 작업에서 고치지 않았으며 테스트 설정만으로 운영 문제가 해결됐다고 말하지 않는다.

## 검증과 전달

24개 완료 cohort/50명 반복/모델 역할·호출 수/Command·Run·Event 고유성·owner/queue/checkout0과 실제 source Git 정본을 대조했다. runtime 변경은4개 파일뿐이다. 독립 검산은 평균/p95/SQL/바이트/호출/최종 근거 hash/시도/증거 SHA를 다시 계산했다. [21개 회귀](regression.log)(6개 해당 없는 hold/lock 보조 조건 deselected, skip0), [7종 오류 주입](negative-controls.json)을 통과했다. 첫 jsonschema 미설치와 검산 테스트의 추가 메타데이터 누락은 시험 구현을 정정했고 실패 로그도 보존했다.

Portable renderer의 validation/package/structural verification은 통과했지만 Chromium headless-shell이 없어서 브라우저 화면/상호작용 QA는 미수행이다. HTML을 임의 renderer로 대체하거나 브라우저를 내려받지 않았다. 표에는 같은 수치를 모두 제공한다. [전달 receipt](report-delivery.log), [artifact](artifact.json), [집계 SQL](aggregation.sql), [원본 목록](raw-index.json), [소스 audit](source-audit.json), [계측 revision/의존성](harness-audit.json).

임시 DB/Redis/프로세스/컨테이너를 정리했고 기존18개 서비스를 유지했다. 기존 checkout/.env/실 Executor와 모델을 수정하지 않았다. [정리 증거](cleanup.json). 베이스 미병합·미푸시·미배포다.

## 재현

동일 Git 정본을 git archive로 별도 source root에 두고 [benchmark 가이드](../../../scripts/benchmarks/worker_e2e/README.md)의 dedicated loopback PG63372/Redis63373·새 출력 폴더를 사용한다. 실제 DB/group을 지정하지 않는다. 전체 command와 fixture/harness SHA는 [실행 receipt](runs.json)에 남겼다. 여기 process_seconds는 기동/캡처/종료를 포함하므로 사용자 시간을 대신하지 않는다.

이번 export는 원래 성공/중단 receipt, 원인 출력 재현2개, 남은 반복8개를 --supplement로 모았다. 모든 시도를 attempts.json에 보존하고 성공 시간 그래프의 조건부 성격을 명시한다. 일반 신규 시험은 한 matrix만 실행해도 된다. 모델 호출 수/prompt 최적화·Dataset Registry/Workflow CRUD·광범위 운영 보완의 보류는 유지한다.
