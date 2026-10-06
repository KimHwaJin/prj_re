"""Refresh standalone test-console preview contracts without starting services."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dtest.settings.loader import load_settings
from dtest.bootstrap import create_app


def main():
    base = ROOT / "docs/contracts/agent-api"

    def read(name):
        return json.loads((base / name).read_text())

    fixtures = {
        "waiting": read("responses/waiting_input.json"),
        "project": read("responses/project_detail.json"),
    }
    for kind in (
        "plan_review",
        "planning_question",
        "decision_review",
        "repair_review",
    ):
        fixtures[kind] = read("events/" + kind + ".json")["data"]
    fixtures["result"] = {
        "status": "analysis_completed",
        "execution_id": "sample-execution",
        "executor_status": "SUCCEEDED",
        "observations": [
            {
                "step_id": "profile",
                "status": "SUCCEEDED",
                "summary": {
                    "note": "화면 확인용 가상 데이터",
                    "rows": 1200,
                    "columns": 9,
                },
            },
            {
                "step_id": "statistics",
                "status": "SUCCEEDED",
                "summary": {"sample": True, "max_val_mean": 1.27},
            },
        ],
        "skipped_steps": [],
        "report": {
            "format": "markdown",
            "status": "ready",
            "artifact_registration": "deferred",
            "content": (
                "# 데이터 품질 분석 보고서\n\n이 보고서는 화면 확인용 "
                "가상 데이터입니다. 실제 실행 결과가 아닙니다.\n\n## 주요 "
                "발견\n데이터 로드와 품질 확인, 기술 통계 단계가 완료된 "
                "화면입니다.\n\n## 다음 검토\n이상치 후보의 업무상 의미를 "
                "확인하고 필요한 경우 새로운 분석을 "
                "요청하세요."
            ),
        },
    }
    settings = load_settings(
        config={
            "MODEL_PROVIDER": "mock",
            "EVENT_WORKER_ENABLED": False,
            "AGENT_WORKER_ENABLED": False,
        },
        environ={},
    )
    fixtures["openapi"] = create_app(settings).openapi()
    payload = json.dumps(
        fixtures, ensure_ascii=False, separators=(",", ":")
    ).replace("<", "\\u003c")
    file = ROOT / "src/dtest/api_service/web/static/demo.html"
    text, count = re.subn(
        r'(<script id="fixtures" type="application/json">)[\s\S]*?(</script>)',
        lambda m: m[1] + payload + m[2],
        file.read_text(),
    )
    if count != 1:
        raise ValueError("Expected one fixture block")
    file.write_text(text)
    print(
        "Updated standalone preview; no lifespan, DB or Executor "
        "call performed"
    )


if __name__ == "__main__":
    main()
