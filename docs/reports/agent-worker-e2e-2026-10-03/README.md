# 공통 Agent Worker 성능 비교 근거

[HTML 보고서](report.html)가 주 결과다. [062 개선 기록](../../improvements/062-agent-worker-e2e-performance.md)과 [재현 절차](../../../scripts/benchmarks/worker_e2e/README.md)를 함께 참고한다.

- `results.json`: 59개 trial의 검증된 지표. `summary.json`: 기본 비교 32개 코호트 집계. `supplemental.json`: 메모리·후속 요청/notify 대조.
- `aggregation.sql`: 실제 실행한 SQLite 집계. 50명 표본 표준편차는 각 trial 평균 3개의 편차다.
- `raw/*.json.gz`, `manifest.json`: 원문·원문/압축 SHA·runtime 소스 hash. 시나리오별 시작/종료 경계는 재현 안내를 따른다.
- `verification.json`, `independent-verification.json`, `capture-validation-tests.txt`: 완료 검증, analyzer를 import하지 않은 독립 산술 검산, 보존 capture의 음성 테스트 11개.
- `hold-validation.json`: 결과 해제 전 24 trial·72 표본. 실행 자리 0, trial마다 CRUD checkout 0 표본이 있고 순간 1~2개의 연결 사용 표본 3개도 보존한다.
- `environment.json`, `source-audit.json`, `fixture-source-sha256.json`: 실제 Python/라이브러리/PG/Redis/Docker 환경과 source/dependency 정합성.
- `measurement-controller.py`, `controller.txt`: 당시 제어 코드·실행 출력 원문. 재실행은 경로를 인자로 받는 `scripts/benchmarks/worker_e2e/matrix.py`를 사용한다.
- `trial-cleanup.json`, `preparation-cleanup.json`, `final-cleanup.json`: 정상 trial과 준비 단계 smoke의 정리 근거. 기존 서비스 자원을 변경하지 않았다.
- `artifact.json`, `source-notes.json`, `narrative.md`: canonical 보고서 데이터·차트/검증 정의·본문. HTML을 직접 수정하지 않는다.
- `portable-delivery-receipt.json`, `browser-qa-attempt.json`: payload/구조·semantic 표 검증은 통과했다. 설치 Chrome의 자동 화면 검증은 시간 초과로 미완료다. 정적 chart 표를 포함한 `structural_only` 결과로 전달하며 실제 화면 QA를 완료했다고 주장하지 않는다.

59 trial·1,722 사용자 시나리오, Agent Python 파일 122개 동일, 총한도 20, 모든 측정 모델콜 5초. 코드 실행/분석 출력은 HTTP fixture이고 실제 Executor/Jupyter/PVC/MinIO/모델/Sso 직원 검증은 시험하지 않았다. 별도 Artifact POST 등록·Workflow pgvector 검색·Task reconciler도 범위 밖이다. 057의 계획만 5초 fixture와 절대 시간을 직접 비교하지 않는다.
