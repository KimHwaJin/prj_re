# 자산 독립성 검증 — 2026-10-05

현재 예시 Skill·Tool을 전제로 하는 공통 계획 검사를 등록 정책으로 옮기고, 기존 자산을 포함하지 않은 두 개의 별도 풀에서 계획·편집·승인을 확인했다. 실제 LLM도 두 풀에서 모두 등록된 반환 구조를 연결했고, 승인 snapshot은 사용자 수정값을 보존했다.

## 조건과 결과

| 검증 | 실제 사용 범위 | 결과 |
|---|---|---|
| 기존 Agent + 신규 자산 계약 + 진단 회귀 | 모델 fixture, 필요한 Executor double, 실제 등록 함수 실행 | 389 통과. 신규 자산 계약 31개 포함 |
| 독립 그래프 실행 4흐름 | 2개 풀 × 조건부 진행/사용자 제외. InMemorySaver, 모델 fixture, Executor double의 실제 함수 실행 | 입력 수정 [3,5,7]·배율3 → 45 확인. 조건부 Operation/Finalize/terminal/report 완료 |
| 실제 모델 2흐름 | 실제 사내 모델, create_agent 조회·계획, LangGraph InMemorySaver/HITL. API·DB·Worker·Executor 없음 | 14개 실행 중 검사 + 저장한 승인 snapshot의 최종 배율4 검산2개 통과 |
| 계획 API PostgreSQL 회귀 | 별도 agentic_runtime_test/checkpoint_test, mock 모델, Executor 제출 없음 | 4 통과. 편집/재시작/승인/replay/재계획/개인 코드 보호 |
| 설치 패키지 smoke | 현재 소스의 깨끗한 임시 사본에서 offline wheel, python -I로 checkout import 제외 | 신규 계약·자산·prompt·create_agent 5역할·mock 승인 검증 통과 |
| 자산 규모 검사 | 18 Skill·100 Tool AST 등록·read_skill/search_tools | 통과. 모델 호출·부하 측정 아님 |

총 Python 회귀는 393개(389+별도PG4)이며 실제 모델 16항목/패키지 smoke는 별도다. 기존 모델 응답 fixtures를 실제 모델 결과로 계산하지 않는다. 성능 향상·실무 모든 자산의 정확성을 입증한 결과가 아니다.

## 독립 자산

| 풀 | Skill | 함수와 입력 | 반환 연결 |
|---|---|---|---|
| inventory | warehouse | decode_lots(document), summarize_lots(batch, multiplier) | batches 배열 → batch. stock.total 결과 |
| billing | accounting | extract_receipts(encoded), quote_invoice(lines, coefficient) | receipts 배열 → lines. invoice_total 결과 |

두 풀 모두 기존 데이터 로드/통계/이상치 함수와 Skill을 포함하지 않는다. 한 파일에 두 Tool을 두며 파일명도 함수명과 다르다. 모듈 최상위에는 import 시 실패하는 문장을 넣어 AST 조회가 파일을 실행하지 않는지 확인한다. 제출되는 함수에는 필요한 import가 그대로 남고 docstring만 제거된다.

실제 모델은 각 요청에서 JSON 문자열 [2,4,6]과 배율3을 읽고 등록 Tool 두 개만 사용했다. 반환 배열 selector를 각각 batches/receipts로 연결했다. HITL 승인 시 [3,5,7]과 배율4로 변경했고 frozen 승인값으로 보존됐다. 최종 배율2항목은 저장한 원본 snapshot을 후처리로 검산했으며 이 검산 때문에 모델을 다시 호출하지 않았다.

그 모델 진단은 **계획·승인까지**다. Executor에서 60을 계산했다는 증거가 아니다. 별도 unit graph의 45 결과는 모델 fixture + Executor double에서 실제 등록 함수를 실행한 증거다. 두 조건을 혼합해 실제 API/Executor E2E라고 표현하지 않는다.

## 검증 중 확인한 사항

실제 모델 진단 준비 단계에서 /var→/private/var와 같은 심볼릭 링크 루트가 정규화된 source 경로와 충돌했다. AssetCatalog 전체에 정규화한 root를 사용하도록 수정하고 같은 revision/source를 얻는 회귀를 추가했다. 수정 전에는 모델 호출 이전에 실패했으며 수정 후 두 실제 모델 흐름이 통과했다.

신규 테스트의 초기 기대값에는 condition을 binding과 혼동한 fixture와 returns 힌트 key/문자열 부분 검색의 오류가 있었다. 실제 공통 JSON schema와 메타데이터 구조에 맞춰 fixture를 수정했다. 이를 제품 오류로 계산하지 않는다.

첫 wheel smoke에서는 기존 로컬 build/lib에 남은 삭제된 과거 소스가 wheel에 포함돼 실패했다. 기존 폴더는 삭제하지 않고, git 대상 현재 소스와 이번 신규 파일만 복사한 깨끗한 임시 디렉터리에서 다시 빌드했다. 최종 wheel은 삭제된 graph/role과 테스트를 포함하지 않았다. 깨끗한 CI checkout과 로컬 build 캐시 조건은 구분한다.

## 재실행

```sh
PYTHONPATH=src python -m pytest src/agent_service/agents/analysis/tests scripts/diagnostics/tests -q
PYTHONPATH=src python scripts/diagnostics/verify_asset_independence.py \
  --model-env /path/to/private-model.env --output /path/to/private-result.json
```

실제 진단은 model 설정 키만 allowlist로 읽고 사용자 제공 alias를 프로세스 안에 한정한다. /etc/hosts를 변경하지 않는다. 원본 응답·승인 함수 코드는 private0600 결과로만 저장한다. [공개 검산 결과](result.json)는 synthetic 자산 식별자와 검사 결과만 담는다.

PG 테스트는 기존 서비스 DB와 다른 임시53605의 PostgreSQL17에서 실행했고 컨테이너를 제거했다. 기존 Executor Compose·콘솔18102/18103·그 DB/세션은 변경하지 않았다. 이미 떠 있는 콘솔의 Agent 코드는 자동 hot reload되는 환경이 아니므로 이번 코드 검증 환경과 구분한다.

[등록·유지보수 계약](../../agent-development/skill-tool-contract.md), [091 변경 기록](../../improvements/091-asset-independent-planning.md)을 따른다. 기존 1.3 관리 코드, 실무 자산 조합 의미, 다양한 자연어·동적 반환 타입, 실제 프론트/Gaia/SDK/Pod 부하·운영 이행은 별도 검토다.
