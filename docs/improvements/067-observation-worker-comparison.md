# 067 observations 증가분 write의 Worker A/B

| 항목 | 내용 |
|---|---|
| 상태 | 25시도·24완료/중단1·독립 검산/보고서 완료 / 최초 중단 원인 미확정·후보 병합 보류 |
| 브랜치 | feature/observation-worker-comparison |
| 기준 | 5ca22a5(065) → 63b2b6b(066), 2026-10-04 KST |
| 범위 | service-only HTTP/Worker/PG/Streams/SSE, 1·10·30·50명, 0초 모델 |

## 문제와 실제 작업

066에서 저장량만 줄었고 실제 Worker 처리량 영향은 미측정이었다. 기존 누적 writer와 표준 reducer 후보를 같은 실행자리20·풀·폴링·mock에서 비교했다. 기본4 Tool/2 Operation과 큰 미리보기20 Tool/20 Operation을 지원하는 별도 harness·manifest/checksum·원본/소스/집계·독립 검산·portable 보고서를 추가했다. 역할별 모델4/23콜은 그대로며 실제 SDK/middleware 경로를 사용한다. 운영 runtime/config/API/durability/승인/receipt를 새로 바꾸지 않았다.

첫 fixture 이력 contract/prefix가 잘못된 비교는 전체 제외 후 실제 items/next_cursor/has_more·API base/path를 맞췄다. 올바른 계약의 후보50명 중단은 유효 시도의 실패 사건으로 보존했다. 원래 예외 사슬 출력은 diagnostic harness에만 추가했고 재시험 성공으로 사건을 지우지 않았다.

## 결과와 판단

완료한50명 각3회 조건부 평균: 기본20.766→20.772초(+0.03%), 대형91.302→91.462초(+0.18%). 저장량 대형9.787→6.793MiB(-30.6%), observations write3.309→0.316MiB(-90.5%), 압축 column1.368→1.235MiB(-9.7%). checkpoint156회와 전체 snapshot/읽기·노드/모델/재개 횟수는 유지한다. 저장 함수 누계30.44→30.72초/사용자도 줄지 않았다. **저장 효율을 처리량 개선으로 발표하지 않는다.** 작은 부하의 단회 시간 차이는 상세 표에 공개했다.

올바른 계약의25시도/814시나리오 중24완료/764시나리오, 후보 대형50명1중단. Operation16 완료 처리 중 첫 ExecutorOutcomeUnknown 뒤20명령RECOVERY·43READY로 새 claim을 멈췄다. 첫 예외 사슬은 없어 최초 원인·reducer와의 인과는 미확정이다. 원인 출력만 추가한 후 기존/후보 대형50명 각3회는 완료했다. 조건부 성공 시간에 실패 비용을 숨기지 않으며 **현재 베이스 병합 보류**다.

## 검증·제한·다음

실제 src SHA/Git 정본/변경 runtime4파일, 24cohort·최종 관찰 hash·명령/이벤트 고유성·owner/queue/checkout0을 대조했다. 독립 수학/SHA 검산·21회귀(skip0, 해당 없는6조건 deselected)·오류 주입7종 통과다. 잘못된 초기12시도와 준비smoke2는 별도 보존했다. HTML은 native grouped bar/표의 canonical reader로 생성·구조검증 완료, 브라우저 UI QA는 headless-shell 부재로 미수행이다.

0초 모델·합성 Executor 결과·단일process·유한 burst이며 real LLM/PVC/Jupyter/HPA/CPU quota/장기 대기 검증이 아니다. 압축 column 합은 전체 디스크/WAL이 아니다. 원래18개 서비스/checkout/.env를 유지했고 임시 DB/Redis/프로세스/컨테이너를 정리했다. 미푸시·미배포다.

다음은 첫 예외와 Executor POST 결과를 같은 조건에서 확보하는 좁은 조사다. 새 writer의 역방향 pending write 혼합 버전 경계도 남는다. 이후 성능은 API CPU 구성요소 sampling으로 살펴보며 모델 호출 횟수 최적화 보류는 유지한다. 운영 default Executor root base/상대 history prefix 정합성도 이번 mock 설정과 별개인 연계 후속이다.

[상세 결과/재현](../reports/observation-worker-2026-10-04/README.md), [읽기용 보고서](../reports/observation-worker-2026-10-04/report.html).

068 후속: [이벤트 복구·최초 오류 진단](068-executor-event-recovery-verification.md)에서 기본 이력 경로는 수정·검증했다. 계측한50명2회는 모두 완료했으나 과거 최초 원인은 미확정이다. 실제 PG에서 새 tagged pending write를 구 LastValue reader가 dict로 읽는 역방향 비호환을 확인했으므로 저장 후보 병합 보류를 유지한다.
