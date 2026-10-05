# 최종 승인 계획·보고서 정합성 검증 — 2026-10-05

최종 승인 파라미터와 사용자 제외를 report 및 후속 모델 문맥에 전달하도록 수정했다. 실제 `max_val + x` 요청을 `max_val`만으로 승인했을 때 실제 통계·보고서·후속 설명·Markdown 재작성 모두 해당 범위를 전달받았다. `detect_outliers`를 제외한 사례에서도 실행하지 않았으며, 보고서/후속 답변이 이를 실패·누락이 아닌 사용자 제외로 설명했다.

## 조건

`feature/approved-plan-report-context`, 기준 `9a52d3f`. 실제 qwen38-27b-nvfp4, loopback API·Redis/Worker·임시 PostgreSQL17(runtime/checkpoint/Store), 로컬 Executor/Jupyter default kernel, 등록된 default-nce Parquet다. 모델 응답·Tool 실행은 대체하지 않았다. 직원 검증만 synthetic verdict fixture이며 쿠키·CSRF·자동 사용자/프로젝트 생성은 실제 서비스다. Phoenix는 껐다. 사내 SDK/브라우저/배포 검증은 아니다.

진단 프로세스에서만 기존 승인된 model host alias를 적용했다. 각 trial은 별도 session·임시 DB/namespace를 사용했고 종료 후 소유 container·임시 자원을 정리했다. 일부 진단 trial은 서로 겹쳐 실행했으며 latency/처리량 비교로 사용하지 않는다. 기존 Compose·Executor 이력·콘솔18102/기존 DB는 보존했다. 최신 코드 콘솔18103/DB53604는 별도로 유지한다.

## 최종 검증

| 항목 | 컬럼 수정 | 컬럼 수정 + Tool 제외 |
|---|---|---|
| 최초 요청 | max_val + x 통계 | max_val + x 통계 + IQR 탐지 |
| 최종 승인 | max_val 통계 | max_val 통계, detect_outliers 제외 |
| 실제 통계 대상 | max_val만 | max_val만 |
| 보고서 모델 입력 | 최종 columns=max_val | 최종 columns=max_val, 탐지 EXCLUDED_BY_USER |
| 완료 checkpoint | report 입력과 같은 execution_scope | report 입력과 같은 execution_scope |
| 후속 설명·보고서 재작성 | 실제 모델 전달 scope 동일 | 실제 모델 전달 scope 동일 |
| 후속 Executor 추가 제출 | 없음 | 없음 |
| 최종 구조 검사 | 15/15 통과 | 17/17 통과 |

모델 호출 직전 BaseChatOpenAI._agenerate에서 최종 HumanMessage를 관측했다. handler 인수나 반환값을 바꾸지 않는다. 이 hook은 현재 고정된 langchain-openai 버전을 대상으로 한 진단이며 서비스 실행기에는 설치하지 않는다. 최종두 trial은 실제 분석2개·후속 답변4개가 완료됐다. 실제 Executor POST는 분석당1개이고 후속 설명·재작성은0개였다. [검산 JSON](result.json)은 시도별 성공/실패와 구조 검사만 담으며 원본 데이터·모델 prompt/reply·Tool 코드는 넣지 않는다.

보고서 본문도 수동 검토했다. Tool 제외 trial은 "제외된 항목은 실패나 누락이 아닌, 의도적인 범위 축소"로 설명했다. 첫 컬럼 수정 trial의 후속 보고서는 max_val을 분석 대상으로 쓰고 x를 미산출 요구로 취급하지 않았다. 이것을 모든 자연어·모델의 의미 정확성 증명으로 일반화하지 않는다.

## 실패를 보존한 과정

1. 초기 컬럼/Tool 제외 trial의 분석·후속 답변은 success였고 report 입력·checkpoint 범위도 일치했다. 그러나 PromptJsonMiddleware handler를 관측한 위치가 SessionAnalysisMiddleware 삽입 전이어서 두 후속 입력 검사가 false였다. 원본 결과를 남기고 관측 위치를 실제 모델 호출 경계로 수정했다. 최종 컬럼 수정15개 검사가 통과했다.
2. 수정한 계측으로 Tool 제외를 다시 시험했을 때, 모델이 workflow_input 바인딩에 편집 가능한 Tool parameter_controls를 중복 선언했다. 세 번의 JSON 교정 후에도 이를 고치지 못해 승인 전에 recovery_required로 끝났다. Executor 요청은0개였다. validator를 완화하거나 모델 계획을 임의 수정하지 않았다.
3. 같은 요청/설정으로 한 번 더 시험해 유효한 계획을 받았고, 최종 Tool 제외17개 검사가 통과했다. 실패를 성공으로 덮어쓰거나 안정적인 성공률이라고 주장하지 않는다. 이 계획 생성 계약 준수/교정 품질은 별도 후속이다.

전체다섯 trial 중 네 분석이 완료되고 한 trial이 승인 전 실패했다. 완료 분석의 후속 답변은 각각두 개다. 초기 관측 실패는 서비스 실패와 구분한다. 원본 private0600 파일은 /private/tmp/approved-scope-090-{edit,exclude,edit-final,exclude-final,exclude-retry}.json에 보존한다. Git에는 allowlist 요약만 넣었다.

## 회귀·패키지·재현

- Analysis Agent345개 통과. 집중65개·신규4개는 그 안에 포함되며 중복 합산하지 않는다.
- 진단/콘솔 모드·모델 관측13개 통과.
- setuptools.build_meta로 wheel 생성, 신규 analysis_scope와 report/conversation prompt 포함 확인. build/pip CLI가 해당 venv에 없으므로 설치된 backend를 직접 사용했다. 추가 의존성 설치 없음.

```sh
PYTHONPATH=src /Users/a10054/SKAX_PROJECT/dtest-agent/.venv/bin/python -m pytest -q src/agent_service/agents/analysis/tests
PYTHONPATH=src /Users/a10054/SKAX_PROJECT/dtest-agent/.venv/bin/python -m pytest -q scripts/diagnostics/tests/test_real_model_scope_observation.py scripts/diagnostics/tests/test_console_modes.py
```

실제 검증은 verify_real_model_parameters.py의 --scenario edit / --scenario exclude를 사용한다. --model-env에는 기존 private 설정, --executor-shared-root에는 실제 공유 저장소를 넣고 사용하지 않는 --port/--db-port를 지정한다. 현재 최신 콘솔18103/53604를 진단 trial에 중복 지정하지 않는다. 기존 모델 호출 횟수 최적화는 이번에 다루지 않았다. 최종컬럼 trial 모델10호출/교정2회, Tool 제외 trial11호출/교정3회이며 기능 검증 관측값이지 성능 개선 수치가 아니다.

## 남은 품질 검토

최종 승인 범위 누락은 수정했다. 그러나 **DataFrame head의 출력 제한을 전체 통계 산출의 표본 제한으로 혼동하는 해석 문장**이 남는다. 실제 통계 Tool은 로드된 DataFrame을 사용하지만 모델이 "산출된 통계량이 전체를 대표한다고 단정할 수 없다"고 head 제한과 함께 설명한 사례가 있다. 관찰의 표시 제한·입력 데이터 범위·분석 방법의 한계를 명확히 나누는 다음 Agent 품질 검토다. 이번 구조 검산은 이런 정성적 오류를 자동 탐지하지 않는다.

계획 생성 계약 실패, 정성/인과 해석, 사내 SDK/Gaia/배포, Registry/보고서 Artifact/Workflow CRUD, 모델 호출 수·지속 부하·운영 후순위는 남긴다. 공개 API/SSE·DDL·실행 동시성을 바꾼 작업이 아니며 베이스 미병합·미푸시·운영 미배포다.
