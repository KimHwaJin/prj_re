# Python 개발 기준

- Python 타입 검사는 ty, lint와 formatting은 Ruff로 관리한다.
- 줄 길이는 79자를 기준으로 한다. Ruff 설정은 pyproject.toml을 따른다.
- Python 코드 변경 후 `uv run --locked ty check`, `uv run --locked ruff check`,
  `uv run --locked ruff format --check`를
  실행하고 변경 범위에 적절한 회귀 테스트를 수행한다.
- 검사 실행 여부와 결과를 구분하여 보고한다. 기존 오류가 있으면 변경에서
  생긴 오류와 구분하고, 검사를 통과했다고 표현하지 않는다.
