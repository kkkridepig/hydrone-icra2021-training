import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.sac import SACAgent, SACConfig  # noqa: E402


class SACTests(unittest.TestCase):
    def _config(self, **overrides):
        values = dict(
            hidden_dim=32,
            replay_capacity=64,
            batch_size=8,
            warmup_steps=8,
            seed=13,
        )
        values.update(overrides)
        return SACConfig(**values)

    def test_normalized_physical_action_contract_and_determinism(self):
        agent = SACAgent(self._config())
        state = np.zeros(26, dtype=np.float32)
        normalized = agent.sample_normalized_action(state)
        deterministic_a = agent.deterministic_normalized_action(state)
        deterministic_b = agent.deterministic_normalized_action(state)
        physical = agent.to_physical_action(normalized)
        roundtrip = agent.to_normalized_action(physical)

        self.assertEqual(normalized.shape, (3,))
        self.assertTrue(np.isfinite(normalized).all())
        self.assertTrue(np.all(normalized >= -1.0))
        self.assertTrue(np.all(normalized <= 1.0))
        self.assertTrue(np.all(physical >= agent.action_contract.physical_low))
        self.assertTrue(np.all(physical <= agent.action_contract.physical_high))
        np.testing.assert_allclose(roundtrip, normalized, atol=1e-6)
        np.testing.assert_array_equal(deterministic_a, deterministic_b)

    def test_update_and_goal_oversampling_are_finite(self):
        agent = SACAgent(self._config())
        state = np.zeros(26, dtype=np.float32)
        metrics = None
        for index in range(8):
            next_state = np.full(26, index / 10.0, dtype=np.float32)
            normalized = agent.deterministic_normalized_action(state)
            metrics = agent.observe(
                state,
                normalized,
                100.0 if index == 7 else 0.0,
                next_state,
                terminated=False,
                truncated=index == 7,
            )
            state = next_state
        self.assertIsNotNone(metrics)
        self.assertEqual(agent.global_step, 8)
        self.assertEqual(len(agent.replay), 10)  # final goal transition is stored 3x
        self.assertEqual(agent.update_count, 1)
        self.assertTrue(all(np.isfinite(value) for value in metrics.values()))
        self.assertTrue(np.isfinite(agent.alpha))
        self.assertGreater(agent.alpha, 0.0)

    def test_checkpoint_restores_alpha_optimizers_and_replay(self):
        config = self._config()
        agent = SACAgent(config)
        state = np.ones(26, dtype=np.float32)
        for index in range(8):
            normalized = agent.sample_normalized_action(state)
            agent.observe(state, normalized, 0.0, state, False, index == 7)

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "sac.pt")
            agent.save_checkpoint(
                path,
                stage=1,
                episode=4,
                metrics={"smoke": 1.0},
                manifest={"test": "roundtrip", "git": {"head": "abc"}},
            )
            restored = SACAgent(config)
            manifest = restored.load_checkpoint(path)

        self.assertEqual(manifest["test"], "roundtrip")
        self.assertEqual(restored.global_step, agent.global_step)
        self.assertEqual(restored.update_count, agent.update_count)
        self.assertEqual(len(restored.replay), len(agent.replay))
        self.assertAlmostEqual(restored.alpha, agent.alpha, places=7)
        self.assertEqual(
            set(restored.alpha_optimizer.state_dict()["state"].keys()),
            set(agent.alpha_optimizer.state_dict()["state"].keys()),
        )
        with torch.no_grad():
            input_tensor = torch.as_tensor(state).unsqueeze(0)
            expected = agent.policy.deterministic_normalized_action(input_tensor)
            actual = restored.policy.deterministic_normalized_action(input_tensor)
        self.assertEqual(torch.max(torch.abs(expected - actual)).item(), 0.0)


if __name__ == "__main__":
    unittest.main()

