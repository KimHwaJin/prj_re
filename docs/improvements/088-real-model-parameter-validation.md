# 088 실제 모델 파라미터·실행·보고서 검증

날짜: 2026-10-04. 브랜치: `feature/real-model-parameter-validation`, 087의 `354faf8`에서 분기. 베이스 미병합·미푸시·운영 미배포.

## 문제와 변경

실제 모델의 미정 데이터 요청이 null 입력·참조 편집 정책 오류로 계획 검증에 실패했다. 미정 키 생략/nullable 선택 인자/Workflow input 편집 위치를 프롬프트에 명시하고 검증 오류에 필드와 수정 방법을 추가했다. 계약을 완화하거나 미정 데이터를 자동 선택하지 않는다.

완료 분석의 보고서 재작성에서는 완료 안내와 근거 표만 나오는 사례를 발견했다. 프롬프트와 내부 message 지침에 실제 Markdown 전문을 요구했고 진단에서 서버 표를 제외한 본문을 확인한다. 외부 REST/SSE 스키마·API path·Tool 함수·Executor payload 규격·설정·DDL 변경은 없다.

실제 모델 전용 `verify_real_model_parameters.py`를 추가했다. 명시 데이터/컬럼·미정 데이터·사용자 편집·결과 기반 판단·후속 설명/보고서를 독립 세션에서 확인한다. 임시DB를 소유·제거하며 모델 응답은 대체하지 않는다. 기존 API 진단의 statistics.columns 편집불가 기대값도 현행087 정책에 맞게 바꿨다.

## 검증

[상세 결과·시간·실패 기록](../reports/real-model-parameters-2026-10-04/README.md).

- 실제 qwen·HTTP·DB/checkpoint/Store·Worker·Redis·Executor/Jupyter 연계. 최초 수정 후 구조28개·보고서 수정 재검증11개.
- 실제 통계는 사용자 최종 max_val만 포함. MULTI는 통계 이후IQR을 선택하고 해당 값으로 실행. followup·보고서 수정은 추가 제출 없음.
- 갱신한 쿠키/CSRF/HITL/SSE/실제Executor 계약15개. Agent 전체341개·최종 관련62개 회귀; 신규2개 포함.
- 초기 실패와 첫 수정 후 보고서 본문 누락도 보존했다. 수치 출처·JSON·본문 존재 확인은 모든 정성 해석의 진실성 검증이 아니다.

## 후속

실제 프론트/브라우저·폐쇄망SDK·커널 가용성, Dataset Registry·보고서 Artifact·Workflow CRUD와 기존074 성능 후보는 남는다. 모델 호출 수 최적화와 운영 복구는 후순위다. 제출 전 구조 응답 실패의 recovery_required 분류도 운영 검토에 기록했다. 기존 테스트 화면18101은 고정 모델이며 이번 실제 모델 검증으로 전환된 것이 아니다. 임시 진단 서버/DB는 제거했다.
