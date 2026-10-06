"""Offline mock Studio entrypoint; uses the production planning/execution builder.

Studio supplies its own checkpointer. This entrypoint does not open the service's
DB, Redis, Executor bindings or SSO. Integration tests go through the Runs API.
"""

import sys
from pathlib import Path

src_root = Path(__file__).resolve().parent / "src"
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
from dtest.devtools.analysis.runtime import local_runtime
from dtest.agent_service.agents.analysis.planning.graph import (
    build_planning_graph,
)

graph = build_planning_graph(local_runtime(), checkpointer=None)
