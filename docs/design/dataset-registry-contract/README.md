# 전처리 데이터 등록·조회 계약 초안

상태: **042, `dataset-registry.v1-draft` — 오프라인 계약 검증 완료, 실제 API·저장소 연계 미구현.** Executor에 아직 전처리 데이터 등록·조회 명세가 없다는 확인에 따라, 먼저 양쪽 개발자가 사용할 계약을 제안한다. 이 문서의 경로는 사용할 수 있는 API 목록이 아니다.

권장안은 **Executor가 실제 파일 정보를 확인하고, Agent가 분석 의미를 덧붙여 전용 Dataset API에 등록하는 방식**이다. 코드 제출 API의 metadata를 파일 생성 명령으로 바꾸지 않는다. 현업 Tool 개발자는 기존처럼 승인된 저장 Tool에서 Parquet를 저장하며 별도 `data.json`을 작성하지 않는다.

## 현재 구현과 이번 범위

| 영역 | 확인한 현재 구현 | 이번 초안 |
|---|---|---|
| Executor Artifact 감지 | Execution workspace의 artifacts 아래 파일 변경을 감지하고 DATASET 유형을 구분하는 코드가 있음 | 사용자·프로젝트·세션 공유 데이터 루트까지 후보 감지를 확장할 계약 |
| Artifact POST | Agent가 작성한 텍스트를 실제 파일로 저장함. Execution SUCCEEDED를 요구하고 DATASET 등록 유형은 없음 | 기존 API 유지. 별도 Dataset 메타데이터 등록 API 권장 |
| Agent 데이터 목록 | `settings.analysis_datasets`에 설정된 목록 | 나중에 동적 Registry provider를 연결할 경계만 정의 |
| 데이터 경로 | Agent는 `user/.../project/.../data`, Executor workspace는 `users/.../projects/.../sessions/...` | 복수형 경로로 맞추는 제안. 이번에 실제 경로를 변경하지 않음 |
| 재사용 | 승인 당시 설정 경로를 snapshot에 복사 | 정확한 Dataset 버전과 현재 파일 상태를 확인한 내부 binding 제안 |

실제 구현 근거는 Agent의 `planning/runtime.py`, `planning/graph.py`, `service_contracts/plan_review.py`, Executor의 `infrastructure/workspace.py`, `infrastructure/_artifacts/{discovery,validation,persistence}.py`, `infrastructure/materialized_artifacts.py`다. Executor 소스는 읽기만 했으며 이번 브랜치에서 변경하지 않았다.

이번 산출물은 [DTO·규칙](/Users/a10054/.codex/worktrees/refactor-bootstrap/dtest-agent/src/service_contracts/dataset_registry_draft.py), [JSON Schema](dataset-contract.schema.json), [합성 예시](examples/project-reuse.json), 오프라인 시험이다. 새 라우터, DB 테이블, 미들웨어, 파일 감시기, 운영 설정은 추가하지 않았다. 예시 JSON의 context/storage/registration/candidate 등 전체 묶음은 검증용이며 한 REST 요청 body가 아니다.

## 책임 분리

| 주체 | 작성·확인하는 정보 |
|---|---|
| 저장 Tool | 사용자가 승인한 처리 결과를 지정된 데이터 디렉토리에 저장. 임의의 함수 반환값을 자동 저장하지 않음 |
| Executor | 실제 파일 위치·크기·게시 상태·Parquet 행 수와 스키마·변경 토큰·실제 생성 Execution/Step/Attempt |
| Agent | 결과를 관찰한 뒤 제목·설명·활용 목적·주의사항·승인 입력 중 부모 Dataset 버전 해석 |
| API/Agent 서비스 | 현재 사용자와 세션의 실제 소속을 확인하고 데이터 범위·버전 선택·승인 snapshot 관리 |
| 프론트 | 공개 DatasetView를 표시하고 selection_id를 선택값으로 전달. 파일 경로·소유자·스키마를 직접 선언하지 않음 |

스키마를 LLM이 추정하여 확정 정보로 저장하면 안 된다. `annotation.declared_parents`도 선언된 관계이며 계산 결과의 수학적 계보를 증명하는 정보가 아니다. 서비스는 승인된 입력 버전 이외의 부모 선언을 거절한다.

## 제안 API

아래 API는 **Agent 서비스 → Executor 내부 호출**을 기본으로 한다. 프론트 공개 API·기존 Run/resume/SSE 인터페이스는 이번에 변경하지 않는다.

| 제안 경로 | 입력 | 반환·역할 |
|---|---|---|
| `GET /api/v1/executions/{execution_id}/dataset-candidates` | 해당 Execution의 실제 Step 완료 이후 조회; limit ≤100, cursor | Executor가 측정한 DatasetCandidate 후보. 페이지 envelope는 Executor 구현 시 확정 |
| `POST /api/v1/executions/{execution_id}/datasets` | DatasetRegistration | DatasetRecord; 파일 생성·코드 실행 없이 확인된 후보에 설명을 등록 |
| `GET /api/v1/datasets` | 신뢰할 수 있는 현재 세션 문맥, limit ≤100, cursor | DatasetPage. USER/PROJECT/SESSION 범위로 접근 가능한 DatasetView만 반환 |
| `GET /api/v1/datasets/{dataset_id}?version=N` | 명시적 양의 버전 | 내부 DatasetRecord. 다른 소유자의 존재를 노출하지 않음 |
| `POST /api/v1/datasets/{dataset_id}/resolve` | version, expected_file_revision, storage_namespace | 현재 파일을 다시 검사한 RuntimeDatasetBinding. 경로는 내부 응답에만 포함 |

인증·문맥 전달 header의 최종 이름은 아직 확정하지 않았다. Agent는 기존 `X-User-Id`로 식별된 사용자와 DB에 저장된 프로젝트/세션 소속에서 내부 UUID 문맥을 만든다. Executor는 신뢰하는 서비스 호출자만 이 문맥을 전달할 수 있도록 검사해야 한다. 단순히 외부 요청 body의 owner/project/session 값을 믿는 API로 구현하지 않는다. DatasetRegistry Protocol의 메서드 인자는 이 문맥을 명시적으로 요구한다.

후보는 성공한 Step의 변경 파일 목록을 기준으로 수집한다. 목록 요청마다 PVC 전체를 재귀 순회하거나 모든 Parquet를 다시 읽는 방식은 피한다. 현재 artifacts 하위 감지만으로 프로젝트 datasets 디렉토리가 자동 발견된다고 가정하지 않는다.

### 등록 body 예시

```json
{
  "idempotency_key": "demo-dataset-registration-1",
  "candidate_id": "00000000-0000-4000-8000-000000000004",
  "expected_file_revision": "demo-stat-footer-revision-1",
  "scope": "PROJECT",
  "annotation": {
    "title": "이상치를 제외한 온도 분석 데이터",
    "description": "완료된 전처리 결과에 대한 설명",
    "purpose": "후속 분석에서 재사용",
    "notes": [],
    "declared_parents": []
  }
}
```

`candidate_id`는 Executor가 생성하고 실제 Execution·Step·소유 범위·파일과 연결한다. 등록 요청에 path, rows, columns, owner ID, Python code를 넣으면 거절한다. 요청의 scope로 기존 후보를 다른 소유자나 더 넓은 범위로 재배정할 수 없다. 데이터 저장 범위는 실행 전 지정되어야 한다.

등록 멱등성은 **생성 Execution + 소유 문맥 + idempotency_key** 단위로 보관하는 것을 제안한다. 같은 키·같은 요청은 처음 받은 버전과 영수증을 반환하고, 같은 키·다른 요청은 409로 거절한다. 설명 등록 실패만으로 전처리 코드를 다시 실행하지 않는다. 이 저장·재시도 동작은 DTO 검증 helper에 구현된 기능이 아니다.

### 조회·선택·해석

공개 DatasetView에는 ref, selection_id, 제목·설명·목적, scope/status, 파일 크기, 제한된 Parquet inspection이 들어간다. 파일 경로·변경 토큰·실행 코드·내부 producer ID를 구조 필드로 노출하지 않는다. 자유 서술 텍스트의 정확성이나 민감정보 제거를 자동 보장하는 계약은 아니다.

선택값은 `pvc:{dataset_uuid}:{version}` 문자열이다. 예: `pvc:00000000-0000-4000-8000-000000000010:1`. 기존 Workflow의 `kind=data_reference` 입력에 문자열로 들어가므로 Run body를 객체 형태로 바꿀 필요가 없다. `latest`, 버전 생략, 0, 비정규 UUID/숫자는 허용하지 않는다. 정적 설정 데이터의 기존 ID는 유지하되, 연결할 때 `pvc:` 접두어 충돌은 거절해야 한다.

내부 resolve 응답은 ref, owner, storage_namespace, runtime_path, expected_file_revision, revision_method를 담는다. 승인 시점과 실제 새 Execution 제출 직전에 현재 파일 상태를 검사한다. 파일·버전이 달라졌다면 새 버전을 고르게 하고 재승인을 받으며, 조용히 최신 파일로 바꾸지 않는다. 이번 compatibility 시험은 기존 승인 코드에 정규 문자열과 binding을 제공할 수 있음을 확인했을 뿐, 실제 resolver를 승인 코드에 연결한 시험은 아니다.

오류 매핑 제안: 알려지지 않았거나 접근할 수 없는 Dataset은 404, 파일/버전 변경·미게시 파일·멱등 키 충돌은 409, 필드·범위·참조 형식 오류는 422. 스토리지 연결 실패와 파일이 없음을 혼동하지 않는다. 정확한 오류 envelope는 Executor 공통 오류 규격을 따른다.

## 범위·경로·버전

MinIO는 기존 원천 데이터 저장소이며 모든 사용자에게 공유된다. 이 Registry의 대상은 **승인된 처리로 PVC에 저장된 전처리 Parquet**다. MinIO에 메타데이터를 쓰거나 원천 중복 반입을 해결하는 작업은 포함하지 않는다.

runtime_root가 `/workspace/pv`인 경우 아래를 제안한다. 실제 runtime_root는 Executor 배포 설정에서 결정한다.

| 범위 | 제안 데이터 루트 | 재사용 가능 범위 |
|---|---|---|
| USER | `users/{user_id}/datasets` | 같은 사용자의 프로젝트·세션 |
| PROJECT 기본값 | `users/{user_id}/projects/{project_id}/datasets` | 같은 사용자·프로젝트의 세션 |
| SESSION | `users/{user_id}/projects/{project_id}/sessions/{session_id}/datasets` | 해당 세션만 |

결과 파일은 root 아래 Run/저장 단위별 새 경로에 게시하고 이미 등록된 파일을 덮어쓰지 않는 정책을 권장한다. 논리 dataset_id와 version은 Registry가 발급한다. 동일 ID의 새 버전을 만드는 정책은 Executor 구현에서 정하되 이전 버전은 자동 대체하지 않는다. 오래된 파일을 외부에서 바꾸면 해당 버전은 STALE로 취급한다.

storage_namespace는 공유 PVC의 논리 이름이다. Pod 이름·커널 ID·현재 Runtime ID가 아니다. 다른 Runtime에서도 같은 namespace를 실제로 마운트한다는 확인이 있어야 재사용할 수 있다. Runtime pool마다 다른 PVC를 쓰는 경우에는 스토리지와 실행 대상의 매핑이 먼저 필요하다.

초안 문맥 UUID는 현재 Agent 서비스의 내부 ID에 맞췄다. Executor 기존 코드 제출 API의 문자열 ID 규격을 전역으로 바꾸는 요구는 아니다. 다른 호출자가 비UUID 소유 ID를 쓰는 경우 새 Dataset API에서 어떤 ID 체계를 지원할지 별도로 확정해야 한다.

## 언제 등록하고 무엇을 검사하는가

1. 사용자 승인에 저장 Tool과 데이터 출력 범위가 포함된다.
2. Executor가 해당 Step을 실행하고 실제 성공·파일 게시 완료를 확인한다.
3. Executor가 후보와 Parquet footer 정보를 제공한다.
4. Agent가 Step 관찰 결과를 바탕으로 annotation을 만든다.
5. 서비스가 확인된 후보·변경 토큰으로 메타데이터를 등록하고 등록 영수증을 저장한다.

**전체 Execution SUCCEEDED는 등록 전제에 넣지 않는다.** 전처리 저장 Step이 성공했다면 이후 학습 Step이 실패해도 유효한 데이터는 재사용할 수 있어야 한다. 기존 Artifact POST의 SUCCEEDED 조건을 이번에 제거하는 방식으로 구현하지 않는다. 보고서 Artifact POST의 호출 시점도 별도 미확정 항목으로 남긴다.

Step 성공만으로 분리된 백그라운드 writer까지 종료됐다고 단정하면 안 된다. 후보는 `publication=FINALIZED`와 정상 footer를 모두 요구한다. WRITING/UNKNOWN/측정 실패는 등록 불가다. 이 publication은 Executor가 writer 종료/원자 게시 규칙에 따라 판단해야 하며 LLM이 선언하지 않는다. 여기서 FINALIZED는 **파일 게시 상태**로, Execution Finalize API와 다른 개념이다.

파일 크기·mtime 등 stat와 footer에서 얻은 `STAT_AND_FOOTER` 토큰은 관측 가능한 변경을 찾는 수단이며 파일 전체 내용의 SHA/불변성 증명이 아니다. 전체 200GB 파일을 조회·승인마다 해시하지 않는다. 더 강한 식별이 필요하면 스토리지 generation이나 저장 시점 writer SHA를 사용한다. 토큰 알고리즘·원자 게시 방식은 Executor의 저장소 driver 계약에서 확정한다. 확인과 사용 사이의 변경 가능성까지 제거하려면 등록 파일 불변 정책이 필요하다.

inspection은 행 수, 전체 컬럼 수, 최대 200개 컬럼의 index/name/dtype, 생략 여부를 담는다. 측정 실패는 UNAVAILABLE+이유로 표시하고 행 수·타입을 꾸며 넣지 않는다. 컬럼명 중복은 index로 구분한다. 파일 전체 데이터·DataFrame·head 배열은 초기 계약에 포함하지 않는다. footer 읽기도 큰 schema에 대비해 읽기 크기·시간 한도를 두는 것을 권장하며 아직 운영 설정을 추가한 것은 아니다.

## data.json은 누가 쓰는가

**필요하다면 Executor가 쓴다.** 실제 후보의 기계적 정보와 Agent가 API로 전달한 annotation을 합쳐 DatasetRecord 형태의 버전 메타데이터를 만든다. 예를 들어 Parquet 옆의 `.data.json`을 원자적으로 게시할 수 있다. Tool 개발자에게 JSON 형식을 수동으로 작성하도록 요구하지 않고 Agent가 임의 경로에 파일을 쓰게 하지 않는다.

JSON sidecar는 필수 HTTP 계약이 아니다. 목록 조회를 위해 기존 Executor PostgreSQL/Artifact 인덱스를 확장하거나 파일 인덱스를 사용할 수 있다. 저장 방식을 먼저 대규모 데이터 카탈로그 DB로 확정하지 않는다. 둘 다 쓰면 한쪽을 원본으로 정하고 다른 쪽은 재생성 가능한 인덱스로 둔다. API마다 PVC 전체를 탐색하는 구현은 피한다. 실제 sidecar 경로·인덱스·정합성은 Executor 개발 시 결정한다.

## 나중에 연결할 Agent 흐름

```mermaid
flowchart LR
  A[현재 세션의 데이터 목록 조회] --> B[Agent 계획 제안]
  B --> C[HITL 선택·입력·승인]
  C --> D[버전·현재 파일 확인 / snapshot 고정]
  D --> E[승인된 코드 Executor 제출]
  E --> F[Step 완료·결과 관찰]
  F --> G[실제 저장 후보 확인]
  G --> H[Agent 의미 설명]
  H --> I[Dataset 메타데이터 등록]
  I --> J[다음 Step 또는 Finalize]
```

읽기 가능한 제한된 목록·스키마는 create_agent의 데이터 문맥/도구 및 middleware로 재사용할 수 있다. 소유권 검사·파일 resolver·등록 영수증 처리는 결정적인 서비스/provider 영역에 둔다. LLM에 임의 PVC 탐색 경로나 미승인 Python을 실행시키는 도구를 주는 방식으로 데이터 조회를 대신하지 않는다. 실제 graph·middleware 구현은 이번 작업에 없다.

후속 분석은 새 Run/Execution/커널에서 승인된 Dataset 버전을 파일로 다시 읽는다. 이전 Python 변수나 살아 있는 커널에 의존하지 않는다. Executor 실행 대기 중 동일 세션의 새 입력 잠금과 명시적 Finalize 원칙은 유지한다.

DTO의 경로 검사는 문자열 규칙이다. 실제 symlink·마운트·실파일 소유권 검사는 Executor driver에서 구현해야 한다. Registry 목록 격리만으로 공유 PVC의 임의 Python 파일 접근까지 차단되는 것은 아니다.

## Executor 개발 순서와 남은 확정 사항

1. 실제 공유 스토리지 namespace/root와 데이터 범위별 출력 경로를 합의한다. 현재 Agent의 단수형 경로도 다음 연결 작업에서 맞춘다.
2. Step 파일 감지에 범위별 datasets 루트를 추가하고 실제 Parquet inspection·producer evidence·게시 상태를 구현한다.
3. 후보 조회·멱등 메타데이터 등록·범위 목록·정확한 버전 resolve를 구현한다. 인덱스/sidecar의 원본, 내부 호출 문맥·비UUID 지원, 오류 및 페이지 envelope를 확정한다.
4. 실제 파일 생성·변경·타사용자 격리·다른 세션 재사용을 Executor에서 검증한다.
5. Agent에 비동기 Registry adapter를 연결하고 승인 binding·새 Execution 제출 전 재확인을 구현한 뒤 실제 연계 시험을 한다.

1~4는 현재 Executor에 이미 구현됐다고 가정하지 않는다. 같은 schema를 양쪽에서 쓰기 위해 Agent의 전체 LangChain 의존성을 설치할 필요는 없다. JSON Schema는 배포 패키지와 독립적으로 전달할 수 있다. JSON Schema는 필드 구조를 검사하며, 소유 관계·실파일 상태 등 추가 규칙은 backend가 검사해야 한다.

## 오프라인 검증 재현

```bash
PYTHONPATH=src python scripts/diagnostics/validate_dataset_contract.py
PYTHONPATH=src python -m pytest src/agent_service/agents/analysis/tests/test_dataset_contract_draft.py -q
```

스키마/예시를 모델에서 다시 생성할 때만 첫 명령에 `--write`를 붙인다. 예시는 합성 UUID와 존재하지 않는 파일을 사용한다. 범위 격리, 버전 변경, 작성 중/실패 파일 거절, 부모 참조, bounded metadata, 기존 문자열 승인 binding 호환 등 **50개 통과**했다. 실제 Executor 호출·실파일 읽기·쓰기·LLM 호출은 0회다. 이 결과는 운영 등록 API의 정확성이나 PVC 연계 성공을 입증하지 않는다.
