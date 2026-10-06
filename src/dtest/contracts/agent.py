"""Agent command adapter installed by the composition root."""

from collections.abc import Callable

_resume_factory: Callable | None = None


def install_resume_factory(factory: Callable):
    global _resume_factory
    _resume_factory = factory


def resume_command(**kwargs):
    if _resume_factory is None:
        raise RuntimeError("Agent command adapter is not installed")
    return _resume_factory(**kwargs)
