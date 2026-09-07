import sys
import unittest
from unittest.mock import Mock
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.environment import (  # noqa: E402
    EnvironmentConfig,
    PaperEnvironment,
    PoseState,
    evaluate_transition,
    goal_metrics,
)


class EnvironmentLogicTests(unittest.TestCase):
    def test_sensor_shutdown_is_distinct_from_live_timeout(self):
        class ROSException(Exception):
            pass
        env = PaperEnvironment.__new__(PaperEnvironment)
        env.config = EnvironmentConfig()
        env._Odometry = object
        env._LaserScan = object
        env._rospy = Mock()
        env._rospy.ROSException = ROSException
        env._rospy.wait_for_message.side_effect = ROSException("shutdown or timeout")
        env._rospy.is_shutdown.return_value = False
        with self.assertRaises(TimeoutError):
            env._wait_for_measurement()
        env._rospy.is_shutdown.return_value = True
        with self.assertRaisesRegex(RuntimeError, "ROS shutdown"):
            env._wait_for_measurement()

    def setUp(self):
        self.config = EnvironmentConfig(max_steps=20)
        self.safe_scan = [2.0] * 20
        self.action = [0.1, -0.1, 0.05]

    def test_goal_event_rewards_without_termination(self):
        pose = PoseState(1.0, 1.0, 2.0, 0.0)
        result = evaluate_transition(
            self.safe_scan,
            pose,
            (1.1, 1.0, 2.0),
            self.action,
            step_index=0,
            config=self.config,
        )
        self.assertEqual(result.reward, 100.0)
        self.assertTrue(result.goal_reached)
        self.assertFalse(result.terminated)
        self.assertFalse(result.truncated)
        self.assertEqual(result.observation.shape, (26,))

    def test_collision_terminates_with_negative_reward(self):
        scan = [2.0] * 19 + [0.49]
        result = evaluate_transition(
            scan,
            PoseState(0.0, 0.0, 2.5),
            (3.0, 3.0, 2.0),
            self.action,
            step_index=0,
            config=self.config,
        )
        self.assertEqual(result.reward, -10.0)
        self.assertTrue(result.collision)
        self.assertTrue(result.terminated)
        self.assertFalse(result.truncated)

    def test_time_limit_is_truncated_not_terminated(self):
        result = evaluate_transition(
            self.safe_scan,
            PoseState(0.0, 0.0, 2.5),
            (3.0, 3.0, 2.0),
            self.action,
            step_index=self.config.max_steps - 1,
            config=self.config,
        )
        self.assertEqual(result.reward, 0.0)
        self.assertFalse(result.terminated)
        self.assertTrue(result.truncated)

    def test_default_paper_time_limit_is_500_steps(self):
        config = EnvironmentConfig()
        result = evaluate_transition(
            self.safe_scan,
            PoseState(0.0, 0.0, 2.5),
            (3.0, 3.0, 2.0),
            self.action,
            step_index=499,
            config=config,
        )
        self.assertEqual(config.max_steps, 500)
        self.assertFalse(result.terminated)
        self.assertTrue(result.truncated)

    def test_evaluation_goal_z_minus_one_is_not_safety_collision(self):
        pose = PoseState(2.0, 3.0, -0.95, 0.0)
        result = evaluate_transition(
            self.safe_scan,
            pose,
            (2.0, 3.0, -1.0),
            self.action,
            step_index=0,
            config=self.config,
        )
        self.assertFalse(result.collision)
        self.assertEqual(result.reward, 100.0)
        self.assertFalse(result.terminated)

    def test_twenty_step_random_rollout_contract(self):
        rng = np.random.default_rng(4)
        pose = PoseState(0.0, 0.0, 2.5, 0.0)
        goal = (3.0, 3.0, 2.0)
        for step in range(20):
            action = np.asarray(
                [
                    rng.uniform(0.0, 0.25),
                    rng.uniform(-0.25, 0.25),
                    rng.uniform(-0.25, 0.25),
                ],
                dtype=np.float32,
            )
            result = evaluate_transition(
                self.safe_scan,
                pose,
                goal,
                action,
                step_index=step,
                config=self.config,
            )
            self.assertEqual(result.observation.shape, (26,))
            self.assertTrue(np.isfinite(result.observation).all())
            self.assertGreaterEqual(result.observation[20], 0.0)
            self.assertLessEqual(result.observation[20], 0.25)
            self.assertGreaterEqual(result.observation[21], -0.25)
            self.assertLessEqual(result.observation[21], 0.25)
            self.assertGreaterEqual(result.observation[22], -0.25)
            self.assertLessEqual(result.observation[22], 0.25)

    def test_heading_and_distance_are_three_dimensional(self):
        heading, heading_z, distance = goal_metrics(
            PoseState(0.0, 0.0, 2.5, 0.0), (0.0, 3.0, -1.0)
        )
        self.assertAlmostEqual(heading, np.pi / 2.0, places=6)
        self.assertLess(heading_z, 0.0)
        self.assertAlmostEqual(distance, np.sqrt(21.25), places=6)


if __name__ == "__main__":
    unittest.main()
