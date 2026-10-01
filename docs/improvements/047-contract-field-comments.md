# 047. API·Workflow 전체 필드 한국어 주석

| 항목 | 내용 |
|---|---|
| 상태 | 주석·동일성·46개 회귀 검증 완료 / 병합·게시 예정 |
| 날짜 | 2026-10-01 |
| 작업 브랜치 | feature/contract-field-comments |
| 기준 commit | 59f1e8082adbe11b081c2bd5d581440ccada2932 |
| 구현 commit | 미커밋 |

## 문제와 변경

사용자는 문서·JSON 파일의 모든 필드가 무엇을 뜻하는지 주석으로 설명하도록 요청했다. 원본 예제의 파싱 가능성을 유지하면서 중첩 필드·Schema·OpenAPI·legacy 형식을 모두 설명해야 했다.

- 계약 JSON 24개에 같은 이름의 JSONC 사본을 추가했다. 객체의 모든 필드 앞에 한국어 주석을 붙이며 원본 데이터와 같도록 검증한다.
- 주요 API/Workflow 문서의 예제 6곳을 필드 주석이 있는 JSONC로 갱신했다. 문서용 주석과 API 제출 body를 구분한다.
- payload/OpenAPI schema의 필드 description 및 문서용 Workflow schema의 description에 의미·형식상 필수 여부를 기록했다. 동적 조건부 필수는 업무 계약을 따른다.
- [필드 주석 안내](../contracts/field-comments.md)에 전체 파일·식별자 차이·사용 및 갱신 방법을 정리했다.
- scripts/design/annotate_public_contracts.py가 설명 없는 새 필드를 거절하고 문맥별 주석·동일성 검증·본문 갱신을 수행한다. 모델의 신규 Schema 추출은 별도다.

본문 필드 이름이나 예제 값을 변경하지 않고 runtime 검증 규칙·API 경로·실행 로직을 유지한다. SSO/Dataset/Workflow CRUD의 후속 구현 제한도 그대로다.

## 검증

- JSONC 24개에 객체 필드 5,161개를 주석으로 설명했다. 반복 등장하는 필드를 포함한 개수이며 서로 다른 필드 이름 수가 아니다. 모든 필드 바로 앞에 한국어 주석이 있고 주석 제거 후 원본 JSON과 동일함을 확인했다.
- schema 필드 정의 567개에 한국어 description을 확인했다. 반복된 모델/사본을 포함한다. 검증 keyword는 이전과 같고 문서용 Workflow schema는 실행용과 검증 규칙이 동일하다. 실행용 schema는 LLM 프롬프트에 포함되므로 원문을 유지했다.
- 본문 JSONC 예제 6개는 이전 JSON 데이터와 동일하다. 원본 요청/이벤트/응답/Workflow 예제 파일도 변경되지 않았다.
- 생성 스크립트를 다시 실행해도 내용이 달라지지 않는 idempotence를 확인했다.
- API 예제 18개를 실제 Pydantic 모델로 재검증하고 판단·수정 승인 validator를 통과했다. 새 Workflow 예제 2개는 실제 등록 Skill/Tool과 함수 인자 검증을 통과했고 legacy 예제는 기존 모델 검증을 통과했다.
- 패키지 경계·계획 Runtime·조건·compiler·계획 수정 pytest **46 passed**, 3 warnings, 4.80초. 경고는 기존 no-checkpointer durability 안내다.

새 부하·실제 모델/DB/Executor E2E 시험은 수행하지 않았다. wheel 빌드 및 격리 검증도 통과했다. source checkout import 없이 API 36 paths, mock graph 4 steps, 12개 역할 builder·프롬프트·리소스를 확인했다.

## 통합·게시

사용자의 이전 게시 요청에 따라 문서 완료 후 베이스 통합 및 origin 게시를 진행한다. 실제 결과는 검증 후 기록한다. 원본 사용자 checkout과 외부 서비스는 유지한다.
