"""Resume a current planning checkpoint across explicit before/after source snapshots.

Uses explicit source snapshots, mock LLM/data, no Executor calls, and a dedicated
local database named boundary_checkpoint_test. Does not drop schemas or tables.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from urllib.parse import urlparse

CHILD = (
    "\nimport asyncio,json,sys\nfrom uuid import uuid4\nfrom "
    "dtest.settings.agent import load_agent_settings\nfrom "
    "dtest.agent_service.agents.analysis.planning.runtime import "
    "PlanningRuntime\nfrom dtest.agent_service.agents.analysis.planning.g"
    "raph import build_planning_graph\nfrom "
    "langgraph.checkpoint.postgres.aio import AsyncPostgresSaver\nfrom "
    "langgraph.types import Command\nfrom "
    "dtest.contracts.initial_request import initial_identity\nfrom "
    "dtest.contracts.user_resume import "
    "resume_identity,resume_envelope\nsettings=load_agent_settings({'MODE"
    "L_PROVIDER':'mock','AGENT_PROJECT_MEMORY_MODE':'off','EXECUTOR_SUBM"
    "IT_ENABLED':'false',\n    'ANALYSIS_DATASETS':'{\"transition-dat"
    'a":{"title":"Transition fixture","scope":"GLOBAL","runtime_path":"/'
    "workspace/pv/example.parquet\"}}'})\nasync def main():\n    "
    "runtime=PlanningRuntime(settings)\n    async with "
    "AsyncPostgresSaver.from_conn_string(sys.argv[1]) as saver:\n        "
    "await saver.setup()\n        graph=build_planning_graph(runtime,chec"
    "kpointer=saver)\n        cfg={'configurable':{'thread_id':sys.argv[2"
    "]}}\n        if sys.argv[3]=='before':\n            "
    "value={key:str(uuid4()) for key in "
    "('user_id','project_id','run_id')}\n            "
    "value.update(session_id=sys.argv[2],user_request='package "
    "transition',model_selection=runtime.models.select().model_dump())\n "
    "           value['initial_request_identity']=initial_identity(value"
    ")\n            state=await graph.ainvoke(value,cfg,durability='sync'"
    ")\n            assert state['interaction_data']['kind']=='plan_revie"
    "w'\n            assert state['initial_request_receipt']==value['init"
    "ial_request_identity']\n        else:\n            saved=await "
    "graph.aget_state(cfg)\n            assert "
    "saved.next==('await_review',) and saved.values['user_request']=='pa"
    "ckage transition'\n            plan=saved.values['plan_views'][0]\n  "
    "          target=saved.tasks[0].interrupts[0].id\n            "
    "command={'resume':{'action':'approve_plan','plan_id':plan['plan_id'"
    "],'plan_revision':plan['plan_revision']}}\n            "
    "identity=resume_identity(str(uuid4()),target,command)\n            "
    "state=await graph.ainvoke(Command(resume={target:resume_envelope(id"
    "entity,command)}),cfg,durability='sync')\n            assert "
    "state['user_resume_receipt']==identity\n            assert "
    "state['final_response']['status']=='plan_approved' and not "
    "state.get('__interrupt__')\n            assert not runtime.agents\n  "
    "      snapshot=await graph.aget_state(cfg)\n        "
    "print(json.dumps({'phase':sys.argv[3],'next':list(snapshot.next),\n "
    "           'steps':len((snapshot.values.get('approved_snapshot') "
    "or {}).get('steps',[])),\n            "
    "'nodes':sorted(graph.get_graph().nodes),\n            "
    "'edges':sorted((e.source,e.target) for e in "
    "graph.get_graph().edges)}))\nasyncio.run(main())\n"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-source", type=Path, required=True)
    parser.add_argument("--after-source", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    parsed = urlparse(args.database_url)
    if (
        parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.path != "/boundary_checkpoint_test"
    ):
        parser.error("Only local boundary_checkpoint_test is allowed")
    thread = str(uuid4())
    results = []
    for phase, source in [
        ("before", args.before_source),
        ("after", args.after_source),
    ]:
        result = subprocess.run(
            [sys.executable, "-c", CHILD, args.database_url, thread, phase],
            cwd=source,
            env={**os.environ, "PYTHONPATH": str(source.resolve() / "src")},
            text=True,
            capture_output=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(f"{phase} failed: {result.stderr}")
        results.append(json.loads(result.stdout.splitlines()[-1]))
    assert results[0]["nodes"] == results[1]["nodes"]
    print(
        json.dumps(
            {
                "postgres_checkpoint_resumed": True,
                "graph_node_names_unchanged": True,
                "diagram_edge_counts": [len(r["edges"]) for r in results],
                "before_next": results[0]["next"],
                "after_next": results[1]["next"],
                "approved_plan_steps": results[1]["steps"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
