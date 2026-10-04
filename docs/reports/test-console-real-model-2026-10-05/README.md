# 실제 모델 기능 콘솔 검증 — 2026-10-05

테스트 직원 로그인과 실제 LLM 연결을 분리한 콘솔에서 브라우저 E2E가 완료됐다. 실제 데이터 로드·통계 실행은 사용자가 확정한 max_val만 수행했다. 페이지 복원·승인 편집·POST SSE 경로에서 확인한 화면 상태 문제를 수정했다. 모델의 보고서 해석에는 승인 변경을 충분히 반영하지 않는 사례가 남았다.

## 조건과 범위

- 브랜치 feature/real-model-test-console, b69ca9f에서 분기. 단일 로컬 앱 프로세스·단일 사용자 기능 시험이다.
- 실제 qwen38-27b-nvfp4를 기존 private 모델 설정으로 연결했다. fixture 응답/전송 대체를 설치하지 않았다. model.frodo.com alias는 진단 프로세스에만 적용했다.
- 직원 검증은 synthetic admin verdict fixture, 로그인 리다이렉트·쿠키·CSRF·DB 권한은 실제 서비스다. 사내 SDK를 검증한 결과가 아니다.
- 실제 loopback API/Redis/Worker·전용 임시 PostgreSQL17(runtime/checkpoint/Store)·Executor/Jupyter default kernel·등록된 default-nce Parquet를 사용했다. 별도 consumer group/키 namespace를 사용하며 Phoenix는 껐다.
- 실제 모델 콘솔18102·DB53603은 검증 후 사용하도록 유지한다. 고정 모델 회귀에 사용한 별도18103·53604는 종료 후 소유 임시 자원을 제거한다. 기존18100/18101·Compose·Executor 이력은 보존한다.

## 실제 브라우저 결과

Codex 내장 브라우저에서 실제 페이지·DOM·쿠키 왕복을 조작하고 화면을 캡처했다. Mac 네이티브/Chrome은 잠금 또는 탭 생성 timeout이 있었으며, 내장 브라우저에서는 작업을 완료했다. 이전 DOM double 검증을 실제 브라우저로 표시한 것이 아니다.

| 시나리오 | 관찰 결과 |
|---|---|
| 로그인/세션 | 테스트 직원으로 로그인·기본 프로젝트 생성·default kernel 세션 생성 |
| 계획 | 실제 모델이 data_load + compute_statistics 계획을 제안; Skill/설명/파라미터 표시·코드 미노출 |
| 입력 수정 | max_val/x를 max_val로 수정. 편집 후 사용자 출처와 revision 증가 표시 |
| 제외 검증 | load 제외로 dependency를 깨면422. 수정값 유지. statistics만 제외하는 편집은 정상 반영 후 복원 |
| HITL 복원 | 새로고침 후 동일 Run·interaction·revision2·max_val 수정값 복원. SSE 해제/재접속 후 승인 가능 |
| 승인/실행 | 동일 공개 Run으로 실제 제출·실행·완료. DB의 final_response도 SUCCEEDED, statistics 대상 max_val만 확인 |
| 실행 중 복귀 | 실행 대기 중 페이지 새로고침 후 기존 Run 복원·입력 잠금. 완료 후 입력 허용 |
| POST SSE | 실제 모델 후속 설명3개 모두 success/answer. 최종 코드에서 접수 직후 busy/입력 잠금·Run 목록 갱신, 종료 후 완료 표시·입력 허용 |
| 세션 전환 | 별도 세션은 입력 가능. 원래 세션으로 복귀하면 최신 Run 복원 |
| 재로그인 | 로그아웃 시 입력 잠금·연결 해제, 다시 로그인하면 같은 세션/Run 복원 |

[요약 검산 JSON](result.json)은 이번 DB의 실제 완료 결과와 브라우저 확인 항목을 담는다. 분석1개 + 후속 설명3개다. 후속은 final_response에 execution_id가 없는 answer이며, 브라우저 시험 전체의 Executor HTTP 호출 횟수를 계측한 결과는 아니다. 별도 계획·승인 단계와 사용자 조작 시간을 포함한 전체 latency/처리량 비교는 수행하지 않았다.

## 발견한 화면 문제와 수정

1. SSE/Run 상세가 최신 상태여도 최근 Run 목록은 이전 running을 표시했다. 현재 상세 상태를 해당 목록 요약에 반영한다.
2. Session 상세 새로고침이 왼쪽 목록의 cached availability를 바꾸지 않았다. 조회한 해당 세션의 availability/active_run을 갱신한다.
3. POST SSE는 접수 후 이전 세션의 send_message 상태가 남았다. 접수한 pending/running/waiting_executor를 즉시 입력 잠금에 반영하고, 접수 후 Session/Run 목록을 명시적으로 갱신한다.
4. POST SSE가 끝나도 POST SSE 연결됨으로 남았다. terminal 결과와 스트림 종료를 확인한 후 완료로 표시한다.

상시 Session 폴링을 추가하지 않았고, 기존 승인/소유권·API409 보호를 완화하지 않았다. 서비스 API·LangGraph·Tool·Executor payload·DDL 변경은 없다.

## 자동 회귀

- 진단 설정/CLI/모델 allowlist/프로세스 alias/fixture 미설치: pytest12개 통과.
- HTML core/샘플/모드 표시: Node9개 통과.
- 고정 모델 + 실제 HTTP/DB/Worker/Executor controller 회귀: 최종12개 통과. 첫 번째 회귀 후 실제 브라우저에서 POST SSE 문제를 추가 발견하여 수정하고 다시 통과했다. [최종 결과](fixture-result.json).
- 해당 controller 시험은 개발용 DOM double이다. 이번 실제 모델 브라우저 시험과 별개다.

```sh
PYTHONPATH=src /Users/a10054/SKAX_PROJECT/dtest-agent/.venv/bin/python -m pytest scripts/diagnostics/tests/test_console_modes.py -q
node --test tools/test-console/tests/console.test.cjs
TEST_CONSOLE_API_URL=http://127.0.0.1:18103/api/v1 TEST_CONSOLE_EXPECT_ADMIN=1 \
  node tools/test-console/tests/live-console.cjs
```

마지막 명령은 고정 모델로 실행한 별도 진단 앱을 대상으로 한다. 실제 모델18102에 고정 응답 가정의 live-console.cjs를 실행하지 않는다. [실행/설정 가이드](../../../tools/test-console/README.md)를 따른다.

## 남은 문제와 경계

실행은 max_val만 수행했지만 보고서는 사용자가 뺀 x를 원래 목표로 설명하고 미산출을 언급했다. 수치 대상 실행이 잘못된 증거는 없으며, 승인 계획/설명문/보고서 입력 문맥의 어느 지점이 원인인지는 이번에 확정하지 않았다. **다음 Agent 기능 검토: 최종 승인된 계획과 보고서·후속 해석의 일치.** 모델 호출 수 최적화와 구분한다.

최소 Markdown 렌더러는 헤딩·텍스트 중심이므로 pipe 표·inline formatting은 원문으로 보인다. 기능 콘솔 사용성 개선 후보다. 데이터 head·전체 노트북·원본 모델 출력·직원/인증정보는 이 보고서에 복사하지 않는다. 검증 캡처는 로컬 /private/tmp/test-console-089-running.jpg, /private/tmp/test-console-089-completed.jpg에 보관하고 Git에 넣지 않는다.

사내 SSO SDK·Gaia·배포/원격 커널·Registry·보고서 Artifact·파일/이미지 입력·전체 관리 CRUD·부하/접근성/반복 성공률 검증은 미완료다. 구조 회귀의 범위가 진단 도구/HTML에 한정되므로 서비스 전체 Agent 테스트를 다시 실행하지 않았다. 베이스 미병합·미푸시·운영 미배포다.
