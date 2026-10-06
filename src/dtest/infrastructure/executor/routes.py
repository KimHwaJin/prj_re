"""Executor v1 REST contract; paths belong to the integration, not deployment settings.

EXECUTOR_BASE_URL is the service root, optionally including a reverse-proxy prefix.
The API version prefix below is appended once; do not include /api/v1 in the base.
"""

from enum import StrEnum
from urllib.parse import quote


class ExecutorRoute(StrEnum):
    EXECUTIONS = "/api/v1/executions"
    EXECUTION = EXECUTIONS + "/{execution_id}"
    OPERATIONS = EXECUTION + "/operations"
    RESULT = EXECUTION + "/result"
    NOTEBOOK = EXECUTION + "/notebook"
    FINALIZE = EXECUTION + "/finalize"
    CANCEL = EXECUTION + "/cancel"
    ARTIFACTS = EXECUTION + "/artifacts"
    EVENTS = EXECUTION + "/events"

    def url(self, base_url: str, execution_id: str | None = None) -> str:
        path = self.value
        if "{execution_id}" in path:
            if not isinstance(execution_id, str) or not execution_id.strip():
                raise ValueError("execution_id must be a non-empty string")
            path = path.format(execution_id=quote(execution_id, safe=""))
        return base_url.rstrip("/") + path
