import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.runner_utils import (  # noqa: E402
    deep_merge,
    load_yaml_with_base,
    moving_average,
    real_time_factor,
)


class RunnerUtilsTests(unittest.TestCase):
    def test_deep_merge_does_not_mutate_inputs(self):
        base = {"training": {"gamma": 0.99, "batch_size": 256}, "episodes": 1}
        override = {"training": {"batch_size": 32}, "max_steps": 50}
        merged = deep_merge(base, override)
        self.assertEqual(merged["training"], {"gamma": 0.99, "batch_size": 32})
        self.assertNotIn("max_steps", base)
        self.assertEqual(base["training"]["batch_size"], 256)

    def test_relative_base_config_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "base.yaml").write_text(
                "training:\n  gamma: 0.99\n  batch_size: 256\nepisodes: 1\n"
            )
            (root / "child.yaml").write_text(
                "base_config: base.yaml\ntraining:\n  batch_size: 32\n"
            )
            config = load_yaml_with_base(str(root / "child.yaml"))
        self.assertEqual(config["training"]["gamma"], 0.99)
        self.assertEqual(config["training"]["batch_size"], 32)
        self.assertEqual(config["episodes"], 1)

    def test_moving_average_and_rtf(self):
        self.assertEqual(moving_average([1.0, 2.0, 3.0], 2), 2.5)
        self.assertIsNone(moving_average([], 300))
        self.assertAlmostEqual(real_time_factor(2.0, 4.0), 0.5)
        self.assertIsNone(real_time_factor(0.0, 4.0))

    def test_formal_stage1_profile_is_complete(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "icra2021_stage1_formal.yaml"
        )
        config = load_yaml_with_base(str(path))
        self.assertEqual(config["episodes"], 1000)
        self.assertEqual(config["max_steps"], 500)
        self.assertEqual(config["training"]["seed"], 0)
        self.assertFalse(config["disturbances"]["wind_enabled"])
        self.assertEqual(config["logging"]["moving_average_window"], 300)
        self.assertEqual(config["logging"]["progress_interval_steps"], 100)
        self.assertEqual(config["goal_sampling"]["mode"], "upstream_public")


if __name__ == "__main__":
    unittest.main()
