# 050. 후속 답변의 짧은 근거 선택과 수치 분리 계약

작업 브랜치: `feature/grounded-answer-efficiency`

출발 commit: `688a051228fbc8c124642072a9049fcb1990fc15`

작업일: 2026-10-02

## 문제

049로 불필요한 Skill 조회는 없앴지만, 모델이 설명 본문에 수치를 작성해 grounding 검증에서 거절되고 답변 전체를 다시 작성했다. private 중간 응답을 수집해 보니 제목 번호뿐 아니라 행·열 개수, 값의 범위, IQR의 일반적인 수학적 비율도 직접 작성했다. 설명 첫 진단은 세 모델 호출 중 앞 두 개가 거절됐다. 단순히 번호 제목만 허용하면 실제 수치의 복사 문제는 남는다.

또한 모든 facts에 Step/path 객체를 반복 생성하고, 완료 분석의 원본 관찰과 서버가 만든 보고서 수치 표를 중복 전달했다. 재작성 시 이 내용과 이전 응답이 다시 입력·출력돼 추론 비용이 커졌다.

## 변경

Conversation은 같은 create_agent와 기존 미들웨어 경계를 사용한다. 새 분류 모델이나 Executor 작업을 추가하지 않았다.

- 내부 `grounding.fact_ids`로 현재 source 분석의 짧은 근거 ID만 선택한다. 서버가 이 ID를 실제 Step/path로 해석하고 기존 `fact_value` 검증·Markdown 렌더링을 사용한다. 공개 API나 프론트가 받아 보관할 ID가 아니다.
- SessionAnalysisMiddleware의 `evidence_view=compact_evidence_view`를 Conversation에서만 사용한다. 기본값은 None이며 기존 문맥을 그대로 전달한다. 공용 middleware는 분석 Agent를 직접 import하지 않고 역할에서 전달한 view 함수를 사용한다. owner 검증 후 현재 invocation용 근거 목록을 만들며 공유 Agent나 전역 변수에 세션별 목록을 캐시하지 않는다.
- 모델용 `analysis.fact_catalog`는 Step별로 `{짧은 ID: {label, value}}`를 담는다. 원본 checkpoint·DB의 완료 분석 관찰은 수정하지 않는다. 같은 Step에 관찰이 여러 개면 기존 최종 렌더러와 같이 마지막 관찰을 사용한다.
- 보고서의 마지막 표가 현재 관찰로 다시 만든 **서버 표와 완전히 일치하는 경우에만** 중복 표를 모델용 문맥에서 제외한다. 보고서 해석문은 유지하고, 단순히 같은 제목을 찾았다는 이유로 내용을 삭제하지 않는다.
- 원래 `facts` Step/path 형식도 읽는다. 두 형식을 혼합하거나 새 ID를 중복 선택하면 거절한다. 다른 source/owner, 없는 ID, 실패·불완전·생략 관찰, 큰 객체, 잘못된 경로는 기존처럼 사용할 수 없다.
- prompt와 Reply JSON Schema의 `message`·`fact_ids` 설명 모두에 수치 분리를 명시했다. 실제 수치·본문 숫자·번호 제목 검증을 느슨하게 만들지 않았다. 일반 FAQ의 숫자는 기존처럼 허용한다.
- 모델 문맥은 기존 `AGENT_SESSION_ANALYSIS_MAX_CHARS` 한도 내에서 구성한다. 근거 목록은 내부 상수 `MAX_CATALOG_FACTS=512`, 최종 선택은 128개로 제한한다. 누락된 목록·잘린 관찰을 표시하고 공개 답변에도 한계를 알린다. 메타데이터만으로 목록 공간을 확보할 수 없으면 기존 bounded 문맥과 Step/path 선택으로 돌아간다.
- benchmark에 `--private-responses-output`을 추가해 거절된 중간 응답을 명시적으로 지정한 비공개 파일에만 기록할 수 있게 했다. 원문과 인증 설정은 Git에 포함하지 않는다.

새 환경변수·DB migration·REST/SSE 변경은 없다. Workflow 작성·승인·Executor 제출 규칙과 파일 등록 API도 변경하지 않았다.

## 검증·성능

최종 실제 모델 A/B 8회에서 변경 후 4회 모두 성공했다. 설명은 평균 28.20→10.76초(61.8% 감소), 호출 2→1, 첫 응답 통과 0/2→2/2였다. 보고서는 변경 후 2/2 성공·평균 34.48초·호출 2회였고 정정 한 번이 남았다. 기존은 1/2 성공(53.96초)이며 다른 회는 91.72초에 검증 실패로 종료됐다. 실패를 성공 평균에 섞지 않았다. API·Agent 전체 회귀 **908개 + subtest 2개 통과**(390.79초). 이후 공용 middleware의 업무 직접 import를 역할의 view 함수 주입으로 정리했고, 모델용 문맥은 동일함을 유지하면서 관련 **84개를 재검증**했다(6.42초). 마지막 wheel도 source checkout 없이 12개 역할·리소스·API 36개 path·mock 실행 4단계를 확인했다. 실제 신규 계획 smoke는 32.71초에 등록 Skill/4개 Tool의 유효 계획을 만들었고 Executor 제출은 0건이다. 공개 OpenAPI SHA256은 변경 전과 같은 `cc6f1d371e93f77a91a28463daeaaf0932213cb67ab0a0d08999323af0beb80b`다. 기존 durability warning은 남아 있으며 신규 테스트 실패는 없다. 단위·경계 검증은 소유권/실제 값/타입과 배열 경로/출력 한도/새 형식의 Graph·SSE·history 일치/동일 캐시 Agent의 동시 세션 분리를 확인한다. Native JSON Schema와 prompt JSON 두 전략에서 오류 수치가 거절되고 수정되는 것을 확인한다.

중간 후보는 별도 실험으로 보존하고 최종 평균에 섞지 않는다. 최종 비교도 조건별 두 회이며 공유 모델 자원을 통제하지 못했다. 같은 고정 입력을 prompt 개선에도 사용했으므로 새로운 요청에 대한 일반화 성능을 보장하지 않는다. 근거 검증 통과가 자연어 해석·인과관계의 정답을 뜻하지 않는다는 기존 한계도 유지한다.

[상세 측정·한계](../reports/answer-efficiency-2026-10-02.md)를 참고한다.

## 후속 작업 우선순위

2026-10-02 사용자 요청으로 모델 호출 횟수 최적화는 보류했다. 보고서 재작성에서 남아 있는 정정 호출 한 번의 원인 분석·축소는 [후속 작업 목록](backlog.md)에 기록하고 현재 진행 대상에서 제외한다. 050의 구현·검증 결과와 기존 수치·소유권 검증은 유지한다.

## 통합·게시

구현·검증 commit `5e22b9868a33fb5c2f52e4e855e7592dca2ba68a`을 `feature/refactor-base`에 fast-forward 병합했다. 베이스·파생 `feature/grounded-answer-efficiency`를 origin `https://github.com/KimHwaJin/prj_re.git`에 atomic push한 뒤 원격 두 SHA 일치를 확인했다. 파생 브랜치는 당시 검증 구현을 보존한다. 게시 결과는 베이스의 별도 문서 commit으로 남긴다. 기존 사용자 checkout과 Docker 서비스는 유지했으며 배포·서비스 재기동은 수행하지 않았다.
