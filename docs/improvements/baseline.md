# 개선 시작 기준 상태

기록일: 2026-09-28. 새 개선 기록 디렉터리는 아래 목록에서 제외했다.

- 출발 브랜치: `feature/load_test_v1`
- 개선 브랜치: `feature/runtime-hardening`
- HEAD: `dad1d6c27e32e2aeb0a616bfeb8368cab1fd6e6b`
- 기존 tracked diff SHA-256: `dd615633cbf2156a2a7cae0cbfe0c749080d4f7aa24dc5038f517fffe417b548`
- 기존 변경 15개 파일: 143줄 추가, 98줄 삭제.
- 준비 시 staged 변경 없음. 브랜치 생성만 했으며 기존 변경은 미커밋 상태다.

이 목록은 기존 테스트·진단·설계 작업과 이후 개선의 경계를 설명한다. 소스 백업이나 복구 가능한 commit이 아니며 untracked 디렉터리는 Git 출력처럼 묶어서 표시한다. 파일 내용과 환경 변수 값은 포함하지 않는다.

**기존 tracked 변경 요약**

```text
 .env.example                                 | 32 +++++++++++----------
 .gitignore                                   |  5 +++-
 Dockerfile                                   | 36 ++++++++++++++++-------
 README.md                                    | 21 ++++++++++++++
 compose.yaml                                 | 43 +++-------------------------
 deploy/README.md                             |  5 +++-
 src/agent_config.py                          |  6 ++++
 src/app/agent_run_worker.py                  |  9 ++++--
 src/app/agent_worker/api_bridge.py           |  2 ++
 src/app/agents/orchestration/dependencies.py |  4 +++
 src/app/core/database.py                     |  2 ++
 src/app/graphs/checkpointer_factory.py       |  3 ++
 src/app/services/agent_graph_service.py      | 43 ++++++++++++++++------------
 src/app/services/graph_crud_persistence.py   | 20 +++++++------
 src/app/services/run_service.py              | 10 +++++--
 15 files changed, 143 insertions(+), 98 deletions(-)
```

**기존 작업 트리 상태**

```text
 M .env.example
 M .gitignore
 M Dockerfile
 M README.md
 M compose.yaml
 M deploy/README.md
 M src/agent_config.py
 M src/app/agent_run_worker.py
 M src/app/agent_worker/api_bridge.py
 M src/app/agents/orchestration/dependencies.py
 M src/app/core/database.py
 M src/app/graphs/checkpointer_factory.py
 M src/app/services/agent_graph_service.py
 M src/app/services/graph_crud_persistence.py
 M src/app/services/run_service.py
?? .dockerignore
?? .env
?? .env.local.example
?? compose.external.yaml
?? compose.loadtest.real.yaml
?? compose.loadtest.yaml
?? compose.local.yaml
?? docs/checkpoint-coexistence-diagnosis-2026-09-23.md
?? docs/design/
?? docs/local-docker.md
?? docs/reports/
?? docs/service-loadtest.md
?? docs/user-request-required-diagnosis-2026-09-23.md
?? scripts/diagnostics/
?? scripts/loadtest/
?? scripts/local.py
?? scripts/local/
?? src/app/agents/orchestration/mock_dependencies.py
?? src/app/core/run_diagnostics.py
?? src/app/test/test_run_diagnostics.py
?? src/app/test/test_service_load_mock.py
?? workspace/
```

기존 변경을 임의로 삭제·되돌리지 않는다. 이후 commit을 만들 때 해당 개선 범위와 기존 변경을 확인해 선택적으로 포함한다. 환경 파일·민감정보를 일괄 stage하지 않는다.
