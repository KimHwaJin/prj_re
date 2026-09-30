# 042. 전처리 데이터 등록·조회 계약 초안

| 항목 | 내용 |
|---|---|
| 상태 | 계약 초안·오프라인 검증 완료 / Executor API·Agent 런타임 연계 미구현 |
| 시작일 / 완료일 | 2026-10-01 / 2026-10-01 |
| 브랜치 | feature/dataset-registry-contract |
| 기준 commit | e5fc1d9fa38364bb376847f622323f2eee7949c8 — 041에서 분기 |
| 계약 구현 commit | 기록 commit에서 확정 |
| 배포 상태 | 실제 서비스 기동·변경 없음. 베이스 병합·push·배포 미수행 |

## 문제와 범위 변경

다음 개선으로 PVC 데이터를 동적으로 조회·선택하여 후속 분석에서 재사용하는 작업을 제안했다. 사용자는 Executor의 전처리 파일 등록 체계가 아직 구현되지 않았고 API 명세도 없다고 확인했다. 따라서 런타임 연결을 먼저 구현하는 대신 양쪽이 사용할 **계약 초안부터 작성**하도록 작업 범위를 정했다.

현재 Agent는 중앙 설정의 analysis_datasets를 사용한다. Executor에는 artifacts workspace의 변경 파일 감지·DATASET 유형 분류가 있으나, 공유 범위별 Parquet 조회·스키마·의미 설명 등록 계약은 없다. 기존 Artifact POST는 텍스트 파일 생성 및 Execution SUCCEEDED를 요구하므로 중간 Step의 전처리 데이터 메타데이터 등록과 같은 기능으로 취급할 수 없다.

## 이번 산출물

- [Executor 개발자용 계약 안내](../design/dataset-registry-contract/README.md): 제안 API 5개, 주체별 책임, 등록 시점, ID/버전, 범위 경로, 저장소·게시 규칙, 실제 구현 순서.
- `src/service_contracts/dataset_registry_draft.py`: 외부 API와 연결되지 않은 DTO·순수 검증 규칙·미래 async provider Protocol.
- [JSON Schema](../design/dataset-registry-contract/dataset-contract.schema.json): 16개 정의. 추가 소유 관계 규칙과 실제 파일 확인은 schema만으로 해결하지 않음을 명시.
- [합성 예시](../design/dataset-registry-contract/examples/project-reuse.json): 등록 요청·Executor 후보·내부 record·공개 view·실행 binding. 실파일을 생성하지 않음.
- `scripts/diagnostics/validate_dataset_contract.py`: 스키마/예시 생성·일치·구조 검증.
- 신규 오프라인 시험 50개 및 [검증 기록](../reports/dataset-registry-contract-verification-2026-10-01.json).

| 판단 지점 | 초안의 방향 |
|---|---|
| 누가 data.json을 작성하는가 | 필요하면 Executor가 실제 파일 정보와 Agent annotation을 합쳐 작성. Tool 개발자에게 요구하지 않음 |
| 기존 API를 바꿔야 하는가 | 코드 제출·Artifact POST 유지. 별도 Dataset 메타데이터 API 권장 |
| 등록 시점 | 실제 성공한 저장 Step + 게시 완료 + 측정한 footer. 전체 Execution 성공 조건은 두지 않음 |
| 재사용 범위 | USER/PROJECT/SESSION, 기본 PROJECT. 원천 MinIO 공유와 별개 |
| 재사용 입력 | pvc:UUID:version 문자열. 기존 data_reference 승인 입력 형태 유지 |
| 버전 변경 | 승인 시/새 Execution 제출 전 확인. stale이면 재선택·재승인, 최신 파일로 자동 대체하지 않음 |
| 파일 식별 비용 | 매 조회 전체 파일 해시 금지. stat/footer 토큰은 내용 불변 증명과 구분 |
| 저장 방식 | API 계약과 분리. 기존 Executor 인덱스 확장·sidecar 여부는 Executor 구현에서 결정 |

Agent의 현재 단수형 출력 경로와 Executor의 복수형 workspace 경로 차이도 기록했다. 이번에 경로를 바꾸거나 존재하지 않는 API를 호출하도록 설정하지 않았다.

## 검증 결과

신규 50개 시험은 범위별 접근, 다른 세션 재사용 규칙, 정규 ID·버전, 파일 변경 거절, 작성 중/실패 Step/미측정 파일 거절, 소유자 변경·범위 확대 거절, 부모 입력 검증, 최대 200개 컬럼, 공개 구조 필드 제한, JSON roundtrip을 확인했다. 200GB/10억 행은 합성 메타데이터이며 실제 대용량 Parquet를 읽는 성능 시험이 아니다.

기존 계획 승인에 버전이 포함된 문자열 입력과 binding을 전달해 freeze할 수 있는지 순수 함수 시험으로 확인했다. **실제 동적 Registry provider 연결 성공을 의미하지 않는다.**

| 실행 | 결과 |
|---|---|
| 신규 계약 단독 시험 | 50 passed, 0.50초 |
| 계약+기존 planning/plan_revision/compiler/workflow_condition/bootstrap 회귀 | 127 passed, 3 warnings, 4.81초. 신규 50개 포함, 중복 합산하지 않음 |
| export된 schema/예시 재검증 | 통과, schema definitions 16 |
| 기준 vs 초안 OpenAPI | 모두 34 paths, 전체 OpenAPI SHA-256 동일 |
| 앱 생성 시 신규 draft 모듈 import 여부 | 양쪽 모두 미import, 실제 앱 라우터·기동 연결 없음 |

3 warnings는 기존 모델 시험에서 checkpointer가 없는 graph에 durability를 지정한 경우다. 전체 727개 회귀는 041의 이전 결과이며 이번에 다시 실행한 결과로 포함하지 않는다. 외부 DB·LLM·Executor 호출과 실제 Dataset 파일 I/O는 하지 않았다.

재현은 계약 안내의 명령을 따른다. 실제 서비스 lifespan을 실행하지 않고 Worker를 비활성화한 앱의 OpenAPI만 비교했다.

## 완료 범위와 다음 작업

**완료는 명세 초안·DTO·검증·기록까지다.** 후보 생성, 실제 Parquet footer 읽기, 물리 경로·symlink 확인, 원자 파일 게시, 범위 인덱스, 멱등 등록 영수증, fresh resolve, 실제 cross-session 재사용은 구현하지 않았다. 문자열 helper에 예전 파일 정보를 넣는 것만으로 현재 파일의 존재·동일성이 보장되지 않는다.

Executor에서 storage namespace/root, 경로 규칙, 후보·등록·조회·resolve, 내부 문맥 전달과 페이지/오류 규격을 확정하고 구현하는 것이 먼저다. 그 다음 Agent adapter·승인 snapshot·제출 전 확인을 연결하고 실제 파일로 연계 시험한다. 보고서 Artifact 등록 시점, 프로젝트 메모리, Workflow 검색은 이번 계약에서 확정하지 않는다.

원래 사용자 checkout의 변경은 보존하고 별도 파생 worktree에만 기록한다. Executor 저장소는 읽기만 했다.
