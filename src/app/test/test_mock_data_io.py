import unittest
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

import pandas as pd

from agent_config import PROJECT_ROOT, TEST_DATA_SELECTION, load_agent_settings
from service_settings import load_settings
from app.workflow.tools.data_io.extract_data import extract_data
from app.workflow.tools.data_io.transform_nce import transform_nce
from app.workflow.tools.data_io.transform_wt import transform_wt
from app.agents.workflow_generator.data_load_steps import required_data_load_steps
from app.schemas.agents.orchestration_schema import DataSelectionResponse


class MockDataIoTest(unittest.TestCase):
    query = {
        "lot_cd": "6E2",
        "process": ["ALL"],
        "query_mode": "period",
        "start_dt": "2026-08-01",
        "end_dt": "2026-08-01",
        "limit": 3,
    }

    def setUp(self):
        self.temporary_root = tempfile.TemporaryDirectory()
        data_dir = Path(self.temporary_root.name) / "data"
        data_dir.mkdir()
        for filename in (
            "df_nce_wide_format.parquet",
            "df_wt_symbol_wide_format.parquet",
        ):
            shutil.copyfile(PROJECT_ROOT / "mock_data" / filename, data_dir / filename)
        self.mock_root = patch(
            "service_settings._snapshot",
            load_settings(config={"MOCK_DATA_ROOT": self.temporary_root.name}, environ={}),
        )
        self.mock_root.start()
        real_read_parquet = pd.read_parquet

        def read_test_parquet(path, *args, **kwargs):
            local_path = PROJECT_ROOT / "mock_data" / Path(path).name
            return real_read_parquet(local_path, *args, **kwargs)

        self.parquet_reader = patch(
            "pandas.read_parquet",
            side_effect=read_test_parquet,
        )
        self.parquet_reader.start()

    def tearDown(self):
        self.parquet_reader.stop()
        self.mock_root.stop()
        self.temporary_root.cleanup()

    def test_extract_then_transform_nce_returns_wide_data(self):
        extracted = extract_data(data_type="nce", **self.query)
        transformed = transform_nce(data=extracted["data"], transform_op="pivot")

        self.assertEqual(extracted["query_params"], self.query)
        self.assertEqual(extracted["data"], {})
        self.assertTrue(extracted["metadata"]["placeholder"])
        self.assertEqual(transformed["shape"], [10_000, 14])
        self.assertIn("BLC(CELL) OPEN MASK MACRO.max_val", transformed["data"])
        self.assertNotIn("process", transformed["data"])
        self.assertNotIn("max_val", transformed["data"])

    def test_extract_then_transform_wt_returns_wide_data(self):
        extracted = extract_data(data_type="wt_symbol", **self.query)
        transformed = transform_wt(
            data=extracted["data"], transform_op="wt_fail_pivot"
        )

        self.assertEqual(extracted["data"], {})
        self.assertEqual(transformed["shape"], [10_000, 7])
        self.assertEqual(transformed["data"]["PT1H.symbol"][0], "EB")
        self.assertEqual(transformed["data"]["PT1H.p_f"][0], 0)
        self.assertNotIn("process", transformed["data"])

    def test_transform_falls_back_to_full_wide_mock_for_raw_input(self):
        self.assertEqual(transform_nce(data={"raw": [1]})["shape"], [10_000, 14])
        self.assertEqual(transform_wt(data={"raw": [1]})["shape"], [10_000, 7])

    def test_extract_placeholder_does_not_validate_customer_arguments(self):
        result = extract_data(data_type="future_type", limit=0)

        self.assertEqual(result["data"], {})
        self.assertEqual(result["data_type"], "future_type")
        self.assertEqual(result["query_params"]["limit"], 0)

    def test_selection_schema_rejects_unsupported_data_type(self):
        selection = {
            "data_count": 2,
            "datasets": [
                {"role": "x", "data_type": "csv", **self.query},
                {
                    "role": "y",
                    "data_type": "wt_symbol",
                    **{**self.query, "transform_op": "wt_fail_pivot"},
                },
            ],
        }
        with self.assertRaisesRegex(ValueError, "nce.*wt_symbol"):
            DataSelectionResponse.model_validate(selection)

    def test_step_builder_rejects_unsupported_data_type(self):
        dataset = {"role": "x", "data_type": "csv", **self.query}
        with self.assertRaisesRegex(ValueError, "Unsupported data_type 'csv'"):
            required_data_load_steps({"data_count": 1, "datasets": [dataset]})

    def test_data_mock_builds_direct_wide_parquet_load_steps(self):
        steps = required_data_load_steps(TEST_DATA_SELECTION, data_mock=True)

        self.assertEqual(
            [step["tools"][0]["tool"] for step in steps],
            ["data_load", "data_load"],
        )
        self.assertEqual(
            [step["tools"][0]["arguments"]["parquet_path"] for step in steps],
            [
                str(Path(self.temporary_root.name) / "data" / "df_nce_wide_format.parquet"),
                str(Path(self.temporary_root.name) / "data" / "df_wt_symbol_wide_format.parquet"),
            ],
        )

    def test_data_mock_setting_defaults_false_and_accepts_true(self):
        self.assertFalse(load_agent_settings(environ={"MODEL_NAME": "test"}).data_mock)
        self.assertTrue(
            load_agent_settings(
                environ={"MODEL_NAME": "test", "DATA_MOCK": "true"}
            ).data_mock
        )


if __name__ == "__main__":
    unittest.main()
