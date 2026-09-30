# 040. MULTI 실패 분석·수정 승인·후속 실행

| 항목 | 내용 |
|---|---|
| 상태 | 수정 수준 1~4 구현, 실제 Executor 8개 시나리오·실제 모델 수정 역할·전체 회귀/wheel 검증 완료 |
| 시작일 / 완료일 | 2026-10-01 / 2026-10-01 |
| 브랜치 | feature/agentic-execution-repair |
| 기준 commit | 199d4faa472a2c66c1ee1fbbadaa53b56df782a0 — 039에서 분기 |
| 구현 commit | 3b18207cad5c583c347a8cbcf64579dd5028c543 |
| 배포 상태 | 기존 Compose 유지, 임시 진단 API 종료, 베이스 병합·push·배포 미수행 |

## 문제와 변경

039는 정상 결과를 보고 다음 인자를 결정하고 후속 Operation을 실행할 수 있었지만, 코드 오류는 수정하지 않고 Cancel했다. 실제 repair_level/attempt ceiling은 0이었고 설정/화면에서 표현한 1~4는 실행되지 않았다.

| 영역 | 이전 | 이번 구현 |
|---|---|---|
| 실패 판단 | MULTI 실패 후 Cancel | 완전한 실제 코드 실패 + continuation + 허용 정책에서만 수정 역할 호출 |
| 수정 권한 | 실제 수준 0만 허용 | 1 binding, 2 실패 함수 구현, 3 등록 자산 재계획+승인, 4 실행별 함수/계획 |
| 모델 | 정상 결과 review/report | 독립 execution_repair create_agent + 프로젝트 prompt + schema/정책 검증 middleware |
| 승인 | plan_review, decision_review | repair_review 및 approve_repair/reject_repair, 명시적 권한 상승 동의 |
| 최초 승인 | 단일 승인 snapshot | 원본 보존, 실행별 수정 snapshot/hash/accepted 이력 분리 |
| 성공 결과 | 완료 Step 보존 | 수정 중에도 완료/skip anchor·근거가 확정된 decision·커널 객체 고정 |
| 시도 제한 | 실제 수정 없음 | Run 전체의 확정한 수정 Operation 상한. 실패 Step이 바뀌어도 유지 |
| 이벤트 | 모든 제출 Step 결과를 요구 | 실제 실패 이벤트의 부분 목록을 허용하고 빠진 Step은 NOT_RUN으로 표시 |
| 최종 결과 | 관찰·리포트·Executor 상태 | 코드 없는 수정 이력·실제 Operation outcome·종료 이유 추가 |

수정은 Agent 프로세스에서 Python을 실행하지 않는다. 기존 HTTP client/멱등성 보호·body 사전 checkpoint·SQL 세션 소유권·Redis inbox/receipt·SSE·Finalize/terminal 경계를 재사용한다. source SHA와 원래 승인·데이터·커널·목표·서비스 시도 ceiling을 검사한다. 수정된 등록 함수는 실행별 별도 ID로 분리하며 레포 Tool 파일을 덮어쓰지 않는다.

## 실제 확인하며 보완한 지점

Executor의 실패 Operation 이벤트에는 실행 결과가 기록된 Step만 포함된다. 미실행 Step까지 같은 개수의 manifest를 요구하면 실패를 읽는 단계에서 진행이 막혔다. 성공 이벤트는 모든 결과를 요구하되 실패 이벤트의 목록은 제출 sequence의 중복 없는 부분집합으로 검증한다. 빠진 Step은 성공으로 간주하지 않고 NOT_RUN으로 보존한다. 수정 후 기존 전체 Step 다음 sequence로 제출하는 실제 경로를 확인했다.

metadata 탐색 middleware가 최종 합성 안내를 conversation Reply JSON으로 고정하고 있어 수정 역할에 부적절했다. 역할별 최종 schema 안내를 주입하게 했으며 duplicate/round 제한은 유지했다. 낮은 수준 1~2의 실패에는 이미 확보한 source/metadata/evidence를 사용해 탐색 Tool을 붙이지 않는다. 높은 수준 3~4의 등록 자산 탐색은 같은 metadata middleware를 사용한다.

실제 모델 첫 두 시험에서는 탐색 후 최종 content가 비어 있었다. 검증 두 번 후 invalid_repair_proposal로 Cancel됐으며 시도는 0이었다. 두 번째 Phoenix 기록은 빈 최종 content 두 번, completion token 343/302, finish_reason stop을 보여준다. 빈 응답의 모델 서버 내부 원인까지 확정한 것은 아니다. 현재 선택 Skill만 전달하고 낮은 수준 탐색을 제거한 다음 시험은 유효한 보정 응답을 반환했다. 성공 기록만 남기거나 앞선 종료를 정상 수정 성공으로 바꾸지 않았다.

수정 승인 API의 잘못된 proposal hash·허용하지 않은 code 필드·명시 상승 동의 누락은 422, stale revision은 409다. 오류가 token을 소비하지 않는 것을 실제 DB로 확인했다. 승인 대기 상태의 Graph를 기존 checkpoint로 다시 구성한 뒤 수정 승인·이벤트 처리·최종 SSE를 검증했다. 이 재구성 시험은 프로세스 강제 종료/Kubernetes 재배포 시험과 구분한다.

## 검증 결과

[검증 요약](../reports/agentic-repair-verification-2026-10-01.json), [실제 Executor 8개 시나리오](../reports/agentic-repair-actual-executor-2026-10-01.json), [실제 수정 모델 역할](../reports/agentic-repair-real-role-2026-10-01.json)을 참고한다.

| 시험 | 결과 |
|---|---|
| 전체 API/Agent 회귀 + 격리 로컬 PostgreSQL | **712 passed**, 0 failed, 2 subtests, 299.43초 |
| 새 수정 단위/모델 middleware 검증 | 23 passed, 3.82초. 전체 suite에 포함되므로 합산하지 않음 |
| 실제 API/DB 수정 승인·재개·SSE | 전체 suite 내 새 시험 통과. stale/hash/권한·token 유지·checkpoint 재구성·멱등 replay 확인 |
| wheel 설치 smoke | checkout import 없음, role/prompt 11개, production builder 및 API/계획 smoke 통과 |
| 실제 qwen38-27b-nvfp4 수정 역할 | 유효한 실제 응답 1회. prompt 5871/completion 277 token. 코드 실패→binding 보정→후속 Operation→Finalize·terminal 완료, 13.501초 |

실제 Compose 시험은 진단용 고정 계획·테스트 전용 등록 함수로 오류를 주입하고 로컬 Executor/Jupyter를 실행했다. 원천 Parquet 파일이나 제품 Tool/Skill 파일을 변경하지 않았다. API/Run Worker/Event Worker/checkpointer/DB/Redis는 실제 서비스였다. --real을 제외한 수정 역할은 명시적인 결정적 테스트 대역이다.

| 시나리오 | Operation 수 | 확정 수정 시도 | 실제 최종 Executor 상태 | 승인 후 최종 결과까지 |
|---|---:|---:|---|---:|
| 1 binding 보정 | 2 | 1 | SUCCEEDED | 3.839초 |
| 2 실패 Tool 구현 수정 | 2 | 1 | SUCCEEDED | 4.593초 |
| 3 등록 자산 재계획·승인 | 2 | 1 | SUCCEEDED | 4.622초 |
| 4 실행별 함수 작성 | 2 | 1 | SUCCEEDED | 4.676초 |
| 사용자 거절 | 1 | 0 | CANCELLED | 3.073초 |
| 시도 한도 소진 | 3 | 2 | CANCELLED | 4.955초 |
| 수정 비활성화 | 1 | 0 | CANCELLED | 3.269초 |
| SINGLE 실패 전달 | 1 | 0 | FAILED | 2.144초 |

모든 실제 시나리오에서 이미 성공한 load는 다시 제출하지 않았다. 성공 경로는 마지막 Operation 성공 후 Finalize와 terminal을 확인했다. SINGLE에 후속 Operation/Finalize를 보내지 않았다. 과거 FAILED와 미실행 NOT_RUN 결과는 관찰 기록에 그대로 남았다.

단위 시험은 수준 상승 명시 승인, 수준 4에서 등록 자산 재계획의 불필요한 재승인 제외, readonly parameter 확인, 원본/근거 tamper, 미등록 자산, 변경된 signature/default, schema 우회, no-op 소스, 서비스 ceiling, 전송 오류 전파, 불완전 결과/인프라·취소 실패 제외, 승인 대기 terminal timeout, 같은 receipt 중복 처리, 서로 다른 Step에서의 Run 공통 시도 한도를 포함한다. 실제 Executor에서 timeout·프로세스 kill·DB 장애를 주입한 것으로 표시하지 않는다.

## 남은 범위와 해석 한계

수정 수준 1~4의 제안·검증·승인·실행 경계는 구현했다. 실제 모델 시험은 수준 1의 binding 보정이며 계획 생성은 고정 fixture다. 실제 모델이 수준 2~4의 다양한 업무 코드를 항상 올바르게 고친다고 검증한 것은 아니다. 전체 실제 LLM 계획→업무 실행의 의미 평가는 별도 시나리오가 필요하다. 이 시간 표는 단일 기능 시험이며 처리량/p95·이전 refactor 대비 성능 수치가 아니다.

AST/signature/의존성 검증은 semantic equivalence와 모든 반환값의 계약을 완전히 증명하지 않는다. Python sandbox도 아니다. 실패 Step의 부분 부작용은 반복될 수 있다. 현재 수정 확인 화면은 제안된 변경의 승인/거절을 지원하며 수정된 Python/계획을 화면에서 직접 입력받지 않는다. 모델의 전송 실패는 기존 복구 경계를 따르고 이번 기능이 모델/Worker 장애 복구를 대체하지 않는다.

수정 코드가 있는 Run의 workflow_eligible=false를 제공하지만 정식 Workflow 승격 API 이행은 후속이다. 모든 후보 거절 후의 신규 자유 계획, PVC dataset catalog/scope/metadata·project_memory·pgvector Workflow 관리·Gaia adapter·첨부/VLM·Artifact 등록 결정·legacy 미사용 코드 정리는 남았다. 총 모델 입력 예산·대규모 수정 코드의 CPU/메모리·다중 사용자 처리량과 1주 이상 실행은 측정하지 않았다.

기존 사용자 체크아웃을 보존하고 파생 브랜치에 구현·실패 기록·최종 검증을 커밋한다. 베이스 병합·원격 push·배포는 수행하지 않았다. 개발 책임·API·설정 예시는 [오류 수정 Runtime 안내](../agentic-execution-repair.md)를 따른다.
