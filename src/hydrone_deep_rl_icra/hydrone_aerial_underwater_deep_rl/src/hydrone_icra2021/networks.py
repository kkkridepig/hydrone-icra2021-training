"""ROS-free paper-contract actor, critic, twin-Q, and SAC policy networks."""

from typing import Tuple

import torch
from torch import Tensor, nn
import torch.nn.functional as F
from torch.distributions import Normal


DEFAULT_STATE_DIM = 26
DEFAULT_ACTION_DIM = 3
DEFAULT_HIDDEN_DIM = 512


def _hidden_mlp(input_dim: int, hidden_dim: int, hidden_layers: int) -> nn.Sequential:
    layers = []
    current_dim = input_dim
    for _ in range(hidden_layers):
        layers.extend((nn.Linear(current_dim, hidden_dim), nn.ReLU()))
        current_dim = hidden_dim
    return nn.Sequential(*layers)


class PaperActor(nn.Module):
    """Deterministic actor with three 512-unit hidden ReLU layers.

    Output is normalized action in ``[-1, 1]``.  Physical conversion belongs in
    :mod:`action_scaling`, never implicitly inside the observation contract.
    """

    def __init__(
        self,
        state_dim: int = DEFAULT_STATE_DIM,
        action_dim: int = DEFAULT_ACTION_DIM,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
    ) -> None:
        super().__init__()
        self.hidden = _hidden_mlp(state_dim, hidden_dim, hidden_layers=3)
        self.output = nn.Linear(hidden_dim, action_dim)

    def forward(self, state: Tensor) -> Tensor:
        return torch.tanh(self.output(self.hidden(state)))


class PaperCritic(nn.Module):
    """Single DDPG critic: ``(state + action) -> 512 -> 512 -> 1``."""

    def __init__(
        self,
        state_dim: int = DEFAULT_STATE_DIM,
        action_dim: int = DEFAULT_ACTION_DIM,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
    ) -> None:
        super().__init__()
        self.hidden = _hidden_mlp(state_dim + action_dim, hidden_dim, hidden_layers=2)
        self.output = nn.Linear(hidden_dim, 1)

    def forward(self, state: Tensor, action: Tensor) -> Tensor:
        return self.output(self.hidden(torch.cat((state, action), dim=-1)))


class _QBranch(nn.Module):
    def __init__(
        self, input_dim: int, hidden_dim: int, hidden_layers: int, output_dim: int = 1
    ) -> None:
        super().__init__()
        self.hidden = _hidden_mlp(input_dim, hidden_dim, hidden_layers)
        self.output = nn.Linear(hidden_dim, output_dim)

    def forward(self, values: Tensor) -> Tensor:
        return self.output(self.hidden(values))


class PaperTwinQ(nn.Module):
    """SAC twin Q network.

    The local prompt requires the twin-Q contract to be explicit while the
    original paper figure was not independently retrievable in Phase 0.  The
    default therefore follows the stated two-hidden-layer critic shape and is
    configurable without changing the public code.
    """

    def __init__(
        self,
        state_dim: int = DEFAULT_STATE_DIM,
        action_dim: int = DEFAULT_ACTION_DIM,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
        hidden_layers: int = 2,
    ) -> None:
        super().__init__()
        if hidden_layers < 1:
            raise ValueError("hidden_layers must be positive")
        input_dim = state_dim + action_dim
        self.q1 = _QBranch(input_dim, hidden_dim, hidden_layers)
        self.q2 = _QBranch(input_dim, hidden_dim, hidden_layers)

    def forward(self, state: Tensor, action: Tensor) -> Tuple[Tensor, Tensor]:
        values = torch.cat((state, action), dim=-1)
        return self.q1(values), self.q2(values)


class PaperSACPolicy(nn.Module):
    """Gaussian reparameterized policy with tanh correction."""

    def __init__(
        self,
        state_dim: int = DEFAULT_STATE_DIM,
        action_dim: int = DEFAULT_ACTION_DIM,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
        log_std_min: float = -20.0,
        log_std_max: float = 2.0,
    ) -> None:
        super().__init__()
        self.hidden = _hidden_mlp(state_dim, hidden_dim, hidden_layers=3)
        self.mean = nn.Linear(hidden_dim, action_dim)
        self.log_std = nn.Linear(hidden_dim, action_dim)
        self.log_std_min = log_std_min
        self.log_std_max = log_std_max

    def forward(self, state: Tensor) -> Tuple[Tensor, Tensor]:
        hidden = self.hidden(state)
        mean = self.mean(hidden)
        log_std = self.log_std(hidden).clamp(self.log_std_min, self.log_std_max)
        return mean, log_std

    def sample_normalized_action(
        self, state: Tensor, epsilon: float = 1e-6
    ) -> Tuple[Tensor, Tensor, Tensor, Tensor]:
        mean, log_std = self.forward(state)
        normal = Normal(mean, log_std.exp())
        pre_tanh = normal.rsample()
        normalized_action = torch.tanh(pre_tanh)
        log_prob = normal.log_prob(pre_tanh)
        log_prob -= torch.log(1.0 - normalized_action.pow(2) + epsilon)
        log_prob = log_prob.sum(dim=-1, keepdim=True)
        return normalized_action, log_prob, mean, log_std

    def deterministic_normalized_action(self, state: Tensor) -> Tensor:
        mean, _ = self.forward(state)
        return torch.tanh(mean)

    # Short aliases are intentionally not used for public contract methods;
    # these make integration adapters convenient without changing semantics.
    def sample(self, state: Tensor, epsilon: float = 1e-6):
        return self.sample_normalized_action(state, epsilon=epsilon)

    def deterministic(self, state: Tensor) -> Tensor:
        return self.deterministic_normalized_action(state)

