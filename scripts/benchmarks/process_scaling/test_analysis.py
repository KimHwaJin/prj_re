"""Reject misleading process aggregates, using an actual smoke capture."""

import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "scaling_analysis", Path(__file__).with_name("analyze.py")
)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


@pytest.fixture
def capture():
    value = os.getenv("DTEST_SCALING_CAPTURE")
    if not value:
        pytest.skip(
            "Set DTEST_SCALING_CAPTURE to a complete raw.json from "
            "the smoke "
            "run"
        )
    return json.loads(Path(value).read_text())


def test_real_capture_validates(capture):
    analysis.validate_processes(capture)


@pytest.mark.parametrize(
    "corrupt",
    [
        "missing_process",
        "duplicate_pid",
        "lost_attempt",
        "duplicated_attempt",
        "exceeded_slots",
        "missing_samples",
        "wrong_cpu_sum",
    ],
)
def test_incomplete_or_inconsistent_capture_is_rejected(capture, corrupt):
    raw = copy.deepcopy(capture)
    if corrupt == "missing_process":
        raw["per_process"].pop()
    elif corrupt == "duplicate_pid":
        raw["per_process"][1]["pid"] = raw["per_process"][0]["pid"]
    elif corrupt == "lost_attempt":
        raw["server"]["workers"].pop()
    elif corrupt == "duplicated_attempt":
        raw["server"]["workers"][1]["run_id"] = raw["server"]["workers"][0][
            "run_id"
        ]
    elif corrupt == "exceeded_slots":
        raw["per_process"][0]["peak_worker"] = raw["config"]["slots"] + 1
    elif corrupt == "missing_samples":
        raw["resource_samples"] = []
    elif corrupt == "wrong_cpu_sum":
        raw["server"]["cpu_seconds"] += 1
    with pytest.raises(AssertionError):
        analysis.validate_processes(raw)


def test_peak_overlap_is_simultaneous_not_sum_of_process_peaks():
    assert (
        analysis.peak_overlap([{"start": 0, "end": 2}, {"start": 2, "end": 3}])
        == 1
    )
    assert (
        analysis.peak_overlap([{"start": 0, "end": 3}, {"start": 2, "end": 4}])
        == 2
    )
