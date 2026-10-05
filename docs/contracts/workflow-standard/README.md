# Workflow 규격 문서 묶음

- [확정 규격2.0](../../workflow-standard.md): 작성·검증·승인·실행·API 계약. 현업 작성자는 이 문서를 사용한다.
- [1.0 대비 변경 추적](../../review/workflow-standard-changes.md): 원본 경로별 변경, 이유, 구현·시험 위치. 확정 규격과 별개다.
- [받은1.0 원본](original-1.0/workflow_spec_1.0.md): 받은 파일의 바이트를 보존했다. SHA256.json에 ZIP/파일별 지문을 기록했다.
- [공개 JSON Schema](workflow-standard.schema.json): 실행 패키지의 같은 resource와 동일성 검증한다. JSON Schema만으로 자산·조건·참조의 유효성이 증명되지는 않는다.
- [static 예제](workflow_static.example.json), [필드 설명](workflow_static.example.jsonc).
- [adaptive 예제](workflow_adaptive.example.json), [필드 설명](workflow_adaptive.example.jsonc).

예제의 inventory/billing Skill·Tool은 독립 테스트 자산이다. 기본 배포 자산에 그대로 POST할 수 있다고 가정하지 않는다. 등록 함수·출력 매핑이 맞는 자산 풀에서 실행하며, 업무별 Skill/Tool을 등록 ID로 교체하고 의미 검증한다. 두 풀 모두 공통 실행기를 통해 실제 함수 실행을 검증했다(모델/Executor transport는 double).

공개2.0과 내부2.0-draft의 숫자는 같은 의미가 아니다. 공개 문서는 normalize()를 통해 내부 승인·실행 계획으로 변환한다. 기존1.3 compatibility 코드와 원본1.0을 이 규격으로 조용히 재해석하지 않는다.

- [등록·검색 확정 계약](../../workflow-registration-and-search.md): 실행 JSON과 별개의 user_queries envelope·수정·색인·HNSW 진단.
- [등록 예시](workflow.registration.example.json), [요청 Schema](registration.schema.json), [자산 응답 Schema](resource.schema.json), [수정 Schema](update.schema.json), [검색 요청](search-request.schema.json), [검색 응답](search.schema.json): 실제 Pydantic class에서 생성.
