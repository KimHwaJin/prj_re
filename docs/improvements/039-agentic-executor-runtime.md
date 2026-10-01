# 039. 승인 계획의 실제 Executor 실행·관찰·후속 Operation·리포트

> 후속 통합 기록 (2026-10-01): 이 작업은 038~046과 함께 `feature/refactor-base`에 fast-forward 반영되고 `origin=KimHwaJin/prj_re`에 게시되었다. 파생 브랜치도 보존·게시했으며 원격 SHA 일치를 확인했다. 아래의 미병합·push 미수행 문구는 당시 완료 시점 기록이다. 배포·후속 기능 상태는 그대로다. [통합·검증 기록](046-api-workflow-reference.md).

| 항목 | 내용 |
|---|---|
| 상태 | 실행 경계·결정 HITL·리포트 구현, 실제 서비스 및 전체 회귀/wheel 검증 완료 |
| 시작일 / 완료일 | 2026-09-30 / 2026-09-30 |
| 브랜치 | feature/agentic-executor-runtime |
| 기준 commit | 2f54b094354c9ed02eff56a1fa655eb0843d662c — 038 브랜치에서 분기 |
| 구현 commit | 065ec2599a1e6e3762461fe127cf9a909b2c49f6 |
| 배포 상태 | 기존 Compose 유지, 임시 테스트 서버 종료, 베이스 병합·원격 push 미수행 |

## 문제와 변경

038은 모델이 계획을 제안하고 사용자가 수정·승인한 snapshot을 저장하는 데서 끝났다. 실제 Executor를 호출하거나 파일·출력을 관찰하지 않았으며 이벤트 Worker도 이전 Graph를 생성했다. 따라서 결과에 따라 파라미터를 결정하거나 후속 Tool을 실행하는 새 설계는 연결되지 않았다.

| 영역 | 이전 | 이번 구현 |
|---|---|---|
| 승인 이후 | plan_approved 종료 | 실제 최초 제출→Operation 이벤트→후속 제출→Finalize→terminal |
| 코드 | 승인 소스 내부 저장 | 함수 원문 보존, 인자·커널 변수 연결, 관찰 코드 추가 |
| 데이터 객체 | 실행하지 않음 | Jupyter 커널에 유지, Agent에는 제한된 실제 관찰만 전달 |
| 조건·파라미터 | 계약만 존재 | 완료한 Step 근거로 승인 범위의 decision 판단·schema 검증 |
| HITL | plan_review | 결과 기반 decision_review와 approve_decisions 추가 |
| 완료 | 승인 단계 성공 | 최종 execution.completed 후 최종 결과 저장 |
| Report | 미실행 | create_agent 해석과 서버가 직접 만드는 실제 수치 근거 표 |
| 이벤트 Worker | 이전 Graph | 새 PlanningRuntime/실행 Graph, 임베디드 API Graph 공유 |
| 자원 | 별도 Worker Graph/cache/checkpointer | 임베디드 Worker는 API의 Graph/cache/checkpointer를 함께 사용 |

기존 HTTP 클라이언트·멱등성·장기 interrupt·PostgreSQL Run 큐·세션 소유권·receipt·SSE 저장 경계를 재사용한다. 이번에 만든 기능으로 다시 계산하지 않는다. API CRUD/Worker SQL pool이 모두 단일 풀로 통합된 것은 아니다.

Executor 대기는 Run 실행 슬롯과 계속 열린 DB 연결을 차지하지 않는다. 이벤트 처리는 Event Worker의 dispatch 동시성으로 다시 실행하며, 모델 판단/리포트 생성 중에는 해당 슬롯을 사용한다. API의 새 요청과 사용자 HITL 응답은 기존 단일 POST이며 공개 Run ID를 유지한다.

## 실제 시험에서 발견하고 고친 사항

- Graph receive가 생성한 task_id를 state schema가 받지 않아 최초 제출에 사용할 ID가 없었다. TypedDict에 추가했고 단위 실행·실제 API/Worker 경로를 검증했다.
- 완료 후 같은 세션에서 새 Run을 시작할 때 astream의 초기 입력 echo에 이전 task_id/events가 남아 있었다. 이를 새 Run에 연결하면 unique graph_task_id 오류가 생겼다. 새 요청의 receive receipt가 기록된 상태부터 서비스 DB에 저장하도록 수정했다. 실제 DB 테스트와 최종 mock+실제 Executor 시험에서 다음 Run이 계획 대기까지 실행됨을 확인했다.
- 첫 실제 리포트가 성공한 Step의 ID 대신 다른 ID를 근거로 써서 terminal 이벤트 처리가 재시도되었다. 공통 create_agent JSON middleware의 응답 검증 hook으로 정확한 허용 ID와 수정 피드백을 전달한다.
- 리포트의 자연어 JSON이 형식 검증을 통과해도 숫자를 잘못 썼다. 실제 관찰의 결측률은 0.0045인데 모델은 근거 없는 450개 셀을 덧붙였고, 프롬프트만 강화한 별도 시험에서는 y의 75% 값을 38 대신 25로 전사했다. 숫자 표를 모델이 다시 쓰는 구조를 제거했다. 서버가 검증한 출력에서 수치 표를 직접 렌더링하고 모델은 해석문만 작성한다.
- 모델이 숫자 포함 해석문을 계속 내면 두 번의 검증 후 evidence_only 결과로 명확히 표시한다. 이미 성공한 실행이 해석문 때문에 세션을 계속 잠그지 않는다. 근거 표만 제공한 것을 정상 모델 해석문 생성으로 기록하지 않는다. 구체적으로 잘못된 숫자 구간을 알려준 뒤의 별도 실제 모델 Report 역할 시험에서는 해석문 검증이 통과했다.
- 실제 모델의 빈 응답과 잘못 연결한 decision 의존성도 관찰했다. 빈 응답은 기존 제한된 재시도 후 recovery_required로 남았다. 의존성 규칙과 예제를 conversation prompt에 보완해 다음 실제 시험은 통과했다. 모델의 모든 계획·해석 오류가 해결됐다고 주장하지 않는다.

검증 전 실패 기록은 reports에 보존한다. 기존 업무 환경의 user_request 오류를 재현하거나 원인을 이번 변경 하나로 확정한 시험은 아니다.

## 최종 검증 결과

[검증 요약](../reports/agentic-executor-verification-2026-09-30.json), [실제 모델 전체 연계](../reports/agentic-executor-real-validation-2026-09-30.json), [mock 모델+실제 Executor](../reports/agentic-executor-mock-validation-2026-09-30.json), [실제 Report 역할 재검증](../reports/agentic-report-real-review-2026-09-30.json)을 구분한다.

| 시험 | 결과 |
|---|---|
| 전체 API/Agent 회귀 | **688 passed**, 0 failed, 2 subtests, 292.25초 |
| 변경 구간 영향 시험 | 45 passed, 4.38초. 전체 suite와 겹치므로 수를 합산하지 않음 |
| wheel 설치 경로 | 소스 checkout import 없음, 역할별 prompt/builder 10개, API·승인 Graph smoke 통과 |
| mock 모델 + 실제 Executor/Jupyter | 계획 0.397초, 승인~최종 결과 5.503초, Operation 2개, Finalize·terminal 완료 |
| 실제 qwen38-27b-nvfp4 + 실제 Executor/Jupyter | 계획 39.299초, 승인~Executor 대기 0.466초, 승인~최종 결과 44.398초 |
| 실제 모델 호출 | planning 6, execution review 1, report 2. ChatOpenAI span으로 역할 구분 |
| Phoenix | 실제 trace 14개 조회 확인 |
| 실제 Report 역할만 재검증 | 20.08초, 해석문 검증 통과, 실제 관찰 수치 직접 표시 확인 |

전체 회귀 첫 실행은 678 passed/7 failed였다. 쿼리·Graph 변경에 대한 이전 mock과 새 테스트 풀 격리를 고친 뒤 두 번째는 684 passed/1 failed였다. 마지막 실패는 Graph provider 테스트가 submit 비활성화 설정을 물려받은 데서 발생했다. 실제 Worker가 주입하는 Executor port 및 명시적 실행 설정으로 테스트를 수정했다. 최종 전체 suite에는 리포트 수치·fallback 관련 추가 테스트까지 포함했다. 이전 실행 통과 수를 최종 수와 합산하지 않는다.

실제 모델 전체 연계와 별도 Report 역할 재검증은 같은 시험이 아니다. 전체 연계의 report.status는 evidence_only였다. 잘못된 숫자 구간을 명확히 알려주는 피드백을 추가한 뒤 **Report 역할만 다시 실행**한 결과가 해석문 검증 통과다. 마지막 피드백 변경 후 모든 전체 연계를 다시 실행한 것으로 표시하지 않는다. 실제 전체 연계의 근거 표에는 y의 75% 값 38.0이 정확히 표시됐다.

실제 데이터는 Jupyter의 기존 df_nce_long_format.parquet였다. 10,000행·8열을 data_load→profile_data→compute_statistics→detect_outliers로 실행했고, 기초 실행 결과를 본 다음 IQR을 선택해 두 번째 Operation을 제출했다. 관찰된 결측률 0.0045, 이상치 109행·비율 0.0109를 반환했다. 새 예제 데이터를 기존 원천 파일에 덮어쓰지 않았다.

같은 세션의 Executor 대기 중 새 요청은 409, 완료 후 접수는 202였다. 최종 mock+실제 Executor 시험에서는 다음 invocation이 waiting_input까지 실행됐고 이전 task_id를 재사용하지 않았다. 실제 모델 시험은 완료 후 접수까지만 확인하고 추가 Run을 취소했다.

자동 테스트에는 SINGLE의 자동 terminal/Finalize 미호출, 실패 시 수정하지 않음, MULTI의 조건부 skip, Operation sequence/version, 사용자 decision 검증·token 유지, terminal timeout의 확인 화면 정리, checksum 변조 거절, 동일 receipt 재전달의 중복 제출 방지, PATH 파일 불변성, 공유 Graph의 추가 풀 생성 방지, 해석 실패의 근거 표 fallback을 포함한다. SINGLE 실패/timeout/변조는 명시적 테스트 대역이며 실제 Executor 장애 주입 시험이라고 표시하지 않는다.

## 한계와 다음 범위

실제 실행의 repair_level/attempt ceiling은 0이다. 단계 1~4 자동 Tool 수정·자유 코드 추가는 다음 구현 대상이며 선택을 허용해놓고 실행한 척하지 않는다. 실패한 MULTI Operation은 계속 가능한 상태에서 Cancel 후 terminal을 기다린다. Artifact POST·노트북 리포트 셀 등록 시점은 사용자가 추후 결정하기로 했으므로 호출하지 않는다.

PVC 데이터 catalog/metadata·scope별 쓰기 root, project_memory, pgvector Workflow 관리·추천, Gaia 등록 adapter, 첨부·VLM, 기존 CLI 이행·미사용 코드 제거는 후속이다. Agent가 전처리 데이터를 저장·공유 관리하는 기능이 이번에 완성된 것은 아니다. Tool 출력 품질과 자연어 해석은 별도의 의미 검증이 필요하다. 관찰 제한은 Step별 제한이며 큰 계획의 총 모델 입력 예산은 후속 성능 검토 대상이다.

1주 이상 실행, Kubernetes 재배포/스케일아웃, 동시 사용자 처리량은 측정하지 않았다. 각 Operation timeout 기본 600초를 장기 작업에 맞게 조정해야 한다. 이전 벤치마크와 이번 단일 실제 모델 시간을 성능 개선 수치로 비교하지 않는다. 실제 LLM 탐색·JSON 생성·결과 판단·리포트 시간은 별도로 발생한다.

구현 책임과 호출/설정 가이드는 [Executor Runtime 개발 안내](../agentic-executor-runtime.md)를 따른다. 원래 feature/total_merge_v1의 사용자 수정은 보존했고 이 브랜치에 코드·검증 기록을 커밋한다. 베이스 병합·push·배포는 이번 완료 범위에 포함하지 않는다.
