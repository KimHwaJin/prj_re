# 후속 설명·보고서 수정의 LLM 비용 비교

보고서 수정의 평균 응답 시간은 **102.06초 → 60.68초(40.5% 감소)**였다. 결과 설명은 **52.96초 → 58.46초(10.4% 증가)**였다. 이번 변경으로 두 요청에서 불필요한 메타데이터 조회는 제거했지만, 설명 답변의 시간 병목을 해결했다고 판단하지 않는다.

## 무엇을 바꿨는가

기존 Conversation Agent는 단순 설명에도 전체 Workflow JSON Schema, 계획 작성 지침과 Skill 조회 도구를 제공했다. 실제 모델은 기존 분석 결과를 설명하면서도 `read_skill`/`search_tools`를 두 번 호출했다. 이 도구들은 실행 함수가 아니라 등록 자산의 설명을 읽는 메타데이터 도구다. 각 호출 뒤 모델을 다시 호출하면서 입력 문맥도 커졌다.

현재는 같은 `create_agent` 루프의 첫 응답에서 답변 또는 계획 준비를 결정한다. 답변에는 기존 완료 분석 근거와 짧은 답변 지침을 제공한다. 새 실행이 필요한 경우에만 내부 `kind=planning`과 등록 Skill ID를 선택한다. `PlanningContractMiddleware`가 표준 ToolNode로 Skill을 조회하고 다음 모델 요청에 상세 계획 지침·Workflow Schema·메타데이터 도구를 제공한다. 별도 분류 모델 호출은 추가하지 않았다. 사용자 승인, Workflow/입력 검증과 Executor 제출 경계는 유지한다.

## 동일 입력 A/B 결과

같은 private 입력 snapshot을 각 버전에서 두 번 실행했다. 각 숫자는 두 회 평균이며 입력 토큰은 해당 요청의 모든 모델 호출을 합산했다.

| 요청 | 이전 시간 | 변경 후 시간 | 시간 변화 | 모델 호출 | 메타데이터 호출 | 누적 입력 토큰 |
|---|---:|---:|---:|---:|---:|---:|
| 결과 설명 | 52.96초 | 58.46초 | 10.4% 증가 | 4 → 3 | 2 → 0 | 46,818 → 25,905 (44.7% 감소) |
| Markdown 보고서 수정 | 102.06초 | 60.68초 | 40.5% 감소 | 5 → 2 | 2 → 0 | 69,131 → 19,871 (71.3% 감소) |

| 요청 | 이전 1회 / 2회 | 변경 후 1회 / 2회 |
|---|---|---|
| 결과 설명 | 56.33 / 49.59초 | 66.08 / 50.84초 |
| 보고서 수정 | 108.68 / 95.43초 | 64.31 / 57.06초 |

설명 출력 토큰은 평균 4,031 → 4,852로 늘었고, 보고서 수정은 7,440 → 5,270으로 줄었다. 출력 길이와 선택 facts가 달라 모델 호출 수만으로 시간 개선을 계산할 수 없다. 두 버전 8회 모두 최종 답변의 source Run·Step·fact selector 검증을 통과했다. 변경 후 4회 모두 전체 Workflow Schema를 받지 않고 메타데이터 호출 없이 종료했다.

## 아직 시간을 쓰는 곳

변경 후 설명 두 회는 각각 모델을 세 번 호출했다. 계측된 두 번째 설명은 첫 응답 20.55초와 정정 응답 14.59초가 `copied_numeric_text`로 거절되고, 마지막 15.67초 응답이 통과했다. 모델이 수치는 facts로 선택하고 본문에는 정성 설명만 작성해야 하는 계약을 지키지 못한 것이다. Skill 조회나 DB 대기가 원인이 아니었다.

보고서 수정도 두 회 모두 첫 응답이 같은 수치 본문 규칙에 걸려 모델을 한 번 더 호출했다. 숫자 검증을 느슨하게 풀어 시간을 줄이지 않았다. 다음 성능 항목은 실제 수치를 서버에서 붙이는 보장을 유지하면서 **모델이 작성할 출력 계약과 정정 횟수**를 줄이는 방향이다.

## 측정 조건과 한계

- 기준: `59eec1f66f04bb63c21abd6720b0d03d76c19b0d`, 후보: `feature/conversation-performance`. 이전 044 실연계의 동일한 요청·history·완료 분석 관찰을 private snapshot으로 고정했다. 입력 hash는 [측정 JSON](conversation-performance-2026-10-02.json)에 남겼다.
- 실제 모델 `qwen38-27b-nvfp4`, temperature 0.2, thinking false. 변경 전·후를 교차 순서로 실행했다. Agent 구성·warmup은 시간에서 제외하고 `ainvoke`와 grounding 검증 종료까지 측정했다.
- API 접수, DB, Worker 대기, SSE 전달, Executor 실행은 이 측정에 포함하지 않았다. 새 실행 제출은 0건이다. 50/100명 부하테스트나 서비스 전체 처리량의 증명이 아니다.
- 모델은 공유 서버 자원이며 부하를 통제하지 못했다. 표본은 조건별 2회이므로 일반적인 지연 개선율이나 통계적 유의성을 주장하지 않는다.
- 앞선 prompt 축소만 적용한 실험은 설명 64.84 → 93.69초로 악화됐고 메타데이터 조회도 남았다. 이를 폐기하고 선택 경계를 구현했다. 그 실험은 위 평균에 섞지 않았다.
- 근거 검증은 실제 원래 값의 삽입을 보장한다. 수치의 의미를 해석하는 문장이 정답임을 보장하지 않는다. 실제 답변에는 분포·데이터 품질을 과도하게 해석하거나 근거 footer를 중복 작성하는 경우가 남았다. 이를 이번 성능 개선의 품질 성과로 계산하지 않는다.

## 재현 도구

`scripts/benchmarks/conversation/run.py`는 private 설정과 입력 파일을 받아 production builder를 실행한다. 측정 JSON에는 토큰·시간·검증 분류 등 계측 정보만 저장하며 프롬프트, 원문 답변, 인증 정보는 저장하지 않는다. 원문 답변을 검토하려면 `--private-answer-output`을 Git 밖 경로로 별도 지정한다.

```sh
PYTHONPATH=/path/to/revision/src python scripts/benchmarks/conversation/run.py \
  --settings-file /private/path/settings.json \
  --input-file /private/path/fixed-input.json \
  --output /private/path/measurements.json \
  --label before --case explanation --iteration 1
```

private 입력 파일은 `source_session`과 `cases`를 가진다. 각 case에는 `name`(explanation/report_revision), Conversation의 `payload`, 실제 완료 분석 `analysis`, 선택적인 `project_system_prompt`가 있다. 설정은 중앙 `load_settings(config=..., environ={})`로 읽는다. `--host-alias hostname=IP`는 호출한 프로세스의 DNS 해석에만 적용한다. before/after는 동일 스크립트에서 `PYTHONPATH`만 각각의 source archive로 지정한다.

## 구현 검증

API·Agent 전체 회귀 890개와 subtest 2개가 통과했다(391.40초). 핵심 관련 73개 통과는 전체 회귀에 포함되는 별도 확인이며 합산하지 않는다. 실제 신규 계획 smoke는 31.86초에 Skill 선택 후 data_load/profile_data/compute_statistics/detect_outliers 계획 한 개를 검증했다. 계획 단계에는 추가 search_tools 조회 3회가 있어 신규 계획의 시간 개선을 주장하지 않는다. Executor에는 제출하지 않았다. 최종 wheel의 상세 prompt·middleware 포함, 12개 Agent 선언과 source checkout 없는 설치 실행도 확인했다. 공개 OpenAPI 36개 path의 hash는 변경 전·후 같았다.
