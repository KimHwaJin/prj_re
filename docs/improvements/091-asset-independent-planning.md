# 091 예시 Skill·Tool과 공통 Agent 분리

날짜: 2026-10-05. 브랜치 `feature/asset-independent-planning`, 090의 `f66a9a6`에서 분기. 구현·검증 완료. 베이스 미병합·미푸시·운영 미배포.

구현·검증 커밋: `b7872f4` (`refactor: decouple planning from sample skill and tool assets`).

## 문제와 원인

사용자는 현재 Skill·Tool이 예시이며 지속적으로 교체·확장될 자산이라고 재확인했다. 현재 2.0 실행 경로에는 특정 로드 함수명과 인자명에 따른 검증이 conversation/revision/repair에 중복돼 있었다. 공통 계획 prompt도 특정 함수·컬럼·데이터 형식·판단 방법의 예제로 동작을 안내했다. 이름이 다른 자산으로 교체하면 같은 데이터 참조 보호가 적용되지 않거나 불필요한 기존 Tool을 계획할 수 있는 구조였다.

Registry 생성기도 파일명과 같은 함수 하나만 추출해 한 파일의 여러 Tool이라는 개발 규칙을 충족하지 못했다. 반환 key 힌트는 registry에 있어도 실제 모델 조회 메타데이터에는 제공하지 않았다.

## 변경

- `service_contracts/tool_bindings.py`에 allowed_sources/input_kind/required 정책을 정의했다. 기계 검증은 Tool 이름 대신 등록 정책을 사용한다. 불필요하게 함수 본문에 별도 manifest 작성 규약을 추가하지 않는다.
- 공통 Workflow validator가 모든 등록 Tool과 정책 상속 별칭을 검사한다. 필수 binding 누락·잘못된 출처/kind를 거절하고 최종 승인에서는 필요한 실제 입력값도 확인한다. 정책 간 모순은 등록 시 거절한다.
- 기존 특정 로드 함수의 보호는 해당 자산 registry의 data_reference 정책으로 이관했다. 현재 예시의 결과 객체 인자는 step_output 정책으로 선언했다. 정책 없는 레거시 인자의 기존 규칙을 임의 추론으로 바꾸지 않는다.
- 수정 계획/복구의 origin 기반 함수는 등록된 연결·편집 정책을 상속한다. 별칭으로 변경해도 정책이 없어지지 않는다. 복구의 data_reference는 최초 승인한 dataset map에서만 해결한다.
- 기본값 보충은 literal을 허용할 때만 수행한다. 출력 객체·경로 참조를 임의 상수 기본값으로 바꾸지 않는다.
- `AssetCatalog`는 실제 AST signature/returns를 제공하고 source/Skill 문서/전체 유효 메타데이터·소속·정책으로 revision을 계산한다. 등록 안 된 Skill의 Tool 참조는 기동 시 거절하며 test_only 필터는 유지한다. 심볼릭 링크 자산 root도 일관되게 정규화한다.
- 생성기는 파일당 여러 공개 함수를 등록하고 기존 source/function 쌍의 ID와 수동 정책을 보존한다. 중복 ID·잘못된 인자 정책·미지원 제출 함수 형식을 거절한다.
- 공통 계획·대화·보고서 prompt의 특정 함수/컬럼/기법 예시를 제거하고 메타데이터·승인·관찰 기준으로 설명했다. 실제 잘못된 Step 편집 선언의 피드백에는 출처와 수정할 입력 필드를 표시한다.

함수 본문·실제 import·현재 자산 디렉터리는 유지했다. Tool은 여전히 docstring만 제거한 함수 코드로 Executor에 제출한다. 공개 API/SSE/HITL JSON·Executor body·DB schema·환경변수·LangGraph 노드/state 수명을 변경하지 않았다. 모델 호출 횟수나 재시도 상한도 변경하지 않았다.

## 검증

- 전체 Agent·진단 회귀389개 통과. 이 중 신규 자산 계약31개다.
- 기존 예시를 전혀 포함하지 않는 inventory/billing 풀에서 HITL 입력/배율 편집·조건부 Operation·사용자 제외·Finalize·terminal/report까지 4개 graph 흐름을 검증했다. 모델/Executor는 명시적 double이며 등록된 실제 함수 코드는 실행했다.
- 실제 사내 LLM으로 두 독립 풀의 조회·계획·HITL 승인14항목과 저장한 snapshot의 최종 배율 검산2항목 통과. 그 진단에서는 Executor를 호출하지 않았다.
- 임시 PostgreSQL에서 기존 계획/재계획 API4개 통과. 임시 컨테이너를 제거했다.
- 깨끗한 현재 소스 사본의 offline wheel에서 checkout import 없이 신규 계약·자산·prompt·5역할 builder·mock 승인 smoke 통과.
- 18 Skill·100 Tool의 등록/검색 회귀 통과. 부하·모델 의미 품질을 측정한 것으로 해석하지 않는다.

실패·보완 과정, 조건과 상세 검사 결과는 [검증 보고서](../reports/asset-independence-2026-10-05/README.md)에 기록했다. 속도·처리량 개선을 주장하는 작업이 아니다.

## 인수인계와 남은 범위

[Skill·Tool 유지보수 계약](../agent-development/skill-tool-contract.md)에 작성 위치, 한 파일의 여러 함수, ID 보존, docstring/returns, 두 인자 정책, 생성/검증/재배포 방법을 정리했다. 업무 설명은 Skill/docstring, 기계적으로 강제할 인자 출처·편집 범위는 등록 담당자의 metadata가 맡는다. 자연어만으로 안전 정책이나 실제 반환 schema를 자동 확정하지 않는다.

완료 범위는 현재 **2.0 Agent 실행 경로**다. 사용자가 유지 요청한 workflow 패키지의 기존 **1.3 관리·컴파일 코드에는 특정 데이터 로드 가정이 남는다.** 그 부분은 Workflow CRUD/2.0 이행의 후속으로 별도 기록하며 전체 레포의 독립성 완료라고 표현하지 않는다.

자산 revision 계산/정책이 바뀌므로 배포 전 기존 HITL/실행 중 복구의 이행을 검토해야 한다. 이미 승인한 원문을 최신 Tool로 다시 생성하지 않는다. 기존 콘솔18103은 이전 코드가 메모리에 올라온 프로세스로 그대로 보존했다. 이번 브랜치의 변경이 그 프로세스에 자동 반영된 것은 아니다.

다음 기능·디테일 우선 항목은 다양한 실무 자산과 모호한 자연어 요청의 계획 교정 품질, 관찰 preview 제한과 실제 Tool 처리 범위를 구분하는 보고서 해석이다. Dataset Registry·Artifact/Workflow CRUD·Gaia/사내SDK·Pod 부하·운영 이행과 모델 호출 최적화의 기존 후순위는 유지한다.
