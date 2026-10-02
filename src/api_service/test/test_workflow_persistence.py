from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from agent_config import load_agent_settings
from service_contracts.workflow import effective_workflow_snapshot, reusable_workflow_snapshot



class _RecordingStore:
    def __init__(self) -> None:
        self.saved: dict | None = None

    def save_catalog_workflow(self, **kwargs):
        self.saved = kwargs
        return "8cdf246a-1e4b-57fa-9012-66d7bf970f22"


class WorkflowPersistenceTests(unittest.TestCase):

    def test_reusable_snapshot_clears_execution_bound_inputs(self):
        workflow = {
            "schema_version": "1.3",
            "workflow": {
                "id": "wf-1",
                "status": "ready",
                "context": {"output_dir": "/tmp/run", "locale": "ko"},
                "input_schema": {
                    "dataset_uri": {
                        "type": "str",
                        "required": True,
                        "allow_llm_inference": False,
                        "validation": [],
                    },
                    "top_n": {
                        "type": "int",
                        "required": False,
                        "allow_llm_inference": False,
                        "validation": [],
                    },
                },
                "inputs": {"dataset_uri": "/pv/a.parquet", "top_n": 10},
                "input_provenance": {
                    "dataset_uri": {
                        "source": "confirmed_metadata",
                        "confirmed": True,
                    }
                },
                "unresolved_inputs": [],
                "steps": [],
            },
        }

        snapshot = reusable_workflow_snapshot(workflow)

        definition = snapshot["workflow"]
        self.assertEqual(definition["status"], "needs_input")
        self.assertEqual(definition["inputs"], {})
        self.assertEqual(definition["input_provenance"], {})
        self.assertNotIn("output_dir", definition["context"])
        self.assertEqual(definition["context"]["locale"], "ko")
        self.assertEqual(
            [item["name"] for item in definition["unresolved_inputs"]],
            ["dataset_uri"],
        )

    def test_effective_snapshot_adds_runtime_decision_only(self):
        workflow = {
            "workflow": {
                "id": "wf-1",
                "steps": [
                    {
                        "id": "eda",
                        "tools": [
                            {
                                "id": "eda.boxplot",
                                "execution": "conditional",
                            }
                        ],
                    }
                ],
            }
        }

        snapshot = effective_workflow_snapshot(
            workflow, {"eda.boxplot": "include"}
        )

        tool = snapshot["workflow"]["steps"][0]["tools"][0]
        self.assertEqual(tool["execution"], "conditional")
        self.assertEqual(tool["runtime_decision"], "include")
        self.assertNotIn(
            "runtime_decision", workflow["workflow"]["steps"][0]["tools"][0]
        )







if __name__ == "__main__":
    unittest.main()
