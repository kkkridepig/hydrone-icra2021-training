"""Independent DDPG agent for the ICRA 2021 paper contract.

The legacy public DDPG script remains a forensic reference.  This module keeps
the new implementation reusable and ROS-free: actions are normalized inside the
agent, converted to physical units only at the environment boundary, and the
observation contract still receives the physical previous action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
from torch import Tensor
from torch.nn import functional as F

from .action_scaling import to_normalized_action, to_physical_action
from .contracts import (
    DEFAULT_ACTION_CONTRACT,
    DEFAULT_OBSERVATION_CONTRACT,
    ActionContract,
    ObservationContract,
)
from .networks import PaperActor, PaperCritic


@dataclass
class DDPGConfig:
    """Training settings with paper values as defaults and smoke overrides."""

    state_dim: int = 26
    action_dim: int = 3
    hidden_dim: int = 512
    learning_rate: float = 1e-3
    gamma: float = 0.99
    tau: float = 1e-3
    replay_capacity: int = 50000
    batch_size: int = 256
    warmup_steps: int = 256
    update_every: int = 1
    noise_theta: float = 0.15
    noise_sigma: float = 0.2
    noise_sigma_min: float = 0.05
    noise_decay_steps: int = 1000000
    seed: int = 0
    device: str = "cpu"

    def __post_init__(self) -> None:
        if self.state_dim != 26 or self.action_dim != 3:
            raise ValueError("DDPG paper contract requires state_dim=26 and action_dim=3")
        if self.hidden_dim < 1:
            raise ValueError("hidden_dim must be positive")
        if self.learning_rate <= 0.0 or not np.isfinite(self.learning_rate):
            raise ValueError("learning_rate must be finite and positive")
        if not 0.0 <= self.gamma <= 1.0:
            raise ValueError("gamma must be in [0, 1]")
        if not 0.0 < self.tau <= 1.0:
            raise ValueError("tau must be in (0, 1]")
        if self.replay_capacity < 1 or self.batch_size < 1:
            raise ValueError("replay_capacity and batch_size must be positive")
        if self.warmup_steps < 0 or self.update_every < 1:
            raise ValueError("warmup_steps must be non-negative and update_every positive")
        if self.noise_sigma < 0.0 or self.noise_sigma_min < 0.0:
            raise ValueError("noise sigmas must be non-negative")
        if self.noise_sigma_min > self.noise_sigma:
            raise ValueError("noise_sigma_min cannot exceed noise_sigma")
        if self.noise_decay_steps < 1:
            raise ValueError("noise_decay_steps must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Transition:
    state: np.ndarray
    action: np.ndarray
    reward: float
    next_state: np.ndarray
    terminated: bool
    truncated: bool


class ReplayBuffer:
    """Bounded replay with explicit terminated/truncated semantics."""

    def __init__(self, capacity: int, seed: int = 0) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = int(capacity)
        self.position = 0
        self.buffer: List[Transition] = []
        self.rng = random.Random(seed)

    def push(
        self,
        state: Sequence[float],
        action: Sequence[float],
        reward: float,
        next_state: Sequence[float],
        terminated: bool,
        truncated: bool = False,
    ) -> None:
        state_array = np.asarray(state, dtype=np.float32).copy()
        action_array = np.asarray(action, dtype=np.float32).copy()
        next_state_array = np.asarray(next_state, dtype=np.float32).copy()
        if state_array.shape != (26,) or next_state_array.shape != (26,):
            raise ValueError("replay states must have shape (26,)")
        if action_array.shape != (3,):
            raise ValueError("replay actions must have shape (3,)")
        if not np.isfinite(state_array).all() or not np.isfinite(action_array).all():
            raise ValueError("replay state/action contains non-finite values")
        if not np.isfinite(next_state_array).all() or not np.isfinite(reward):
            raise ValueError("replay transition contains non-finite values")
        transition = Transition(
            state_array,
            action_array,
            float(reward),
            next_state_array,
            bool(terminated),
            bool(truncated),
        )
        if len(self.buffer) < self.capacity:
            self.buffer.append(transition)
        else:
            self.buffer[self.position] = transition
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size: int) -> Tuple[np.ndarray, ...]:
        if batch_size < 1 or len(self.buffer) < batch_size:
            raise ValueError(
                f"cannot sample batch_size={batch_size} from replay size={len(self.buffer)}"
            )
        batch = self.rng.sample(self.buffer, batch_size)
        return (
            np.stack([item.state for item in batch]),
            np.stack([item.action for item in batch]),
            np.asarray([item.reward for item in batch], dtype=np.float32),
            np.stack([item.next_state for item in batch]),
            np.asarray([item.terminated for item in batch], dtype=np.float32),
            np.asarray([item.truncated for item in batch], dtype=np.float32),
        )

    def state_dict(self) -> Dict[str, Any]:
        return {
            "capacity": self.capacity,
            "position": self.position,
            "buffer": [
                {
                    "state": item.state,
                    "action": item.action,
                    "reward": item.reward,
                    "next_state": item.next_state,
                    "terminated": item.terminated,
                    "truncated": item.truncated,
                }
                for item in self.buffer
            ],
            "rng_state": self.rng.getstate(),
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        if int(state["capacity"]) != self.capacity:
            raise ValueError(
                f"replay capacity mismatch: checkpoint={state['capacity']} agent={self.capacity}"
            )
        entries = state["buffer"]
        if len(entries) > self.capacity:
            raise ValueError("checkpoint replay exceeds configured capacity")
        self.buffer = []
        for item in entries:
            self.push(
                item["state"],
                item["action"],
                item["reward"],
                item["next_state"],
                item["terminated"],
                item.get("truncated", False),
            )
        self.position = int(state["position"]) % self.capacity
        self.rng.setstate(state["rng_state"])

    def __len__(self) -> int:
        return len(self.buffer)


class OUNoise:
    """Small normalized-action OU process matching the public algorithm family."""

    def __init__(
        self,
        action_dim: int,
        theta: float,
        sigma: float,
        sigma_min: float,
        decay_steps: int,
        seed: int,
    ) -> None:
        self.action_dim = int(action_dim)
        self.theta = float(theta)
        self.max_sigma = float(sigma)
        self.sigma_min = float(sigma_min)
        self.decay_steps = int(decay_steps)
        self.sigma = self.max_sigma
        self.rng = np.random.default_rng(seed)
        self.state = np.zeros(self.action_dim, dtype=np.float32)

    def reset(self) -> None:
        self.state.fill(0.0)

    def sample(self, step: int) -> np.ndarray:
        if step < 0:
            raise ValueError("noise step must be non-negative")
        noise = self.rng.standard_normal(self.action_dim).astype(np.float32)
        self.state += self.theta * (0.0 - self.state) + self.sigma * noise
        fraction = min(1.0, float(step) / float(self.decay_steps))
        self.sigma = max(
            self.sigma_min,
            self.max_sigma - (self.max_sigma - self.sigma_min) * fraction,
        )
        return self.state.copy()

    def state_dict(self) -> Dict[str, Any]:
        return {
            "action_dim": self.action_dim,
            "theta": self.theta,
            "max_sigma": self.max_sigma,
            "sigma_min": self.sigma_min,
            "decay_steps": self.decay_steps,
            "sigma": self.sigma,
            "state": self.state,
            "rng_state": copy.deepcopy(self.rng.bit_generator.state),
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        if int(state["action_dim"]) != self.action_dim:
            raise ValueError("OU action dimension mismatch")
        self.sigma = float(state["sigma"])
        self.state = np.asarray(state["state"], dtype=np.float32).copy()
        if self.state.shape != (self.action_dim,):
            raise ValueError("OU state shape mismatch")
        self.rng.bit_generator.state = copy.deepcopy(state["rng_state"])


class DDPGAgent:
    """Paper-contract DDPG with physical/normalized action separation."""

    checkpoint_format = "hydrone_icra2021_ddpg_v1"

    def __init__(
        self,
        config: DDPGConfig = DDPGConfig(),
        observation_contract: ObservationContract = DEFAULT_OBSERVATION_CONTRACT,
        action_contract: ActionContract = DEFAULT_ACTION_CONTRACT,
    ) -> None:
        self.config = config
        self.observation_contract = observation_contract
        self.action_contract = action_contract
        if config.state_dim != observation_contract.state_dim:
            raise ValueError("DDPG state dimension does not match observation contract")
        if config.action_dim != action_contract.dim:
            raise ValueError("DDPG action dimension does not match action contract")
        self.device = torch.device(config.device)
        torch.manual_seed(config.seed)
        self.actor = PaperActor(config.state_dim, config.action_dim, config.hidden_dim).to(self.device)
        self.target_actor = PaperActor(config.state_dim, config.action_dim, config.hidden_dim).to(self.device)
        self.critic = PaperCritic(config.state_dim, config.action_dim, config.hidden_dim).to(self.device)
        self.target_critic = PaperCritic(config.state_dim, config.action_dim, config.hidden_dim).to(self.device)
        self.target_actor.load_state_dict(self.actor.state_dict())
        self.target_critic.load_state_dict(self.critic.state_dict())
        self.actor_optimizer = torch.optim.Adam(
            self.actor.parameters(), lr=config.learning_rate
        )
        self.critic_optimizer = torch.optim.Adam(
            self.critic.parameters(), lr=config.learning_rate
        )
        self.replay = ReplayBuffer(config.replay_capacity, seed=config.seed)
        self.noise = OUNoise(
            config.action_dim,
            config.noise_theta,
            config.noise_sigma,
            config.noise_sigma_min,
            config.noise_decay_steps,
            seed=config.seed,
        )
        self.global_step = 0
        self.update_count = 0
        self.last_metrics: Dict[str, float] = {}

    def _state_tensor(self, state: Sequence[float]) -> Tensor:
        values = self.observation_contract.validate_state(state)
        return torch.as_tensor(values, dtype=torch.float32, device=self.device).unsqueeze(0)

    def select_normalized_action(
        self, state: Sequence[float], explore: bool = True
    ) -> np.ndarray:
        with torch.no_grad():
            action = self.actor(self._state_tensor(state))[0].cpu().numpy()
        if explore:
            action = action + self.noise.sample(self.global_step)
        action = self.action_contract.clip_normalized(action)
        self.action_contract.validate_normalized(action)
        return action.astype(np.float32, copy=False)

    def deterministic_normalized_action(self, state: Sequence[float]) -> np.ndarray:
        """Return the normalized actor output without advancing OU noise."""

        return self.select_normalized_action(state, explore=False)

    def select_action(self, state: Sequence[float], explore: bool = True) -> np.ndarray:
        """Return the bounded physical action for ``PaperEnvironment.step``."""

        normalized = self.select_normalized_action(state, explore=explore)
        return self.to_physical_action(normalized)

    def to_physical_action(self, normalized: Sequence[float]) -> np.ndarray:
        """Convert a normalized actor action to the environment's physical units."""

        return self.action_contract.validate_physical(
            to_physical_action(normalized, contract=self.action_contract)
        )

    def to_normalized_action(self, physical: Sequence[float]) -> np.ndarray:
        """Convert a physical command back to normalized actor space."""

        return self.action_contract.validate_normalized(
            to_normalized_action(physical, contract=self.action_contract)
        )

    def observe(
        self,
        state: Sequence[float],
        normalized_action: Sequence[float],
        reward: float,
        next_state: Sequence[float],
        terminated: bool,
        truncated: bool = False,
    ) -> Optional[Dict[str, float]]:
        self.observation_contract.validate_state(state)
        self.observation_contract.validate_state(next_state)
        self.action_contract.validate_normalized(normalized_action)
        self.replay.push(
            state,
            normalized_action,
            reward,
            next_state,
            terminated,
            truncated,
        )
        self.global_step += 1
        if (
            len(self.replay) < max(self.config.batch_size, self.config.warmup_steps)
            or self.global_step % self.config.update_every != 0
        ):
            return None
        metrics = self.update()
        self.last_metrics = metrics
        return metrics

    @staticmethod
    def _assert_finite(name: str, value: Tensor) -> None:
        if not torch.isfinite(value).all():
            raise FloatingPointError(f"non-finite {name}")

    def _soft_update(self, target: torch.nn.Module, source: torch.nn.Module) -> None:
        tau = self.config.tau
        with torch.no_grad():
            for target_param, source_param in zip(target.parameters(), source.parameters()):
                target_param.mul_(1.0 - tau).add_(source_param, alpha=tau)

    def update(self) -> Dict[str, float]:
        (
            states,
            actions,
            rewards,
            next_states,
            terminated,
            truncated,
        ) = self.replay.sample(self.config.batch_size)
        del truncated  # time-limit transitions bootstrap like non-terminal steps
        state_batch = torch.as_tensor(states, dtype=torch.float32, device=self.device)
        action_batch = torch.as_tensor(actions, dtype=torch.float32, device=self.device)
        reward_batch = torch.as_tensor(rewards, dtype=torch.float32, device=self.device).unsqueeze(-1)
        next_state_batch = torch.as_tensor(next_states, dtype=torch.float32, device=self.device)
        terminated_batch = torch.as_tensor(terminated, dtype=torch.float32, device=self.device).unsqueeze(-1)

        with torch.no_grad():
            next_action = self.target_actor(next_state_batch)
            next_value = self.target_critic(next_state_batch, next_action)
            target_value = reward_batch + (1.0 - terminated_batch) * self.config.gamma * next_value
        predicted_value = self.critic(state_batch, action_batch)
        critic_loss = F.mse_loss(predicted_value, target_value)
        self._assert_finite("critic loss", critic_loss)
        self.critic_optimizer.zero_grad(set_to_none=True)
        critic_loss.backward()
        self.critic_optimizer.step()

        for parameter in self.critic.parameters():
            parameter.requires_grad_(False)
        try:
            actor_action = self.actor(state_batch)
            actor_loss = -self.critic(state_batch, actor_action).mean()
            self._assert_finite("actor loss", actor_loss)
            self.actor_optimizer.zero_grad(set_to_none=True)
            actor_loss.backward()
            self.actor_optimizer.step()
        finally:
            for parameter in self.critic.parameters():
                parameter.requires_grad_(True)

        self._soft_update(self.target_actor, self.actor)
        self._soft_update(self.target_critic, self.critic)
        self.update_count += 1
        metrics = {
            "critic_loss": float(critic_loss.detach().cpu().item()),
            "actor_loss": float(actor_loss.detach().cpu().item()),
            "q_mean": float(predicted_value.detach().mean().cpu().item()),
            "target_mean": float(target_value.detach().mean().cpu().item()),
        }
        if not all(np.isfinite(value) for value in metrics.values()):
            raise FloatingPointError("non-finite DDPG metrics")
        self.last_metrics = metrics
        return metrics

    def checkpoint_dict(
        self,
        manifest: Optional[Dict[str, Any]] = None,
        stage: int = 1,
        episode: int = 0,
        metrics: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        return {
            "format": self.checkpoint_format,
            "algorithm": "ddpg",
            "stage": int(stage),
            "episode": int(episode),
            "config": self.config.to_dict(),
            "observation_contract": {
                "scan_beams": self.observation_contract.scan_beams,
                "previous_action_dim": self.observation_contract.previous_action_dim,
                "auxiliary_dim": self.observation_contract.auxiliary_dim,
                "state_dim": self.observation_contract.state_dim,
            },
            "action_contract": {
                "dim": self.action_contract.dim,
                "physical_low": self.action_contract.physical_low.copy(),
                "physical_high": self.action_contract.physical_high.copy(),
            },
            "actor": self.actor.state_dict(),
            "target_actor": self.target_actor.state_dict(),
            "critic": self.critic.state_dict(),
            "target_critic": self.target_critic.state_dict(),
            "actor_optimizer": self.actor_optimizer.state_dict(),
            "critic_optimizer": self.critic_optimizer.state_dict(),
            "replay": self.replay.state_dict(),
            "noise": self.noise.state_dict(),
            "global_step": self.global_step,
            "update_count": self.update_count,
            "python_random_state": random.getstate(),
            "numpy_random_state": np.random.get_state(),
            "torch_random_state": torch.get_rng_state(),
            "metrics": copy.deepcopy(metrics if metrics is not None else self.last_metrics),
            "manifest": manifest or {},
        }

    def save_checkpoint(
        self,
        path: str,
        manifest: Optional[Dict[str, Any]] = None,
        stage: int = 1,
        episode: int = 0,
        metrics: Optional[Dict[str, float]] = None,
    ) -> None:
        payload = self.checkpoint_dict(
            manifest=manifest,
            stage=stage,
            episode=episode,
            metrics=metrics,
        )
        torch.save(payload, path)

    def load_checkpoint(self, path: str, map_location: str = "cpu") -> Dict[str, Any]:
        payload = torch.load(path, map_location=map_location, weights_only=False)
        if payload.get("format") != self.checkpoint_format:
            raise ValueError(f"unsupported DDPG checkpoint format: {payload.get('format')}")
        checkpoint_config = payload.get("config", {})
        for field_name in ("state_dim", "action_dim", "hidden_dim", "replay_capacity"):
            if checkpoint_config.get(field_name) != getattr(self.config, field_name):
                raise ValueError(
                    f"checkpoint/config mismatch for {field_name}: "
                    f"checkpoint={checkpoint_config.get(field_name)} "
                    f"agent={getattr(self.config, field_name)}"
                )
        self.actor.load_state_dict(payload["actor"], strict=True)
        self.target_actor.load_state_dict(payload["target_actor"], strict=True)
        self.critic.load_state_dict(payload["critic"], strict=True)
        self.target_critic.load_state_dict(payload["target_critic"], strict=True)
        self.actor_optimizer.load_state_dict(payload["actor_optimizer"])
        self.critic_optimizer.load_state_dict(payload["critic_optimizer"])
        self.replay.load_state_dict(payload["replay"])
        self.noise.load_state_dict(payload["noise"])
        self.global_step = int(payload["global_step"])
        self.update_count = int(payload["update_count"])
        self.last_metrics = dict(payload.get("metrics", {}))
        random.setstate(payload["python_random_state"])
        np.random.set_state(payload["numpy_random_state"])
        torch.set_rng_state(payload["torch_random_state"])
        return dict(payload.get("manifest", {}))
