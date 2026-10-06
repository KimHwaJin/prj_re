"""Load each role's independent, packaged prompt without normalizing its text."""

from importlib.resources import files


def load_prompt(package: str) -> str:
    return files(package).joinpath("prompt.md").read_text(encoding="utf-8")
