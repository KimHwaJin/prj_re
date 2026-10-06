# 108. Windows DB 초기화 실행 경로

2026-10-06 / `feature/windows-schema-initialization`  
출발: `feature/refactor-base`, `f3d7add787fdfa9278590202856c960e85a34926`.

## 확인된 문제

107의 앱 루프 수정 후 Windows 관련 경고는 해소되었으나 사용자가 처음 보고한
DB 오류는 남아 있다. 전체 하위 원인과 초기화 이력은 확보하지 못했으므로
스키마 누락이나 네트워크 원인으로 단정하지 않는다.

새 DB의 서비스·이벤트 테이블과 checkpoint 테이블은 초기화해야 한다.
앱 시작은 Alembic을 자동 실행하지 않는다. scripts/migrate.py가 CRUD head,
Event head, checkpoint setup 순서로 실행한다. 프로젝트 메모리 Store 준비는
CRUD revision이 담당한다. PostgreSQL 서버·DB 생성과 pgvector 설치는 별도다.

초기화 경로를 확인하면서 launcher와 두 Alembic env.py의 asyncio.run()이
Windows 기본 Proactor를 사용하는 문제를 발견했다. Event chain과 checkpoint의
psycopg async 역시 Selector를 필요로 하므로 앱 루프 수정과 별도로 보완한다.

## 변경

- scripts/migrate.py, crud_migrations/env.py, migrations/env.py에서 Runner를
  사용하고 Windows는 Selector loop factory를 명시한다.
- 비 Windows는 기본 factory를 사용한다. Python 3.11의 asyncio.run()과 동일한
  Runner 수명으로 coroutine, pending task, async generator와 loop를 정리한다.
- 적용 대상·revision·순서·DDL·SSL 정책은 변경하지 않는다.
- 문서에 초기화 순서, DB 자체·pgvector 준비, 연결 오류와 스키마 오류의 차이를
  기록한다. 사용자 DB에 이 작업을 대신 실행하지 않았다.

## 검증

macOS / Python 3.11.15에서 관련 회귀 **23개 통과**:

- 초기화 launcher의 선택 설정과 CRUD→Event→checkpoint 순서, Windows Selector
  및 실행 후 loop close.
- 두 Alembic 진입점을 Windows/비 Windows로 모의 실행하여 마이그레이션 callback,
  engine dispose와 loop close, Event version table 유지 확인. 실제 DB 대신 대역 사용.
- 기존 YAML 선택·파일 도구·앱 Windows 실행 회귀.

처음 테스트에서 DB 대역의 동기 execute를 async로 잘못 정의해 발생한 경고를
수정했다. 최종 실행은 RuntimeWarning을 오류로 처리해 경고 없이 통과했다.
실제 Windows Python 3.11.9와 사용자 DB의 migration은 아직 검증하지 않았다.
기존 DDL migration의 PostgreSQL 검증 이력은 101 등 이전 기록과 구분한다.

AGENTS.md에 따른 locked Ruff·ty·format 검사를 실행했다. 설치된 검사 도구를
재사용하기 위해 uv run --locked --no-sync를 사용했다. 전체 Ruff **3,501건**,
ty **772건**은 기존 실패 상태 그대로이며 파일·코드·메시지 비교에서 추가/삭제
진단은 0건이다. 전체 format은 **790파일 통과**다.

## 사용자 실행

앱을 종료하고 같은 config.yml의 대상을 확인한 다음 실행한다.

```sh
uv run python scripts/migrate.py --env local --check-config
uv run python scripts/migrate.py --env local
uv run python app.py --env local
```

초기화가 성공해도 ConnectionDoesNotExistError가 남으면 스키마 미준비와 별개로
실제 host/port·DB 연결·서버 로그를 진단한다. --check-config는 접속 시험이 아니다.
