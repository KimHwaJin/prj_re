# 073 결과 재개의 동일 트랜잭션 중복 조회 제거

상태: 구현·실제 SQL 원인 계측·관련 회귀·동일 조건 비교 완료 / 소폭 비용 절감·후보 유지 권장 / 베이스 미병합·미푸시·미배포.
브랜치 feature/result-resume-query-audit. 기준 55103856cbf960a3d263148cc2c429a6ef53c9f0은070 src와 동일(071/072 기록만 승계), 구현 2c79e5949c5f8d5761a83db8fb3bce439acc2f1a.

## 문제 → 실제 변경

prepare의 최신 FOR UPDATE 행을 refresh로 다시 읽었고, Executor 완료는 wait 검사에서 잠근 Run/Task를 finalize_state에서 다시 잠갔다. 실제1/10명 계측에서 같은 transaction·SQL·binding의 중복12회/흐름을 확인했다. prepare refresh2개 제거, 모듈 내부 _finalize_locked_state로 같은 transaction의 locked rows를 전달하여 결과당2개 제거했다. 소유권/lease·취소·wait/recovery·원자 상태/이벤트 저장은 유지한다.

권한 lock_session의 발견/잠금 후 재확인·User/Project 보호·메시지 dedup·새 invocation/receipt의 서비스 저장 복구는 필요하므로 유지한다. 모델/LLM·checkpoint·API·schema·pool·capacity·환경변수는 변경하지 않았다. --query-audit는 벤치마크 진단 옵션이다.

## 검증 → 효과

관련158회귀(skip0), 강화된 신규6경계 재검증 통과. 초기6 fixture 오류와 수정 검증을 보존했다. 진단4회22흐름에서 중복12→0, 권한/Message 조회 유지. 별도 전후18회424흐름 완료, 총22회446흐름·검산1,846개. 50명3회 평균 완료 16.458→16.226초(1.41% 감소), API CPU 15.784→15.576초(1.32% 감소). 50명 첫 trial 실제 내부 SELECT600회 감소·보호/INSERT 횟수 유지. SQL 계측/profiler는 속도 분모에서 제외하며 1/10/30과 보조는 단일 trial이다.

## 판단 → 제한 → 다음

확인된 중복 제거와 소폭 비용 절감으로 후보 유지를 권장한다. 유한 로컬3회 비교이므로 일반적 개선률·통계적 유의성·전체 병목 해결을 주장하지 않는다. [상세 원인·비교·원본](../reports/resume-query-2026-10-04/README.md)을 따른다. 다음은 모델 호출 수를 제외하고 CPU 프로파일의 SQL 실행 준비·ORM 결과 생성 비용을 구분해 더 큰 비용을 선택하는 검토다. 별도 runtime 후보는 이 단계 자체와 분리한다.

실제 Pod/HPA·지속 유입·원격 DB·멀티 replica·RSS는 미검증이다. 기존18서비스·checkout/.env 보존, 전용2컨테이너/volumes 정리. 모델/Registry/Workflow CRUD/운영·066/067/071/072 보류는 유지한다.
