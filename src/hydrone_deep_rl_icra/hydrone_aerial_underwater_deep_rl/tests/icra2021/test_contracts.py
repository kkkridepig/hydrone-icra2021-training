import sys
import unittest
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.contracts import (  # noqa: E402
    DEFAULT_ACTION_CONTRACT,
    DEFAULT_OBSERVATION_CONTRACT,
)
from hydrone_icra2021.observation import build_observation  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_dimensions_and_finite_state(self):
        contract = DEFAULT_OBSERVATION_CONTRACT
        self.assertEqual(contract.scan_beams, 20)
        self.assertEqual(contract.state_dim, 26)
        state = build_observation(
            [1.0] * 20,
            [0.1, -0.1, 0.05],
            heading=0.2,
            heading_z=-0.3,
            goal_distance=4.0,
        )
        self.assertEqual(state.shape, (26,))
        self.assertTrue(np.isfinite(state).all())

    def test_scan_shape_is_strict(self):
        with self.assertRaises(ValueError):
            DEFAULT_OBSERVATION_CONTRACT.validate_scan([1.0] * 19)

    def test_action_bounds(self):
        action = DEFAULT_ACTION_CONTRACT.validate_physical([0.0, -0.25, 0.25])
        np.testing.assert_allclose(action, [0.0, -0.25, 0.25])
        with self.assertRaises(ValueError):
            DEFAULT_ACTION_CONTRACT.validate_physical([0.3, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()

