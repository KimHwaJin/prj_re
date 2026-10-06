# 099 플랫폼 보안 키 철자 호환

2026-10-06. 브랜치: `feature/platform-security-setting-compat`. 기준: `feature/refactor-base` / `4d9b051`.

## 문제

초기 공유 목록의 `IS_SECURITY_SERVCE`만 플랫폼 키로 허용하여 실제 템플릿의 `IS_SECURITY_SERVICE`가 `Unknown service setting`으로 로컬 기동을 막았다.

## 변경

정상 철자와 이전 전달 철자를 모두 플랫폼 전용 키로 허용한다. 둘 다 서비스 snapshot에서 제외하고 SSO나 역할 설정에 연결하지 않는다. 알 수 없는 서비스 키의 엄격한 검증은 유지한다. 사용자 YAML의 철자를 바꿀 필요는 없다.

## 검증

두 철자·true/false에 대해 실제 YAML을 읽고 서비스 설정이 변경되지 않는 회귀 검사를 추가했다. 설정 bootstrap·YAML 파일·플랫폼 계약 회귀 81개가 3.65초에 모두 통과했다. `git diff --check`도 통과했다. 실제 서비스 재시작이나 배포는 수행하지 않았다.

## 통합·게시

2026-10-06 `0ac031e`는 후속 100의 `ea4f818`에 포함되어 베이스에 fast-forward 병합했다. `feature/platform-security-setting-compat`도 origin에 게시·보존했다. 현재 구현은 플랫폼 전용 허용 목록을 제거한 100을 따른다. [100 통합 기록](100-settings-model-cleanup.md)을 참고한다.
