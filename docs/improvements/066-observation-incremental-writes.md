# 066 observations 증가분 write 후보

| 항목 | 내용 |
|---|---|
| 상태 | 후보 구현·로컬 비교/호환 검증 완료 / 베이스 미병합·미푸시·미배포 |
| 브랜치 | feature/observation-delta-checkpoints |
| 기준 | 5ca22a5 (065), 2026-10-04 KST |
| 범위 | 실제 PG 저장량·graph 읽기/재개·표준 reducer/Delta5 비교 |

## 문제와 개선 방향

065에서 큰 미리보기/20 Operation은 observations blob+pending write가 누계의 약70%였다. 매 노드가 기존 목록까지 다시 쓰는 누적 비용을 줄이되 승인·본문·실패 근거·순서를 보존해야 한다.

새 Operation facts만 내부 tagged append로 쓰고 표준 LangGraph reducer가 전체 list를 구성하는 후보를 구현했다. full snapshot blob은 유지하며 reader/API 계약은 같다. plain list는 legacy 전체 교체, receive는 Overwrite([]) reset이다. 기존 seed에 구버전 누적 pending write가 있더라도 prefix를 중복하지 않는다. 공식 beta DeltaChannel5는 격리 진단 overlay로만 비교하고 runtime에는 넣지 않았다. 설정·스키마 migration·pool/durability/승인/receipt 정책을 변경하지 않았다.

## 결과와 영향

13조건×5회×3방식=195회. 큰 미리보기20 Operation의 누계는 기존9.405→표준6.430MiB(-31.6%), observations pending write는-90.5%다. 시간948.3→1000.6ms(+5.5%)이며 범위가 겹친다. 작은 미리보기20 Operation은 저장3.4% 감소/시간+0.7%, repair10은 저장1.7% 감소/시간+6.9%다. **처리량 개선이 입증된 결과가 아니다.**

Delta5는3.916MiB(-58.4%)지만 시간1200.1ms(+26.5%)와 ancestor-history 조회65회/trial이 생긴다. 저장 감소만 보고 기본 채택하지 않았다. metadata/원문/receipt 삭제·pruning·durability 완화는 포함하지 않았다.

## 검증·호환과 다음

Agent/패키지 회귀324 passed + 실제 PG13 passed, skip0. 구버전 plan/Executor/decision/repair wait와 checkpoint commit 실패 뒤 남은 seed3/pending4가 신규 복원4개인 상황을 확인했다. 195회 새 pool/graph 복원·wait 재진입·terminal 완료·동일 근거 SHA·중복 재제출 없음·후속 reset·과거 checkpoint 보존을 검증했다. 독립5231 검산/오류 대조4개. 시험용 컨테이너/파일은 정리했고 기존18개 서비스는 유지했다.

신규 tagged pending write를 구버전 LastValue worker가 이어받는 역방향 혼합 배포는 지원을 보장하지 않는다. 병합/배포 전에 작업 버전 경계를 확인해야 한다. 실제 HTTP/Worker/모델/Executor/PV 장기 대기·멀티 Pod 시험은 아니다.

후속 Worker 동일 부하 A/B는 [067](067-observation-worker-comparison.md)에서 완료했다. 대형50명 논리 저장30.6%/압축 column9.7% 감소지만 시간91.30→91.46초로 속도 개선은 없다. 올바른 계약의25시도 중24완료·1중단이며 첫 원인이 미확정이므로 후보 병합을 보류한다. 중단 원본과 성공 조건부 비교를 구분했다.

[상세 결과와 재현](../reports/observation-delta-2026-10-04/README.md), [개발자 저장 규약](../agent-development/analysis-state-lifecycle.md).

068 후속: [이벤트 복구·최초 오류 진단](068-executor-event-recovery-verification.md)에서 기본 이력 경로는 수정·검증했다. 계측한50명2회는 모두 완료했으나 과거 최초 원인은 미확정이다. 실제 PG에서 새 tagged pending write를 구 LastValue reader가 dict로 읽는 역방향 비호환을 확인했으므로 저장 후보 병합 보류를 유지한다.
