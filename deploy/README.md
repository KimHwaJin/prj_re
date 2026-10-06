# 단일 애플리케이션 배포

> 098 설정 규칙: config.yml은 로컬 전용이며 환경별 YAML과 병합하지 않습니다. 현재 파일 선택·키·초기화는 docs/application-configuration.md를 따릅니다.
정본은 app.py 한 컨테이너다. API·공통 Agent Worker·Executor 이벤트 수신·reconciler가 같은 lifespan에서 동작한다. 별도 Worker Deployment를 배포하지 않는다.

1. 실제 config.yml은 build context에서 제외하며 각 환경의 독립 파일은 [YAML 설정 안내](../docs/application-configuration.md)대로 준비한다.
2. secret.example.yaml의 config.prd.yml block에 실제 DB·Redis·모델·Executor·SSO 정보를 채워 Secret으로 제공한다. 예제 파일에 실제 credential을 커밋하지 않는다. Deployment는 이 키를 /app/config.prd.yml로 readonly 마운트하고 APP_ENV=prd만 selector로 받는다.
3. 같은 설정으로 scripts/migrate.py를 배포 사전 단계에서 직렬 실행한다. DB·계정·extension 준비와 이전 split DB 데이터 이행은 별도다. 활성 이벤트 DB는 API DB와 같아야 한다.
4. dtest-agent.yaml의 이미지·PVC·자원을 환경에 맞춘다. 사내 CICD 고정 placeholder는 플랫폼이 치환한다. cicd/basic/dev/config.dev.example.yml은 해당 Dockerfile이 config.dev.yml로 설치하며 고정 비밀 env는 계속 사용한다.
5. 기존 별도 Worker는 drain/종료 후 내장 Worker로 전환한다. readiness /service/ready, liveness /service/live와 종료 유예70초를 유지한다. Secret 내용이 바뀌면 프로세스 재시작이 필요하다. subPath 마운트에 실시간 반영을 기대하지 않는다.

[설정·배포 구조](../docs/deployment-configuration.md), [로컬 Compose](../docs/local-docker.md)를 따른다. 예제 Secret/Deployment는 이번 단계에서 실제 cluster에 적용하지 않았다.
