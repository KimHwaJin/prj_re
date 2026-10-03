# 058 배포·공통 설정 검증 근거

수행 대상은 feature/deployment-config-unification 작업 트리다. 실제 사내 배포가 아니다. 현재 사용자 환경과 다른 고유 Compose 프로젝트의 빈 PostgreSQL/Redis를 사용했다. 모델 mock, Executor 제출 false이며 사용자 Run의 전체 분석 플로우 성능을 측정하지 않았다.

- related-tests.txt: 관련 8개 모듈의105개 회귀 통과.
- manifest-schema.json: 공식 Kubernetes v1.34.1 OpenAPI definitions에 unknown field 금지를 추가한 strict 검사. 정본/CICD/Secret의8개 document 통과. CICD 고정 placeholder는 schema fixture로만 치환했으며 저장소 값은 유지했다. 실제 클러스터 적용이나 사내 API version/정책 검증은 아니다.
- shared-db-smoke.json: 이벤트 DB 미지정 → CRUD DB 파생, 실제 readiness/metrics/Redis 장애·복구/SSO 경계/idle SIGTERM 검증.
- split-db-smoke.json: 기존 EW_DATABASE_URL=agent override → 같은 단일 프로세스에서 기동·준비·종료 검증.

root Dockerfile 이미지 빌드와 frozen lock check도 성공했다. 사내 base 이미지/SDK 빌드는 수행하지 않았다. 로컬 kind 인증서가 만료되어 kubectl strict dry-run은 실패했고, 대신 [공식 Kubernetes v1.34.1 schema](https://github.com/kubernetes/kubernetes/blob/v1.34.1/api/openapi-spec/swagger.json)로 검증했다. TLS를 끄고 운영 배포한 것은 아니다.

재현:

```bash
docker build -t dtest-agent:config-unification-check .
# 프로젝트 Python 의존성 환경에서 실행. .env를 읽지 않고 기존 Compose는 조작하지 않는다.
python scripts/diagnostics/deployment_smoke.py --image dtest-agent:config-unification-check --output /tmp/shared-db-smoke.json
python scripts/diagnostics/deployment_smoke.py --image dtest-agent:config-unification-check --split-event-db --output /tmp/split-db-smoke.json
```

검증 도구는 고유 project 이름과 임의 localhost port를 사용하고 마지막에 자신이 만든 컨테이너/볼륨을 정리한다. stop 테스트는 **실행 중인 graph가 없는 idle 종료**다. 진행 중인 호출의 drain·강제 취소·SIGTERM은 related-tests의 기존 실제 subprocess 및 runtime 회귀가 담당한다. 실제 모델, Executor 실행, 장기 대기·복구와 Kubernetes HPA 결과로 확대 해석하지 않는다.
