# 087 Tool 파라미터 노출·편집 정책

날짜: 2026-10-04. 브랜치: `feature/tool-parameter-review`, 086에서 분기. 베이스 미병합·미푸시·운영 미배포.

## 문제

함수 signature에 있는 선택 인자라도 Workflow arguments에서 생략되면 PlanView에 나타나지 않았다. 실제085에서 statistics.columns 편집이422였다. 사용자에게 필요한 값·함수 기본값·결과 기반 판단과 데이터 객체/실행 경로를 구분하는 등록 정책이 없었고, 모델이 편집 선언을 생략하면 고정 상수도 수정할 수 없었다.

## 변경

- Tool registry의 선택적 parameter_controls에 title/description/editable/value_schema를 선언한다. AST 기본값을 읽고 JSON·schema를 검증하며 코드 실행·annotation eval을 하지 않는다. 등록 생성기는 수동 정책/availability를 보존한다.
- 공통 contracts의 tool_parameters로 기본값 고정·편집 허용·schema 교집합을 모듈화했다. compute_statistics.columns와 detect_outliers.method/columns를 등록하고 data_load/profile_data의 데이터/경로 편집은 금지했다. 기타 정책이 없는 레거시·custom Tool의 명시적 Workflow 정책은 유지한다.
- 생략된 허용 선택 인자는 actual JSON 기본값으로 보충한다. 제공된 literal/input/decision은 유지한다. Workflow는 편집 범위를 더 좁히거나 잠글 수 있다. 결과 기반 판단 output_schema에도 등록 제약을 적용한다.
- 사용자 수정·Agent 값·함수 기본값·미확정을 PlanView의 origin/has_value로 구분하고 제목·설명을 추가했다. 편집/재개/재작성에서 동일 값의 출처를 유지하며 승인 snapshot/실제 제출 코드에 고정한다.
- HTML 폼·public payload schema·SSE/REST 예제와 모든 해당 JSONC 주석을 갱신했다. 제출 path/header/body와 함수 본문·signature·import는 유지한다. 새 설정·DB migration·편집용 모델 호출은 없다.

## 검증

[결과 JSON](../reports/tool-parameter-review-2026-10-04/result.json).

- 관련 Agent/카탈로그/계획/실행/수정/조건/데이터 참조134회귀 통과. null·false·0·키워드 기본값·tuple JSON 변환, 코드 미실행, 잘못된 schema/default, 참조 보호, 제약 교집합, result decision·input 우회 방지, checkpoint 재개·승인 소스 반영을 검사했다.
- 별도 일회성 PostgreSQL에서 API/재작성4회귀 통과. invalid columns422와 token 보존, 재시작 후 사용자 값/승인 snapshot·멱등 replay/SSE를 확인했다. 해당 DB는 제거했다.
- HTML core/preview8개 통과. 실제 HTTP/API/PostgreSQL/Store/Worker/Redis/Executor/Jupyter 연계12개 통과. columns=null을 폼에서 ["max_val"]로 편집하고 실제 Executor 통계 결과가 max_val만 포함함을 검증했다. 실제 실행·finalize·보고서·SSE 재접속·POST stream도 통과했다.
- 사내SDK 판정과 모델 응답은 fixture이며 실제 자연어 계획 품질 검증이 아니다. Node controller + 개발용 DOM double을 사용했고 실제 브라우저 시각·클릭 검증으로 주장하지 않는다. 기존 durability/no-checkpointer 경고6개는 별도 실행 경로에서 그대로다.

## 사용과 후속

최신 코드 진단 서버: http://127.0.0.1:18101/test-console. 전용 임시 DB53602·관리자SSO fixture·모델300ms·실제 Executor 제출. 이전18100 프로세스의 Agent runtime은 이번 정책을 로딩하지 않았으므로 최신 기능 확인은18101을 사용한다. 원래checkout/.env/Compose/원천 Parquet는 수정하지 않았다. Executor 실행 이력은 남는다.

[작성 가이드](../tool-parameter-policy.md)를 따른다. 정책/default를 asset revision에 넣으므로 기존 승인 대기 계획은 자산 변경 보호 대상이다. 운영 배포 전 대기 계획의 이행이 필요하다. 실제 컬럼 존재 여부·실제 모델 자동 입력 품질·커널 의존성·브라우저 UX, Dataset Registry·보고서 Artifact·Workflow CRUD 이행과 기존 성능/운영 후속은 남는다. 이번 검증 시간은 처리량 개선 수치가 아니다.
