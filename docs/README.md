# 문서의 정본과 이력

현재 서비스 구현은 `src/dtest`다. 문서가 다르면 현재 실행 코드·OpenAPI와 아래
정본을 대조한다. 특정 날짜의 설계·측정·개선 기록은 현재 사용 안내와 구분한다.

| 주제 | 현재 정본 |
| --- | --- |
| 패키지 책임·조립 | [서비스 구조](architecture/service-layout.md) |
| YAML 선택·키·우선순위 | [앱 설정](application-configuration.md) |
| 시작·DB·종료 | [기동](configuration-bootstrap.md), [migration](database_migrations.md) |
| 배포·로컬 Docker | [배포](deployment-configuration.md), [Compose](local-docker.md) |
| SSO·사용자 | [SSO](sso-authentication.md), [사용자](user-identity-api.md) |
| Run·HITL·SSE | [공개 API](public-run-api.md) |
| Workflow 공개2.0·등록/추천 | [표준](workflow-standard.md), [검색](workflow-registration-and-search.md) |
| 내부 실행 계획 | [JSON 설명](workflow-json-reference.md) |
| Agent·Skill·Tool 개발 | [개발 안내](agent-development/README.md) |
| 성능 검증 도구 | [부하 검증](service-loadtest.md) |
| 테스트 범위 | [회귀 안내](../tests/README.md) |

`improvements/`는 변경 이유·검증·제한·통합 기록이다. `reviews/`는 당시 리뷰와
의사결정이며, `reports/`는 당시 commit·설정에서 얻은 측정 원본이다. 옛 소스 경로와
명령은 그 commit에서 확인한다. 이력을 현재 상태로 소급해 고치지 않는다.

`design/`의 완료된 설계 초안은 구현 계약보다 우선하지 않는다.
Dataset 등록·조회는 외부 구현 협의 중인 [계약 초안](design/dataset-registry-contract/README.md)이다.
실제 API가 이미 존재하는 것으로 사용하지 않는다.

Workflow의 [원본 초안](contracts/workflow-standard/original-1.0/workflow_spec_1.0.md),
[변경 추적](review/workflow-standard-changes.md)과 공개 확정 문서는 별도로 보존한다.
초안·내부 계획2.0-draft·공개 Workflow2.0을 동일 schema로 취급하지 않는다.

폐기된 completion/data-selection 전용 안내와 옛 API 패키지 설명은112에서
삭제·통합했다. 과거 보고서에 복제된 실행 테스트는 현재 회귀에 필요한 단언을
이관한 뒤 삭제했다. 과거 결과를 다시 확인하려면 기록된 Git commit을 사용한다.
