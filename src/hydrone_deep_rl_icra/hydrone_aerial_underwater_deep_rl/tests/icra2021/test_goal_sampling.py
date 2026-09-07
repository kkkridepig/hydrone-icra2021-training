import random
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.goal_sampling import (  # noqa: E402
    GoalSamplingConfig,
    PublicGoalSampler,
)


class GoalSamplingTests(unittest.TestCase):
    def test_upstream_public_grid_and_bounds(self):
        sampler = PublicGoalSampler(stage=1, rng=random.Random(3))
        for _ in range(200):
            x, y, z = sampler.sample()
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, 3.9)
            self.assertGreaterEqual(y, -4.0)
            self.assertLessEqual(y, 3.9)
            self.assertGreaterEqual(z, 0.5)
            self.assertLessEqual(z, 3.9)
            self.assertAlmostEqual(round(x * 10.0) / 10.0, x)
            self.assertAlmostEqual(round(y * 10.0) / 10.0, y)
            self.assertAlmostEqual(round(z * 10.0) / 10.0, z)
            self.assertTrue(sampler.is_valid((x, y, z)))

    def test_stage2_never_samples_inside_pillar_radius(self):
        sampler = PublicGoalSampler(stage=2, rng=random.Random(9))
        for _ in range(1000):
            x, y, z = sampler.sample()
            for center_x, center_y in sampler.config.obstacle_centers:
                distance_sq = (x - center_x) ** 2 + (y - center_y) ** 2
                self.assertGreaterEqual(
                    distance_sq, sampler.config.obstacle_radius ** 2
                )

    def test_sampling_timeout_is_bounded(self):
        config = GoalSamplingConfig(
            x_values=(2.0,),
            y_values=(2.0,),
            z_values=(2.0,),
            max_attempts=3,
        )
        sampler = PublicGoalSampler(stage=2, rng=random.Random(1), config=config)
        with self.assertRaises(RuntimeError):
            sampler.sample()


if __name__ == "__main__":
    unittest.main()

