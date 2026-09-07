import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import torch

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.ddpg import DDPGAgent, DDPGConfig, ReplayBuffer  # noqa: E402


class DDPGTests(unittest.TestCase):
    def test_deterministic_action_does_not_advance_exploration_or_training(self):
        agent = DDPGAgent(DDPGConfig(hidden_dim=32))
        state = np.linspace(-0.5, 0.5, 26, dtype=np.float32)
        with torch.no_grad():
            expected = agent.actor(torch.from_numpy(state).unsqueeze(0))[0].numpy()
        with patch.object(agent.noise, "sample", side_effect=AssertionError("OU noise used")):
            first = agent.deterministic_normalized_action(state)
            second = agent.deterministic_normalized_action(state)
        np.testing.assert_array_equal(first, expected)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(first.dtype, np.float32)
        self.assertEqual(first.shape, (3,))
        self.assertEqual((agent.global_step, agent.update_count, len(agent.replay)), (0, 0, 0))

    def test_replay_preserves_terminal_and_truncation(self):
        replay = ReplayBuffer(capacity=4, seed=2)
        state = np.zeros(26, dtype=np.float32)
        action = np.zeros(3, dtype=np.float32)
        replay.push(state, action, -10.0, state, True, False)
        replay.push(state, action, 0.0, state, False, True)
        self.assertEqual(len(replay), 2)
        values = replay.state_dict()["buffer"]
        self.assertTrue(values[0]["terminated"])
        self.assertFalse(values[0]["truncated"])
        self.assertFalse(values[1]["terminated"])
        self.assertTrue(values[1]["truncated"])

    def test_update_is_finite_and_action_boundary_is_physical(self):
        config = DDPGConfig(
            hidden_dim=32,
            replay_capacity=64,
            batch_size=8,
            warmup_steps=8,
            seed=5,
        )
        agent = DDPGAgent(config)
        state = np.zeros(26, dtype=np.float32)
        action = agent.select_action(state, explore=True)
        normalized_roundtrip = agent.to_normalized_action(action)
        self.assertEqual(action.shape, (3,))
        self.assertEqual(normalized_roundtrip.shape, (3,))
        self.assertTrue(np.isfinite(action).all())
        self.assertTrue(np.all(normalized_roundtrip >= -1.0))
        self.assertTrue(np.all(normalized_roundtrip <= 1.0))
        self.assertTrue(np.all(action >= agent.action_contract.physical_low))
        self.assertTrue(np.all(action <= agent.action_contract.physical_high))
        metrics = None
        for index in range(8):
            next_state = np.full(26, index / 10.0, dtype=np.float32)
            normalized = agent.select_normalized_action(state, explore=True)
            metrics = agent.observe(
                state,
                normalized,
                0.0,
                next_state,
                terminated=False,
                truncated=index == 7,
            )
            state = next_state
        self.assertIsNotNone(metrics)
        self.assertTrue(all(np.isfinite(value) for value in metrics.values()))
        self.assertEqual(agent.update_count, 1)

    def test_checkpoint_roundtrip_restores_training_state(self):
        config = DDPGConfig(
            hidden_dim=32,
            replay_capacity=64,
            batch_size=4,
            warmup_steps=4,
            seed=8,
        )
        agent = DDPGAgent(config)
        state = np.ones(26, dtype=np.float32)
        for index in range(4):
            normalized = agent.select_normalized_action(state, explore=False)
            agent.observe(state, normalized, 0.0, state, False, index == 3)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "ddpg.pt")
            agent.save_checkpoint(
                path,
                manifest={"test": "roundtrip"},
                stage=1,
                episode=7,
                metrics={"critic_loss": 1.0},
            )
            restored = DDPGAgent(config)
            manifest = restored.load_checkpoint(path)
            self.assertEqual(manifest["test"], "roundtrip")
            self.assertEqual(restored.global_step, agent.global_step)
            self.assertEqual(restored.update_count, agent.update_count)
            with torch.no_grad():
                input_tensor = torch.as_tensor(state).unsqueeze(0)
                expected = agent.actor(input_tensor)
                actual = restored.actor(input_tensor)
            self.assertEqual(torch.max(torch.abs(expected - actual)).item(), 0.0)
            self.assertEqual(len(restored.replay), len(agent.replay))
            payload = torch.load(path, map_location="cpu", weights_only=False)
            self.assertEqual(payload["episode"], 7)
            self.assertEqual(payload["metrics"]["critic_loss"], 1.0)


if __name__ == "__main__":
    unittest.main()
