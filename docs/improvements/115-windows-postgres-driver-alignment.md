# 115 — Windows PostgreSQL 드라이버 정합성과 SQL 오류 진단

## 기준과 상태

- 기준: feature/executor-checkpoint-validation, 728fba8.
- 작업: feature/windows-postgres-driver-alignment.
- 구현 커밋: ea94f23 (의존성·lock·진단·회귀·기록).
- 상태: 구현·격리 검증 완료. 2026-10-08 베이스 통합·origin 게시. 서비스 미배포.
- 사용자 환경: psycopg/psycopg-binary 3.3.6, pool 3.3.3,
  psycopg2-binary 2.9.13. Windows에서 연결 keyword 오류와 Worker 실패 보고.

## 문제와 확인 범위

기존 lock은 psycopg/psycopg-binary 3.3.4, pool 3.3.1이었고 선언은 넓은 범위였다.
사용자 환경과 레포의 재현 가능한 설치 기준이 달랐다. 사용자 보고 버전 숫자
자체는 불일치가 아니다. psycopg2-binary는 다른 모듈로 공존이 가능하다.

공식 3.3.6의 내부 generator는 connect(conninfo)이며 Python 호출부도 이에 맞는다.
이전 3.3.4 호출은 timeout 키워드를 전달한다. 새 바이너리에 이전 호출을 적용한
대조에서 TypeError: connect() takes no keyword arguments를 정확히 재현했다.
정상 설치에서는 실제 DB 연결이 성공했다. 따라서 실제 설치/로드 파일 혼합을
점검해야 하며, 사용자 Windows에서 그 혼합이 발생했다는 것은 아직 미확정이다.

Task reconciler·Agent command Worker는 SQLAlchemy/asyncpg SQL에서 실패했고,
Executor event Worker는 psycopg pool.open(wait=True)가 연결을 준비하지 못하면
PoolTimeout으로 끝난다. ProgrammingError는 같은 원인으로 단정하지 않는다.
기존 background 로그는 예외 타입만 남겨 테이블·컬럼·권한·SQL 오류를 구분하기
어려웠다. 이번 변경이 사용자 DB 스키마 문제까지 고쳤다고 주장하지 않는다.

## 변경

- psycopg[binary,pool]==3.3.6, psycopg-pool==3.3.3으로 선언·lock을 맞췄다.
  binary extra가 동일 버전 바이너리를 요구한다. 다른 패키지 버전은 유지한다.
- psycopg2-binary를 서비스 의존성에 추가하거나 제거하지 않는다.
- SQLSTATE를 제공하는 원인 예외를 DBAPI orig·cause/context에서 찾아 안전한
  원인 타입·상태 코드·조치 힌트로 기록한다. 순환/깊이 제한을 둔다.
- 42P01/42703은 선택 DB migration 확인, 42501은 권한 확인,
  42601은 SQL 확인으로 구분한다. 원문 SQL/예외/credentials는 로그에 넣지 않는다.
- connect keyword TypeError가 직접 전달되면 locked 드라이버 재설치 힌트를 준다.
  pool이 감춘 예외를 임의로 복원하거나 모든 PoolTimeout을 버전 오류로 분류하지 않는다.
- 현재 DB 안내에 Windows 재설치·버전/로드 파일 확인·재시작과 schema 오류
  확인 순서를 추가했다. DB_INIT_ON_START의 기본 false 및 기존 초기화 정책은 유지한다.
- 드라이버 내부 함수 monkeypatch, timeout 제거, Windows 전용 sync 연결 전환은 없다.

## 검증

/tmp의 격리 Python3.11.15 환경에 사용자 보고 네 패키지를 설치했다.
기존 서비스 venv·기존 PostgreSQL·Redis·Executor를 수정하지 않았다.

일회용 pgvector PostgreSQL17 컨테이너 dtest-driver-115-pg를 만들고 loopback
임시 포트60153에서 확인했다. startup109_driver_primary와
startup109_driver_checkpoint는 이번 작업 전용 새 DB다.

- 이전 keyword 호출 + 새 compiled 함수: 예상 TypeError 1회 대조.
- 정상 psycopg3 AsyncConnectionPool: 연결 준비 및 SELECT 1 성공.
- 같은 환경 psycopg2: SELECT 1 성공.
- 관련102회귀 통과. 신규8회귀는 테이블/컬럼/권한/문법 원인, keyword 힌트,
  예외 순환, 비정상 SQLSTATE, 로그 원문 비노출을 확인한다.
- 실제 DB 앱 초기화 회귀1개 통과: 두 앱 동시 초기화 잠금, CRUD/Event/Store/
  checkpoint 테이블 준비, 재시작 데이터 보존, 초기화 실패 시 앱 제공 차단.
- 전체 Ruff3075·ty759는 기존과 동일, 새 진단 없음. 전체 format802파일 통과.
  전체 lint/type 통과로 표현하지 않는다.
- 격리 테스트 첫 수집은 pgvector 보조 경로 누락으로 2개 오류가 있었다.
  테스트 환경 경로만 보완한 뒤 위102개가 통과했다. 제품 코드 실패와 구분한다.

실제 Windows OS 실행과 사용자 DB의 ProgrammingError 원인은 미확정이다.
같은 버전 조합의 macOS 테스트이며 Windows 실행 검증으로 표현하지 않는다.
이번 컨테이너는 검증 후 제거하고 임시 DB·볼륨을 보존하지 않는다.

## 적용과 후속

[현재 DB 안내](../database_migrations.md)의 uv sync --locked
--reinstall-package 명령으로 실제 설치 파일을 정렬하고 프로세스를 재시작한다.
새 SQLSTATE로 남아 있는 오류를 구분한다. 114 설정 정책은 그대로다.
예정된 DB 연결 옵션·데이터 경로 하드코딩 정리를 다음 작업으로 유지한다.

## 통합 기록 — 2026-10-08

사용자 머지·푸시 요청에 따라 114의 bc9ee9e/728fba8 및 이번 구현 ea94f23을
feature/refactor-base에 통합한다. 베이스와 feature/executor-checkpoint-validation,
feature/windows-postgres-driver-alignment를 origin에 atomic push하고 세 원격
ref의 SHA를 로컬과 대조한다. 서비스 재시작·배포는 별도이며 사용자 Windows의
실제 설치 재정렬과 ProgrammingError의 SQLSTATE 확인은 남아 있다.
