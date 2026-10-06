# 과거 측정·검증 원본

이 디렉토리는 기록된 날짜·commit·설정의 결과와 계산 근거다. 현재 서비스의
실행 안내나 전체 기능 지원 목록이 아니다. 현재 정본은 [문서 안내](../README.md),
현재 재현 도구는 [성능 검증 안내](../service-loadtest.md)를 따른다.

원본 JSON·CSV·manifest·로그·그림은 보존한다. 옛 소스 경로·테스트 명령과 hash는
당시 Git commit을 기준으로 읽는다. 이력의 결과를 현재 코드에 맞춰 소급 수정하지
않는다. 현재 코드의 회귀는 `tests/`이며 기본 pytest는 보고서를 수집하지 않는다.

112에서 result-log-replay/task-event-insert/task-event-full-return의
2026-10-04 보고서에 동일하게 복제된 test_portable_probe.py 세 사본을 삭제했다.
원본 코드는112 이전 Git 이력에 남고, payload replay 불변성 검증은 현재
`tests/api_service/test_plan_event_batch_postgres.py`로 통합했다. 과거 측정 결과나
검증 영수증을 새로 실행한 것으로 바꾸지 않는다.
