import sys
import unittest
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.action_scaling import (  # noqa: E402
    to_normalized_action,
    to_physical_action,
)
from hydrone_icra2021.contracts import DEFAULT_ACTION_CONTRACT  # noqa: E402


class ActionScalingTests(unittest.TestCase):
    def test_vector_roundtrip(self):
        normalized = np.asarray([-1.0, -0.25, 0.75], dtype=np.float32)
        physical = to_physical_action(normalized)
        restored = to_normalized_action(physical)
        np.testing.assert_allclose(restored, normalized, atol=1e-6)
        DEFAULT_ACTION_CONTRACT.validate_physical(physical)

    def test_batch_roundtrip_and_bounds(self):
        normalized = np.asarray(
            [[-1.0, -1.0, -1.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
            dtype=np.float32,
        )
        physical = to_physical_action(normalized)
        restored = to_normalized_action(physical)
        np.testing.assert_allclose(restored, normalized, atol=1e-6)
        self.assertTrue(np.all(physical >= DEFAULT_ACTION_CONTRACT.physical_low))
        self.assertTrue(np.all(physical <= DEFAULT_ACTION_CONTRACT.physical_high))


if __name__ == "__main__":
    unittest.main()

