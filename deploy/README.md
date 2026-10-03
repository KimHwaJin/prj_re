# 단일 애플리케이션 배포

정본은 `app.py`로 실행하는 한 컨테이너다. FastAPI, Run Worker, Executor 이벤트 수신/재개, reconciler가 같은 프로세스와 lifespan에서 동작한다. 별도 Worker Deployment는 사용하지 않는다.

1. 배포 전에 CRUD·이벤트·checkpoint schema를 서버와 같은 설정으로 준비한다. init container나 자동 reset은 없다.
2. `dtest-agent.yaml`의 이미지·PVC·자원은 환경에 맞춘다. 사내 CICD의 고정 placeholder는 플랫폼이 치환한다.
3. Secret 예제의 공통 DB·Redis·Executor·모델 값을 주입한다. 기존 분리 DB는 `EW_DATABASE_URL`로 보존한다.
4. 기존 별도 Worker가 있으면 drain/종료 후 내장 Worker로 전환한다.
5. readiness `/service/ready`, liveness `/service/live`, 종료 유예70초를 유지한다.

[공통 설정·포트·schema 준비·전환 절차](../docs/deployment-configuration.md), [로컬 환경](../docs/local-docker.md), [058 검증 기록](../docs/improvements/058-deployment-config-unification.md)을 따른다.

```bash
docker build -t dtest-agent:local .
docker compose -f compose.external.yaml up
```

`kubectl apply -f deploy/dtest-agent.yaml`은 schema·Secret·PVC 준비와 기존 Worker 전환 완료 뒤 실행한다. 이번 변경은 실제 사내 배포를 수행하지 않았다.
