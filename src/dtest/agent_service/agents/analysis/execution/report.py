"""Render verified quantitative outputs directly; the LLM writes interpretation."""

import html
import json


def evidence_rows(value, prefix=""):
    if isinstance(value, dict) and value.get("type") in {
        "dict",
        "list",
        "tuple",
    }:
        yield from evidence_rows(value["items"], prefix)
        if value.get("truncated"):
            yield prefix + ".표시범위", "일부 출력만 표시"
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from evidence_rows(
                item, prefix + "." + str(key) if prefix else str(key)
            )
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            yield prefix, value
        else:
            for index, item in enumerate(value):
                yield from evidence_rows(item, prefix + "." + str(index))
    else:
        yield prefix, value


def cell(value):
    text = (
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        if not isinstance(value, str)
        else value
    )
    return (
        html.escape(text)
        .replace("|", "&#124;")
        .replace("\n", "<br>")
        .replace("`", "&#96;")
    )


def render_evidence_markdown(observations, *, max_rows=100):
    parts = [
        "## 실행 결과 근거",
        "",
        (
            "아래 값은 검증한 Executor 출력에서 그대로 표시합니다. 모델이 "
            "계산한 값이 아닙니다."
        ),
    ]
    for observation in observations:
        parts.extend(
            [
                "",
                f"### {cell(observation['step_id'])} · {cell(observation['tool_id'])}",
                f"실행 상태: {cell(observation['status'])}",
            ]
        )
        summary = observation.get("summary")
        if summary is None:
            parts.append(
                "표시할 구조화 관찰이 없습니다. 상세 출력은 Executor "
                "실행 기록에 남아 "
                "있습니다."
            )
            continue
        import itertools

        rows = list(itertools.islice(evidence_rows(summary), max_rows + 1))
        parts.extend(["", "| 출력 항목 | 실제 값 |", "|---|---|"])
        parts.extend(
            f"| {cell(key)} | {cell(value)} |"
            for key, value in rows[:max_rows]
        )
        if len(rows) > max_rows:
            parts.append(
                "\n출력 표는 일부 항목만 표시했습니다. 전체 출력은 "
                "Executor 기록을 "
                "확인하세요."
            )
        if observation.get("has_image"):
            parts.append(
                "\n이미지 출력이 있지만 텍스트 모델이 해석한 결과는 아닙니다."
            )
    return "\n".join(parts)
