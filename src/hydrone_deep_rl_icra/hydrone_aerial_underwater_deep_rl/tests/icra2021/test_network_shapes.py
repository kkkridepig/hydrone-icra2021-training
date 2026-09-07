import sys
import unittest
from pathlib import Path

import torch

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.networks import (  # noqa: E402
    PaperActor,
    PaperCritic,
    PaperSACPolicy,
    PaperTwinQ,
)


class NetworkShapeTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(7)
        self.state = torch.randn(4, 26)
        self.action = torch.tanh(torch.randn(4, 3))

    def test_actor_26_to_3(self):
        actor = PaperActor()
        output = actor(self.state)
        self.assertEqual(tuple(output.shape), (4, 3))
        self.assertTrue(torch.isfinite(output).all())
        self.assertTrue(torch.all(output <= 1.0))
        self.assertTrue(torch.all(output >= -1.0))

    def test_ddpg_critic_29_to_1(self):
        critic = PaperCritic()
        output = critic(self.state, self.action)
        self.assertEqual(tuple(output.shape), (4, 1))
        self.assertTrue(torch.isfinite(output).all())

    def test_sac_twin_q(self):
        twin_q = PaperTwinQ()
        q1, q2 = twin_q(self.state, self.action)
        self.assertEqual(tuple(q1.shape), (4, 1))
        self.assertEqual(tuple(q2.shape), (4, 1))
        self.assertTrue(torch.isfinite(q1).all())
        self.assertTrue(torch.isfinite(q2).all())

    def test_policy_sampling_and_deterministic_action(self):
        policy = PaperSACPolicy()
        sampled, log_prob, mean, log_std = policy.sample_normalized_action(self.state)
        deterministic = policy.deterministic_normalized_action(self.state)
        self.assertEqual(tuple(sampled.shape), (4, 3))
        self.assertEqual(tuple(log_prob.shape), (4, 1))
        self.assertEqual(tuple(mean.shape), (4, 3))
        self.assertEqual(tuple(log_std.shape), (4, 3))
        self.assertEqual(tuple(deterministic.shape), (4, 3))
        for value in (sampled, log_prob, mean, log_std, deterministic):
            self.assertTrue(torch.isfinite(value).all())

    def test_finite_forward_backward(self):
        actor = PaperActor()
        critic = PaperCritic()
        twin_q = PaperTwinQ()
        policy = PaperSACPolicy()

        actor_loss = actor(self.state).pow(2).mean()
        critic_loss = critic(self.state, self.action).pow(2).mean()
        q1, q2 = twin_q(self.state, self.action)
        twin_loss = (q1.pow(2) + q2.pow(2)).mean()
        sample, log_prob, _, _ = policy.sample_normalized_action(self.state)
        policy_loss = (sample.pow(2) + log_prob.pow(2)).mean()

        total = actor_loss + critic_loss + twin_loss + policy_loss
        self.assertTrue(torch.isfinite(total))
        total.backward()
        for module in (actor, critic, twin_q, policy):
            for parameter in module.parameters():
                self.assertIsNotNone(parameter.grad)
                self.assertTrue(torch.isfinite(parameter.grad).all())


if __name__ == "__main__":
    unittest.main()

