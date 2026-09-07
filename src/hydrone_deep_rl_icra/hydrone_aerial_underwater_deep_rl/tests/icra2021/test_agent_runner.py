"""Exercise the actual agents and runner's evaluation branches without Gazebo."""

import copy
import importlib.util
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import torch

PACKAGE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "icra2021_runner_under_test", PACKAGE / "scripts" / "icra2021_agent.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TwoStepEnvironment:
    def __init__(self, config):
        self.resets = 0
        self.closed = False

    def reset(self):
        self.resets += 1
        self.steps = 0
        return np.zeros(26, dtype=np.float32)

    def step(self, action):
        assert action.shape == (3,) and np.isfinite(action).all()
        assert np.all(action >= [0.0, -0.25, -0.25])
        assert np.all(action <= [0.25, 0.25, 0.25])
        self.steps += 1
        return np.full(26, 0.1, dtype=np.float32), 0.0, False, self.steps == 2, {}

    def close(self):
        self.closed = True


class AgentRunnerTests(unittest.TestCase):
    def run_case(self, algorithm, mode, evaluation_enabled=True, fail_evaluation=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "episodes": 1000,
                "max_steps": 2,
                "training": {
                    "seed": 0, "device": "cpu", "hidden_dim": 32,
                    "batch_size": 2, "warmup_steps": 2, "replay_capacity": 16,
                },
                "evaluation": {
                    "enabled": evaluation_enabled,
                    "interval_episodes": 50 if mode == "resume" else 1,
                    "episodes": 2, "max_steps": 2,
                },
                "logging": {"output_dir": str(root / "run")},
                "checkpoint": {"path": str(root / "result.pt")},
            }
            agent, training = runner._make_agent(algorithm, config)
            checkpoint_arg = ""
            before = None
            if mode in ("resume", "evaluate"):
                checkpoint_arg = str(root / "input.pt")
                runner._save_checkpoint(agent, algorithm, Path(checkpoint_arg), 1, 49, {})
                before = Path(checkpoint_arg).read_bytes()
            params = {
                "~algorithm": algorithm, "~mode": mode, "~stage": 1,
                "~config": str(root / "config.yaml"), "~checkpoint": checkpoint_arg,
                "~episode_limit": 1,
            }
            ros = Mock()
            ros.get_param.side_effect = lambda key, default=None: params.get(key, default)
            ros.is_shutdown.return_value = False
            ros.get_time.return_value = 0.0
            env = TwoStepEnvironment(None)
            initial_noise = copy.deepcopy(agent.noise.state_dict()) if algorithm == "ddpg" else None
            with ExitStack() as stack:
                stack.enter_context(patch.object(runner, "rospy", ros))
                stack.enter_context(patch.object(runner, "_load_config", return_value=config))
                stack.enter_context(patch.object(runner, "_git_metadata", return_value={}))
                stack.enter_context(patch.object(runner, "_make_agent", return_value=(agent, training)))
                stack.enter_context(patch.object(runner, "PaperEnvironment", return_value=env))
                if fail_evaluation:
                    stack.enter_context(patch.object(
                        agent, "deterministic_normalized_action",
                        side_effect=AttributeError("evaluation regression"),
                    ))
                    with self.assertRaisesRegex(AttributeError, "evaluation regression"):
                        runner._run()
                else:
                    self.assertEqual(runner._run(), 0)

            output = root / "run" / "gates" / "episode_1"
            summary = json.loads((output / "summary.json").read_text())
            records = [json.loads(line) for line in (output / "episodes.jsonl").read_text().splitlines()]
            self.assertTrue(env.closed)
            if fail_evaluation:
                self.assertEqual(summary["status"], "failed")
                self.assertIn("AttributeError", summary["error"])
                self.assertTrue(Path(summary["checkpoint"]).is_file())
                return
            self.assertEqual(summary["status"], "completed")
            self.assertIsNone(summary["error"])
            self.assertFalse(summary["interrupted"])
            if mode == "evaluate":
                self.assertEqual([row["kind"] for row in records], ["evaluation"])
                self.assertEqual((agent.global_step, agent.update_count, len(agent.replay)), (0, 0, 0))
                self.assertEqual(Path(checkpoint_arg).read_bytes(), before)
                if initial_noise is not None:
                    np.testing.assert_array_equal(agent.noise.state_dict()["state"], initial_noise["state"])
                self.assertIsNone(summary["checkpoint"])
            else:
                expected_evaluations = 2 if evaluation_enabled else 0
                self.assertEqual([row["kind"] for row in records],
                                 ["training"] + ["deterministic_evaluation"] * expected_evaluations)
                self.assertEqual(env.resets, 1 + expected_evaluations)
                self.assertEqual((agent.global_step, agent.update_count, len(agent.replay)), (2, 1, 2))
                self.assertEqual(summary["completed_training_episodes"], 50 if mode == "resume" else 1)

    def test_periodic_evaluation_after_training(self):
        for algorithm in ("ddpg", "sac"):
            with self.subTest(algorithm=algorithm):
                self.run_case(algorithm, "train")

    def test_resume_preserves_periodic_evaluation_at_episode_50(self):
        for algorithm in ("ddpg", "sac"):
            with self.subTest(algorithm=algorithm):
                self.run_case(algorithm, "resume")

    def test_standalone_evaluation_does_not_train_or_write_checkpoint(self):
        for algorithm in ("ddpg", "sac"):
            with self.subTest(algorithm=algorithm):
                self.run_case(algorithm, "evaluate")

    def test_evaluation_can_be_disabled(self):
        self.run_case("ddpg", "train", evaluation_enabled=False)

    def test_evaluation_exception_is_reported_as_failure(self):
        self.run_case("ddpg", "train", fail_evaluation=True)
