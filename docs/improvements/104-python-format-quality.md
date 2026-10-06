# 104. Ruff·ty 개발 기준과 79자 포맷 적용

## 문제와 변경

현재 서비스·테스트·진단 스크립트의 포맷이 혼재하고, Ruff·ty 버전과
줄 길이 기준이 저장소에 고정되어 있지 않았다.

- `pyproject.toml`에 Python 3.11, Ruff `line-length = 79`, E501 검사를 정의했다.
- 개발 의존성·`uv.lock`에 Ruff 0.16.10과 ty 0.0.84를 고정했다.
  운영 의존성 및 `requirements.txt`는 변경하지 않았다.
- `AGENTS.md`와 README에 동일한 검사 명령과 결과 보고 기준을 기록했다.
- 관리 대상 Python 575개 중 509개와 문서의 Python 예제에 포맷을 적용했다.
- 일부 긴 단일행 문자열은 값이 같은 인접 문자열로 분리했다.
  docstring·SQL 등 여러 줄의 문자열 내용과 import 순서는 유지했다.
- 과거 검증 소스 사본인 `docs/reports/**`와 받은 표준 원본
  `docs/contracts/workflow-standard/original-1.0/**`은 변경하지 않고
  Ruff·ty 검사에서도 제외했다. 현재 소스의 검사 규칙은 완화하지 않았다.
- docstring의 `Args:` 행 끝 공백이 정리된 Tool 한 개는 표준 카탈로그
  생성기로 동기화했다. 함수·파라미터·출력 계약은 그대로다.

## 연계 회귀에서 발견한 별도 오류 수정

전체 회귀에서 이전 103 경로 통합의 ID 타입 오류가 확인됐다.
PostgreSQL 이벤트 원장은 `execution_id`를 UUID 객체로 반환하지만
`ExecutorRoute.url()`은 문자열을 요구해 이력 보충 호출이 예외로 중단됐다.
`EventRouter`의 URL 생성 지점 한 곳에서 `str(execution_id)`로 변환했다.
DB ID 타입·이벤트 스키마·HTTP 경로 규칙은 그대로다.
실제 DB·HTTP 경계를 검증하는 기존 이벤트 복구 8개를 수정 후 재실행했다.
아래의 포맷 AST 비교에서는 이 명시적인 한 줄 수정만 별도로 제외했다.

## 검증

| 확인 | 결과 |
|---|---|
| `ruff format --check .` | 784개 파일·문서 코드 블록 포맷 통과 |
| 관리 대상 Python AST 비교 | 575개 포맷 동등; 명시적 UUID 변환 1곳과 docstring 공백 정리만 구분 |
| API·Agent 최초 전체 회귀 | 1296 통과, 4 실패, 0 오류, 0 제외 |
| UUID 변환 수정 후 이벤트 복구 재검증 | 8개 통과; 해당 최초 실패 3개 모두 포함 |
| 카탈로그 동기화 후 Agent 전체 재검증 | 377개 통과; 카탈로그 동등성 포함 |
| 전체 고유 항목의 최종 검증 합계 | 1300 통과, 0 제외; 미해결 실패 0 |
| 설치 wheel 검증 | checkout import 없이 API 32개 경로, 역할 Agent 5개, 리소스 로딩 통과 |
| `git diff --check` | 통과 |
| 관리 대상 Ruff 진단 | 13,473 → 3,520; 전체 검사는 미통과 |
| E501 줄 길이 진단 | 11,644 → 1,817 |
| 관리 대상 ty 진단 | 772 → 772; 위치 변경을 제외한 신규·해소 진단 0 |

회귀 시험은 이 작업에서 만든 PostgreSQL·Redis 임시 컨테이너와 격리 DB를
사용했다. 기존 사용 환경·외부 Executor·실제 LLM은 대상으로 삼지 않았다.
설치 검증은 새 wheel을 만들어 수행했다. AST 비교에서는 실행 구문과
일반 문자열 값을 그대로 비교하고 docstring의 공백 정리만 정규화했다.
UUID를 HTTP ID로 변환하는 별도 오류 수정은 포맷 변경과 구분해 검증했다.
포맷으로 인해 코드 제출 시 달라질 수 있는 원문 표기와 실행 의미는 구분한다.

## 남은 검사 진단

포맷 통과가 전체 lint·타입 검사를 통과했다는 뜻은 아니다. Ruff formatter는
긴 주석·docstring·분리할 수 없는 식별자나 일부 표현식을 모두 줄이지 않는다.
**현재 모든 물리적 줄이 79자 이하인 상태는 아니다.** E501을 숨기는 일괄
예외는 추가하지 않았다. import 정렬·미사용 import·테스트 fixture 중복 선언과
기존 타입 진단 등은 별도 lint·타입 수정 작업이 필요하다.

처음에 시도한 import 이동·변수 수정·unsafe 자동 수정을 묶은 작업은
포맷 요청보다 범위가 넓다는 사유로 자동 승인 검토에서 거절됐다.
해당 변경은 적용하지 않았고 작업 전 Python 백업에서 다시 시작해
동작을 보존하는 포맷으로 범위를 한정했다. 안전한 괄호 제거는 별도로
적용하고 전체 AST 동등성을 다시 확인했다.

## 재현 명령

```bash
uv sync --locked --group dev
uv run --locked ruff format .
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
```

작업 검증에는 위와 동일한 고정 버전의 Ruff·ty 임시 환경과 기존
Python 3.11 테스트 의존성 환경을 사용했다. 실제 서비스 배포·기동은
이 작업에 포함하지 않는다.
