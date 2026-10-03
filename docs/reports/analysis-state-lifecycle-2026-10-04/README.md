# 분석 상태 수명과 읽기 경계 검증 — 2026-10-04

**새 FAQ 요청에 이전 코드 제출 본문·Operation ID·오류가 남는 것을 수정했다. 각 노드가 읽는 상태도 77개 전체에서 필요한 1~26개로 제한했다. 응답 시간/처리량 개선을 측정한 작업은 아니다.**

## 문제와 변경

기존 receive의 Run 초기화는 수작업 목록이었다. `execution_command`, `execution_error`, `execution_phase`, `executor_operation_id`, `executor_version`, `executor_wait_phase`, `planning_activity_id`, `routing_result`, `submitted_steps`의 초기화가 누락됐다. 실제 완료 분석 뒤 FAQ만 두 번 물어도 이전 8,472bytes의 제출 body와 Operation ID, `report` 단계가 최신 상태에 남았다.

`state.py`에 77개 평면 채널을 수명/책임별 TypedDict로 선언했다. `planning/lifecycle.py`는 매 새 요청의 Run 기본값을 한 곳에서 생성하고, receive는 새 ID·이력·현재 소유자의 제한된 분석 근거를 덧붙인다. 새 요청에는 본문을 `{}`로 비우고 오류/Operation 정보도 초기화한다.

23개 노드는 `input_schema`로 필요한 채널만 읽는다. Conversation 13개, plan revision 22개, 실행 review 18개, report 26개, repair proposal 24개다. 입력 타입은 같은 전체 채널 타입에서 파생한다. public projection은 기존 계약을 사용하며 input_schema 자체를 권한/SSE redaction으로 쓰지 않는다.

HITL/Executor resume 또는 복구용 `ainvoke(None)`에서는 receive를 다시 실행하지 않는다. 완료 직후에도 승인 source·제출 body·receipt를 유지하므로 서비스 projection/응답 유실을 복구할 수 있다. 다음 Run을 받을 때만 비운다. 같은 session의 WAITING_EXECUTOR 입력 잠금·Run/Command 소유권·모델/prompt pin·durability sync·pool/슬롯은 유지했다.

## 실제 PostgreSQL 저장량 비교

- 전: 실제 `2622552` graph.py와 execution/nodes.py를 독립 진단 프로세스에 로드했다. checkout은 바꾸지 않았다.
- 후: 현재 작업 소스. 정확한 runtime 파일 hash는 `candidate.json`과 `comparison.json`에 기록했다.
- 전후 각각 **1개 6행 Parquet 분석 → 같은 session FAQ 2개**다. 모델은 즉시 응답하는 mock, Executor는 프로세스 내 double이며 실제 등록 Tool Python을 실행했다. 실제 PostgreSQL을 사용했다. 각 분석의 MULTI submit/continue/finalize double 호출은 동일한 3회, 정상 결과·완료 Step·관찰 수치·판단값·report 상태가 동일했다. 후속 두 번은 Executor를 추가 호출하지 않았다.
- API/Worker/HTTP/Redis/SSE 부하, 5초 모델, 수백 GB 출력, 장기/반복 repair를 측정한 것이 아니다. 이전 063의 5초 모델 HTTP 결과와 같은 모집단으로 합치지 않는다.

| 구간 | checkpoint 수(전후 동일) | 최신 JSON+참조 blob KiB 전→후 | 과거 포함 논리 누계 KiB 전→후 | 누계 변화 |
|---|---:|---:|---:|---:|
| analysis_completed | 27 | 67.73 → 68.02 | 582.03 → 596.47 | +2.48% |
| followup_1 | 31 | 30.34 → 22.65 | 663.30 → 679.09 | +2.38% |
| followup_2 | 35 | 30.51 → 22.76 | 745.20 → 762.26 | +2.29% |

후속 최신 snapshot은 약 25% 작아졌다. `execution_command` JSON은 8,472→2bytes(`{}`)였고, Operation ID/이전 실행 단계는 제거됐다. 제한된 실제 분석 근거·대화 이력·커널 선택은 유지했다. **전체 누계는 2~2.5% 증가**했다. 기존에 비워 두던 초기 채널도 명시적으로 작성해 version metadata/중간 writes가 늘었고, 원본은 삭제하지 않았기 때문이다. 경로/UUID/version 문자열 길이의 작은 차이도 있으므로 모든 차이를 단일 필드 비용으로 단정하지 않는다.

노드별 input_schema만으로 version metadata가 작아지지는 않았다. 이 환경의 최신 `versions_seen`에는 각 실행 노드의 trigger와 `__interrupt__` 읽기 버전 목록이 유지됐고, `channel_versions`/`versions_seen` 구성값은 줄지 않았다. input 필드 수를 blob decode·prompt 길이·DB 저장량 감소율로 바꾸어 해석하지 않는다.

논리 누계는 checkpoint JSON·metadata JSON·고유 blob·write payload의 byte 합이다. 최신 값은 최신 checkpoint JSON·metadata JSON과 그 버전이 참조하는 blob의 합이며 pending writes/Python heap은 제외한다. index/WAL/페이지/row key는 두 지표에서 제외한다. 과거 이력은 그대로 조회 가능하며 `comparison.json`은 원본 행에서 합계를 독립 재계산한 결과다.

## 기능과 호환 검사

- 분석 Agent 전체 + package 경계: **318 passed**, `agent-regression.txt`. 계획 수정/자유 코드·승인·판단·repair 수준/재시도·근거 답변·프로젝트 메모리·동기 I/O 경계 등을 포함한다. 기존 내부 `checkpointer=False` 역할의 durability 경고는 숨기지 않았다.
- 실제 PostgreSQL/API 회귀: **50 passed, skip 0**, `postgres-regression.txt/xml`. 초기/사용자 resume receipt 복구·풀 반환·계획/판단/repair API·세션 동시 입력/소유권을 검사했다.
- 기존 전체 읽기 metadata로 만든 계획·Executor·판단·repair 대기를 pool 종료/재생성 후 새 입력 범위로 재개하는 네 검사가 포함된다. 저장 값 그대로 복원, 같은 실행/승인, 중복 receipt 미재실행, 성공 load 미재실행, 모델 추가 호출 없음, connection 반환을 확인했다.
- 별도로 실제 이전 `2622552` graph/nodes 소스를 넣어 같은 네 대기를 검사했다. 결과와 source hash는 `exact-reference-compatibility.txt/json`, 검사 때 사용한 로컬 controller는 `exact-reference-controller.py`다(이번 전용 fixture의 경로와 가짜 인증값 포함). fixture 기반 검사와 실제 이전 소스 검사를 구분한다.
- 첫 넓은 회귀에서는 publish_review 입력 스키마에 public_events가 누락돼 실패했고, 범위를 보완한 뒤 위 전체 검사가 통과했다. 신규 PG 검사에서는 fixture import 경로 오류를 바로잡았다. 이 실패를 정상 결과로 집계하지 않았다.

### 검사 종료 경고와 남은 한계

50개 회귀 종료 시 `Future exception was never retrieved / unexpected connection_lost() call`이 한 번 출력됐다. 설치된 asyncpg의 SSL upgrade 협상용 `connection_lost`에서 생성되는 예외인 것은 소스로 확인했다([asyncpg 소스](https://github.com/MagicStack/asyncpg/blob/master/asyncpg/connect_utils.py)). 어떤 개별 테스트/취소 타이밍이 유발했는지는 확정하지 못했다. 변경 관련 pool·상태 재개 8개를 asyncio debug로 분리 실행한 결과 **8 passed, 해당 경고 미재현**이었다(`connection-warning-targeted.txt`). 예외를 숨기거나 runtime에 우회 처리를 추가하지 않았다. 운영 품질 확인 때 재검토할 항목으로 남긴다.

이 검증이 오래된 설문형 그래프의 이행, 실제 Executor/SSO/LLM 연계, 장기 작업, 모든 모델/출력 크기, 운영 Pod 배포를 보장하지 않는다. 설정/API/SSE/프롬프트/호출 수/Workflow·Skill·Tool 원본은 바꾸지 않았다. 테스트 전용 PostgreSQL·Redis 정리와 기존 컨테이너 보존은 `cleanup.json`을 따른다.

## 재현과 파일

로컬 **폐기 가능한** `agentic_checkpoint_test`를 준비하고 아래 env에 DSN을 넣는다. dev 의존성과 pandas/pyarrow가 설치된 Python을 사용한다. 진단은 임의 운영 DB를 허용하지 않으며 새 thread를 생성한다. 함수 fixture 및 SQL 집계는 테스트용이다.

```sh
export DTEST_STATE_PROFILE_DSN='<로컬 agentic_checkpoint_test DSN>'
python scripts/diagnostics/profile_analysis_state.py --variant reference --reference-revision 2622552 --fixture-dir /tmp/state-reference-tools --output /tmp/state-reference.json
python scripts/diagnostics/profile_analysis_state.py --variant candidate --fixture-dir /tmp/state-candidate-tools --output /tmp/state-candidate.json
```

모든 output/fixture-dir는 새 경로여야 한다. 새 경로를 다시 골라 반복할 수 있다. source revision은 로컬 Git에 있어야 하며 reference는 그래프와 실행 노드만 이전 버전으로 읽고 나머지 변경하지 않은 runtime/자산/fixture를 사용한다. 원본 `reference.json`과 `candidate.json`에는 SQL·크기·노드 입력 수·결과 대조가 있고 제출 코드 본문은 포함하지 않는다. `comparison.json`은 요약·원본 SHA, `final-verification.json`은 최종 검산이고 `artifact-sha256.json`은 저장한 원본/검사 파일 해시다.

[개발 안내](../../agent-development/analysis-state-lifecycle.md), [개선 기록](../../improvements/064-analysis-state-lifecycle.md). feature/analysis-state-lifecycle에 구현하며 베이스 미병합·미푸시·미배포다.
