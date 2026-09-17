"""Actual CPU optimizer/checkpoint tests when torch is available.

The tiny generated fixtures only exercise software paths, not the hypothesis.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from core import history_row, load_config

TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None


@unittest.skipUnless(TORCH_AVAILABLE, "torch unavailable: run this test with the existing server torch")
class Learning(unittest.TestCase):
    def setUp(self):
        self.c = load_config(HERE/"config.json")
        self.c.update(perception_epochs=1, expert_epochs=1, batch_size=8,
                      train_seeds=[0], validation_seeds=[10])

    def test_actual_gradient_update(self):
        from learning import accelerator_check
        result = accelerator_check(self.c, "cpu")
        self.assertTrue(result["fp32_event_and_expert_update"])

    def test_short_fit_and_checkpoint_roundtrip(self):
        from learning import Predictor, fit
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            episodes = root/"collection"/"episodes"
            episodes.mkdir(parents=True)
            rng = np.random.RandomState(19)
            for seed in [0, 10]:
                n = 12
                sensors = rng.normal(0, .03, (n, 14)).astype(np.float32)
                sensors[:, 13] = 9.8
                actions = np.tile([.1, -.1, .02], (n, 1)).astype(np.float32)
                labels = np.array([[i % 2, (i//2) % 2] for i in range(n)], dtype=np.float32)
                histories = np.stack([history_row(sensors[i], actions[max(i-1, 0)], .2) for i in range(n)])
                np.savez_compressed(episodes/("s%d.npz" % seed),
                    images=rng.randint(0, 255, (n, 48, 48, 3), dtype=np.uint8),
                    sensors=sensors, actions=actions, labels=labels, history_rows=histories,
                    heights=np.linspace(.5, -.5, n).astype(np.float32), input_dt=np.full(n, .2))
                (episodes/("s%d.json" % seed)).write_text(json.dumps(dict(
                    scenario=dict(seed=seed, goal=[.5, 0, -.6]), reason="success")))
            result = fit(root/"collection", root/"model", self.c, 0, "cpu")
            self.assertTrue(result["checkpoint_roundtrip"])
            predictor = Predictor(root/"model/model.pt", self.c)
            out = predictor.actions(np.zeros(21, dtype=np.float32))
            self.assertEqual(out.shape, (4, 3))
            self.assertTrue(np.isfinite(out).all())
            self.assertTrue((out[:, 0] >= 0).all())
            self.assertTrue((np.abs(out) <= .25).all())
            modified = dict(self.c, horizon=2)
            with self.assertRaises(RuntimeError):
                Predictor(root/"model/model.pt", modified)


if __name__ == "__main__":
    unittest.main()
