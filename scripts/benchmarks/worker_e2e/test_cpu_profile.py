"""Validate diagnostic caller edges without collecting argument values."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from cpu_profile import CPUProfile


@pytest.fixture
def profiler():
    original = ThreadPoolExecutor.submit
    profile = CPUProfile(lambda: True)
    try:
        yield profile
    finally:
        ThreadPoolExecutor.submit = original


def leaf(secret_argument):
    return sum(range(500))


def recurse(depth, secret_argument):
    leaf(secret_argument)
    if depth:
        return recurse(depth - 1, secret_argument)
    return 0


def test_caller_counts_and_privacy(profiler):
    profiler.start()
    recurse(3, "request-body-must-not-be-exported")
    result = profiler.finish()
    recursive = next(row for row in result["main"] if row["function"] == "recurse")
    edge = next(edge for edge in recursive["callers"] if edge["function"] == "recurse")
    assert recursive["calls"] == 4
    assert recursive["primitive_calls"] == 1
    assert edge["calls"] == 3
    assert edge["primitive_calls"] == 1
    assert edge["cumulative_cpu_seconds"] >= edge["self_cpu_seconds"] >= 0
    assert "request-body-must-not-be-exported" not in repr(result)
    assert profiler.finish() is result


def test_offload_profile_has_separate_cpu_clock(profiler):
    profiler.start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(leaf, "private").result() > 0
    result = profiler.finish()
    assert result["offload_jobs"] == 1
    assert any(row["function"] == "leaf" for row in result["offload"])
    assert result["offload_thread_cpu_seconds"] > 0
    assert sum(row["self_cpu_seconds"] for row in result["offload"]) <= result["offload_thread_cpu_seconds"]


def test_outside_measurement_does_not_collect(profiler):
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(leaf, "private").result() > 0
    assert profiler.jobs == []
    assert profiler.finish() is None
