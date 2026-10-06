# 107. Windows 로컬 실행 루프 호환성

2026-10-06 / `feature/windows-selector-startup`  
출발: `feature/refactor-base`, `1fd9b1cb1f9e7522ef34e06f7cac2da1a6ecdd23`.

## 문제와 확인 범위

사용자 환경은 Windows, Python 3.11.9, 로컬 PostgreSQL이다. 앱 시작 후
`task-lock-reconciler`의 DBAPIError와 signal listener의
ConnectionDoesNotExistError, “지정된 네트워크 이름을 더 이상 사용할 수
없습니다”, Selector 이벤트 루프 정책에 관한 경고가 보고되었다.
전체 경고와 사용자 DB 접속 로그는 아직 확보하지 못했다.

현재 lock의 Uvicorn 0.52.4는 Windows 단일 프로세스에서 Proactor 루프
factory를 선택한다. 그러나 LangGraph checkpoint와 프로젝트 Store가 사용하는
psycopg의 비동기 연결은 Windows에서 Selector를 요구한다.
전역 policy만 바꾸면 Uvicorn의 명시적 factory가 이를 무시할 수 있다.
[psycopg 공식 안내](https://www.psycopg.org/psycopg3/docs/advanced/async.html).

Python 3.11.9에서 Runner와 Selector를 사용할 수 있으므로 Python 버전 교체가
이번 수정의 전제는 아니다. 정책 API의 Python 3.14 폐기 경고와도 구분한다.
[Python 정책 문서](https://docs.python.org/3/library/asyncio-policy.html).

API/명령 원장의 SQLAlchemy와 signal listener는 asyncpg를 사용한다.
해당 transport 오류와 psycopg의 루프 비호환은 구분해야 한다. Windows 오류64는
연결 손실을 뜻하지만 이것만으로 PostgreSQL 설정이나 루프가 원인이라고
단정할 수 없다.
[Windows 오류 코드](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-).

## 변경

- `src/dtest/bootstrap.py`의 `build_server()` 실행기에서 Windows만 Runner와
  Selector 루프를 명시한다. 전역 loop policy 변경과 새 환경설정을 추가하지 않는다.
- 실제 선택 루프를 비밀값 없이 시작 로그에 남긴다.
- 기존 `serve()`와 lifespan·종료 신호 처리를 유지한다. Runner가 서버 종료·예외
  발생 시 남은 task, async generator, executor와 루프를 정리한다.
- Linux/macOS는 Uvicorn의 기존 `run()`을 그대로 사용한다.
- 외부 플랫폼의 이미 실행 중인 루프는 변경하지 않는다. 플랫폼 launcher는
  해당 OS와 DB 드라이버에 맞는 루프를 선택해야 한다.
- 로컬 실행 안내에 선택 확인과 DB 연결 오류 진단 범위를 기록한다.

## 검증

macOS / Python 3.11.15 / Uvicorn 0.52.4에서 관련 회귀 **74개 통과**:

- Windows 분기를 모의하여 Selector에서 coroutine 실행, 정상/실패 종료 후
  남은 task 취소·정리 및 loop close 확인.
- 전역 policy와 Uvicorn 기본 runner를 사용하지 않는 Windows 경로 확인.
- 해당 경로에서 실제 루프백 HTTP 요청, FastAPI lifespan 시작·종료, 소켓 전달 확인.
- 비 Windows 위임, 기존 실제 SIGTERM drain, 설정 및 checkpoint/Store 자원 수명 회귀.

처음 테스트 실행의 4개 실패는 테스트에 허용되지 않는 PORT=0을 넣은 문제였으며
정상 설정 생성 후 Uvicorn 테스트 port를 0으로 바꾸어 해결했다. 2개 SIGTERM
테스트는 sandbox의 socket bind 차단으로 실패했으며 루프백 허용 실행에서 통과했다.

저장소 AGENTS.md에 따른 `uv run --locked --no-sync` Ruff·ty 검사를 실행했다.
`--no-sync`는 이미 설치된 locked 검사 도구와 테스트 Python을 사용하기 위한 것이다.
전체 Ruff는 기존 **3,501건**, ty는 기존 **772건**으로 실패 상태가 동일하다.
파일·코드·메시지를 비교했을 때 추가/삭제 진단은 각각 0건이다.
새 테스트 파일 Ruff는 통과했고 전체 `ruff format --no-cache --check`는
**788파일 통과**다. 기존 전체 검사 실패를 통과로 표시하지 않는다.

## 사용자 재확인과 남은 범위

```sh
uv run python app.py --env local
```

`service_event_loop platform=win32 implementation=_WindowsSelectorEventLoop`
로그를 확인한다. Python 3.11.9 실제 Windows에서 실행하지 않았으며,
사용자 로컬 DB·사내 SDK 연결도 여기서 검증하지 않았다. Windows 루프 경고와
DB 연결 오류가 모두 해소되었다고 주장하지 않는다. asyncpg 연결 손실이 남으면
실제 DB host/port와 서버 로그·단독 연결 시험으로 이어서 진단한다.

DB SSL, 재시도, Worker 기능, 사용자 YAML이나 DB 데이터는 변경하지 않았다.
