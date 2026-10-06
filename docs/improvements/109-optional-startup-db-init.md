# 109. 환경설정으로 제어하는 앱 시작 DB 초기화

2026-10-06 / `feature/optional-startup-db-init`  
출발: `feature/refactor-base`, `4f0328cac81fb0f42929e93f5c23148da6fa6138`.

## 요구사항과 기존 문제

새 PostgreSQL에서는 사용자가 별도 scripts/migrate.py를 실행해야 했다.
앱 시작에도 초기화하되 환경변수로 on/off할 수 있게 해 달라는 요청을 반영한다.
단순히 앱에서 Alembic upgrade를 직접 부르면 실행 중인 asyncio loop에 새 Runner를
중첩하게 되고, 여러 Pod가 동시에 시작할 때 version table/DDL/SDK setup이 충돌한다.
또 Alembic의 fileConfig가 실행 중인 앱 logger와 root logging을 변경할 수 있다.

## 구현

- `DB_INIT_ON_START`를 RuntimeSettings·ServiceSettings에 추가했다. 기본 false이며
  기존 YAML > 환경변수 > 기본값 우선순위를 유지한다. 예제는 키를 주석으로만
  제공하므로 env on/off를 가리지 않는다. check-config에 bool과 출처를 표시한다.
- true이면 attach_service의 lifespan이 기존 플랫폼/router lifespan, Worker 시작
  전에 초기화를 기다린다. 준비 실패 시 API 제공·Worker 실행을 시작하지 않고
  구성한 SSO 자원을 닫는다. false이면 이 준비 경로를 호출하지 않는다.
- `infrastructure/database/schema.py`로 CRUD head → Event head → checkpoint setup을
  모았다. Store는 기존 CRUD revision으로 준비한다. 수동 migrate.py도 이 경로를
  사용하며 앱 스위치와 무관하게 실행한다. revision을 stamp하거나 DB를 reset하지 않는다.
- Alembic의 global context 충돌을 막는 프로세스 Lock과, 이 서비스 replicas가
  공유하는 주 DB의 session advisory lock으로 전체 초기화 순서를 직렬화한다.
  별도 잠금 연결은 connect timeout10초, 잠금 SQL timeout60초다. 모든 migration에
  이 timeout을 적용하거나 모든 단계를 하나의 transaction으로 묶지 않는다.
  SQL advisory lock은 primary DB 단위다. primary가 다른 외부 서비스·직접 DDL
  호출·실행 중 구 버전 writer까지 조율한다고 주장하지 않는다.
- 앱은 thread에서 동기 migration과 별도 Windows Selector Runner를 실행한다.
  앱 loop를 중첩하거나 막지 않는다. 취소 시 소유한 thread 종료를 기다린 뒤
  취소를 전달하므로 초기화 작업을 버리고 요청 제공을 시작하지 않는다.
- 앱에서 호출할 때 Alembic logging 재설정을 생략한다. CRUD engine은 실패 시에도
  dispose한다. 안전한 초기화 단계·예외 타입을 로그에 남기고 앱 오류에는 DB
  credentials/payload를 포함하지 않는다. 수동 CLI의 원 traceback은 진단용으로 유지한다.
- 설정 예제·설정 분류표·실행 안내·DB 초기화 문서를 갱신했다.

## 검증

macOS / Python 3.11.15 / disposable pgvector PostgreSQL17 (vector0.8.6)에서
관련 테스트 **148개 통과**. RuntimeWarning을 오류로 처리했다.

- default/env/YAML 우선순위·잘못된 bool 검증.
- 초기화→기존 lifespan→Worker 순서, 비활성 미호출, 실패 시 Worker 미시작·SSO close.
- thread가 앱 loop를 막지 않으며 취소 때 소유 작업을 끝까지 기다리는 경계.
- 초기화 실패 시 잠금 해제, 수동 CLI 원인 보존, 앱 예외 redaction, migration
  파일이 없을 때 DB 접근 전 실패.
- 기존 설정파일 선택·export·Alembic Windows loop·앱 Windows loop·실제 SIGTERM 종료.
- 실제 새 primary DB와 별도 checkpoint DB에서 두 앱 subprocess가 같은 advisory
  lock을 기다리는 것을 pg_locks로 확인한 뒤 해제했다. 두 앱 모두 준비 상태가
  되고 서비스/Store/Inbox/checkpoint 테이블이 준비됐다.
- 사용자 1건을 저장한 뒤 앱을 재시작해 그 데이터가 보존됨을 확인했다.
- 잘못된 DB 주소에서 true는 startup failure·준비 상태 미발행, false는 Worker가
  꺼진 앱의 정상 시작을 확인했다. false가 DB를 사용하는 기능의 성공까지
  보장한다는 뜻은 아니다.
- 앱 초기화 후 기존 service_started 로그가 유지됨을 확인했다.

추가로 실제 DB에 수동 migrate.py를 실행했다. DB_INIT_ON_START=false에도 수동
준비가 완료되고 저장한 사용자 1건이 보존됨을 확인했다. 수동 경로/앱 경로 모두
같은 코드라는 점을 실제 DB로 검증했다. 초기화 대상은 이 작업 전용 임시 DB이며
기존 사용자 컨테이너/DB를 변경하지 않았다. 임시 PostgreSQL 컨테이너는 제거했다.

AGENTS.md에 따른 uv run --locked --no-sync Ruff·ty·format 검사를 실행했다.
설치된 locked 검사 도구를 재사용한 것이다. 전체 Ruff는 기존3,501→3,500건으로
실패 상태이며 변경 테스트의 import 오류1건을 정리했다. ty는 기존772건 그대로다.
파일·코드·메시지 비교에서 추가 진단은 각각0건이다. 전체 format은 **794파일 통과**다.
새 schema/테스트 파일 Ruff도 통과했다. 전체 lint/type 검사가 통과했다고 표시하지 않는다.

## 사용

환경변수 제어 시 선택 YAML에서 DB_INIT_ON_START를 생략한다.

```powershell
$env:DB_INIT_ON_START = "true"
uv run python app.py --env local
# 이후 끄고 재시작
$env:DB_INIT_ON_START = "false"
uv run python app.py --env local
```

PostgreSQL 서버, DB 자체, pgvector는 별도로 준비해야 한다. 초기화 파일은 소스
checkout/현재 Docker 이미지에 포함된다. migration resources가 없는 설치 위치에서
옵션을 켜면 명확하게 시작 오류로 처리하며 준비되었다고 간주하지 않는다.
실제 Windows Python3.11.9와 사용자 DB에서 검증한 것은 아니다.
초기화가 기존 ConnectionDoesNotExistError를 고쳤다는 주장도 하지 않는다.
기존 DB의 큰 migration은 이전 writer 중단 후 수동 실행하는 배포 정책을 유지한다.
