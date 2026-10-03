# 065 대형 출력·다단계/반복 repair 저장 비용

| 항목 | 내용 |
|---|---|
| 상태 | 측정·분석·회귀 완료 / runtime 변경 없음·베이스 미병합·미푸시 |
| 브랜치 | feature/checkpoint-growth-profile |
| 기준 | 27fbefd (064), 2026-10-04 KST |
| 범위 | 실제 PG 체크포인트 bytes/로컬 기능 비용, LLM·Executor double |

## 문제와 실제 작업

064에서 최신 후속 상태는 줄었지만 전체 누계가 늘었다. 큰 출력과 많은 Operation/repair에서 누적 원인이 어디인지, 어떤 channel을 개선해야 하는지 근거가 부족했다.

현재 production graph에 등록 Python fixture를 연결하고 actual serializer·checkpoint SQL을 계측하는 재현 도구 및 독립 검산을 추가했다. 13조건×3회 39회: raw stdout0~16MiB, 같은 20개 Tool의 1/5/20 Operation, 큰 미리보기와20 Operation 조합, repair0/1/5/10회. source/body/approval/receipt를 보존하고 mock ceiling만10으로 설정했다. 운영 코드·설정·API·배포는 그대로다.

## 결과와 영향

- raw output64KiB→16MiB의 누계는 약0.335MiB로 같았다. 툴별 text16,000자와 summary 한도가 동작한다. full file checksum 읽기 비용은 남는다.
- 같은20 Tool을1→20 Operation으로 나누면 CP19→153, 누계0.549→3.042MiB, 로컬 수행224→821ms다. review 정책·0초 역할·Python double I/O를 포함한 진단 시간이며 서비스 처리량 개선 수치가 아니다.
- 각 Tool의 큰 미리보기까지 남기면1.157→9.413MiB. observations 전체 목록을 매번 blob+write로 보존하는 비용이 약70%로 다음 개선 대상이다. 작은 output에서는 version/interrupt metadata 비중이 약77%다.
- repair10회는 CP99·2.321MiB. 정상 기본3회 설정은 변경하지 않았다. 성공load는1회, 최종sum6과실패관찰/수정이력은유지했다.

## 검증·한계와 다음

39회 모두 완료·중복 이벤트 재제출 없음·pool 반환, 새 pool 39개 완료 상태 decode, 독립 796개 검산, 관련 회귀 39 passed/skip0. 실제 HTTP/Worker/Redis/모델/Executor/Jupyter/장기 대기는 제외했으며 중간 wait의 큰 상태 graph 재개는 별도 후속이다. 원본/범위/정리/재현은 [상세 보고서](../reports/checkpoint-growth-2026-10-04/README.md).

다음은 observations의 delta 저장 별도 후보 A/B다. 공식 DeltaChannel은 설치되어 있지만 beta라 자동 적용하지 않았다. 증가분 write와 새 요청 reset, 기존 seed·HITL·Executor/repair 순서·중간 checkpoint·pool 종료 후 재개, 저장 감소와 읽기 시간의 교환을 함께 검증한 뒤 채택한다. metadata 임의 삭제·durability 완화·승인 원문 삭제·과거 pruning은 이번 작업에 포함하지 않는다.
