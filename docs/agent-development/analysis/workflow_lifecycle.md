# Analysis Workflow Lifecycle

이 문서는 그래프 구현과 독립적인 분석 수명주기 정책을 정의한다.

## 1. 기존 Workflow 추천

사용자 요청을 받으면 저장된 Workflow 중 목적과 입력 계약이 맞는 항목을 먼저 추천한다.

- 추천 결과가 있고 사용자가 수락하면 해당 Workflow를 실행한다.
- 추천 결과가 없거나 사용자가 거절하면 Registry-only Workflow 생성 단계로 이동한다.

## 2. Registry-only Workflow 생성

기존 Skill과 Registry Tool만 사용한다.

- 생성·수정 Tool을 Workflow에 포함하지 않는다.
- 사용자가 수락하면 Workflow를 자산으로 저장하고 실행한다.
- 기존 자산만으로 부족하면 임시 Tool을 섞지 않고 부족한 기능을 표시한다.
- 사용자가 이 Workflow도 거절하면 Ephemeral Code 단계로 이동한다.

## 3. Ephemeral Code 실행

필요한 함수와 실행 코드를 셀 JSON 안에서만 생성한다.

- Workflow를 만들지 않는다.
- Tool, Skill, Workflow 자산으로 저장하지 않는다.
- 함수 정의와 호출을 같은 셀에 넣는다.
- Executor에 한 셀씩 전달하고 같은 kernel을 유지한다.
- 생성 결과는 `EphemeralCodeOutput`과 `write_ephemeral_cell_json_files`를 사용한다.

## 자산화 경계

| 단계 | Workflow 저장 | Tool 저장 | Executor JSON |
|---|---:|---:|---:|
| 추천 Workflow | 기존 자산 사용 | 기존 자산 사용 | 예 |
| Registry-only Workflow | 사용자 수락 시 가능 | 아니요 | 예 |
| Ephemeral Code | 아니요 | 아니요 | 예 |

사용자가 명시적으로 별도 승인하지 않는 한 Ephemeral Code를 Registry나 Skill로 승격하지 않는다.
