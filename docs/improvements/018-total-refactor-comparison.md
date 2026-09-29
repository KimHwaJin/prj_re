# 018. 최초 브랜치 대비 전체 변경 영향 비교

- 브랜치: `feature/benchmark-total-refactor`.
- 초기: 로컬 `feature/total_merge_v1` / `c751822`. 현재 애플리케이션: `4bb5c2f`.
- 017은 마지막 DB transaction 변경만 비교했으며, 전체 리팩토링 비교와 구분한다.
- 앱 코드를 바꾸지 않고 실제 API·그래프·PostgreSQL에 호출당 5초의 로컬 HTTP Mock LLM을 연결했다.
- 완료: 1·10·30·50명 × 초기/현재 8조건, 182개 사용자 흐름, 136,898 HTTP 요청. 전원 Workflow 승인 대기 도달, HTTP 오류 0. Executor 제출은 제외.
- 주 비교: 초기 한 프로세스/1자리, 현재 한 프로세스/4자리. 현재 기본값은 1. 동일 1자리 대조와 CRUD 단독은 미실행이므로 독립 기여율로 확대하지 않는다.
- 50명 평균: 1,038.04→252.80초. 모델은 양쪽 약20초, 큐 대기 1,014.30→229.05초. Worker 서비스 DB 점유/Run 5.26→0.21초. 그래프 생성 200→1, psycopg 풀 생성 400→2.
- 100명은 사용자 요청으로 중단했으며 부분 실행을 성공/실패 통계에 넣지 않았다. 하위 API/Mock 프로세스 정상 종료를 확인했고 전용 PostgreSQL 컨테이너를 제거했다.
- 원본 gzip SHA-256, 사용자/Run/모델 수·실행 한도 자동 검증과 독립 SQLite 평균/백분위 대조 8조건 통과. PNG 2개를 실제 열어 잘림·표현을 확인했다.
- 기존 전체 회귀 355 passed는 이전 구현 검증 기록이며 이번에 재실행한 것은 아니다. 실제 LLM·Executor·Redis·Gaia·Kubernetes·장기 실행 검증은 이번 범위 밖이다.
- 각 조건은 1배치이며 운영 최적 동시성·통계적 신뢰구간을 확정한 시험이 아니다. 원격 push·베이스 병합·기존 서비스 배포 없음.

[상세 결과](../reports/total-refactor-comparison-2026-09-29/report.md) · [변경별 문제/영향](../reports/total-refactor-comparison-2026-09-29/CHANGE-IMPACT.md) · [조건/재현](../reports/total-refactor-comparison-2026-09-29/METHOD.md).
