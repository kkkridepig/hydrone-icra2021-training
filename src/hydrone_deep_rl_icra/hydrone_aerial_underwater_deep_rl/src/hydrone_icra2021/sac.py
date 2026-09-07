"""Independent SAC agent for the ICRA 2021 paper contract.

The legacy ``scripts/sac_air3D_tanh_3layers.py`` remains a forensic
reference.  This module keeps the new implementation ROS-free and makes the
policy-space/physical-space boundary explicit:

* policy, Q networks, and replay store normalized actions in ``[-1, 1]``;
* the environment receives bounded physical actions;
* the 26-D observation stores the previous *physical* action.

The implementation is deliberately small enough to unit test without ROS,
while still carrying a complete resume checkpoint (including entropy
temperature, optimizers, replay, and random-number-generator state).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import random
from typing import Any, Dict, Optional, Sequence

import numpy as np
import torch
from torch import Tensor
from torch.nn import functional as F

from .action_scaling import (
    to_normalized_action as _to_normalized_action,
    to_physical_action as _to_physical_action,
)
from .contracts import (
    DEFAULT_ACTION_CONTRACT,
    DEFAULT_OBSERVATION_CONTRACT,
    ActionContract,
    ObservationContract,
)
from .ddpg import ReplayBuffer
from .networks import PaperSACPolicy, PaperTwinQ


@dataclass
class SACConfig:
    """SAC settings.

    Defaults follow the paper-contract decisions captured in
    ``REPRODUCTION_NOTES.md``.  The public script's different values remain
    available only as forensic reference in ``icra2021_upstream_reference``.
    """

    state_dim: int = 26
    action_dim: int = 3
    hidden_dim: int = 512
    critic_hidden_layers: int = 2
    learning_rate: float = 1e-3
    gamma: float = 0.99
    tau: float = 1e-2
    alpha: float = 0.2
    automatic_entropy_tuning: bool = True
    target_entropy: Optional[float] = None
    log_std_min: float = -20.0
    log_std_max: float = 2.0
    replay_capacity: int = 50000
    batch_size: int = 256
    warmup_steps: int = 256
    update_every: int = 1
    goal_transition_oversampling: int = 3
    seed: int = 0
    device: str = "cpu"

    def __post_init__(self) -> None:
        if self.state_dim != 26 or self.action_dim != 3:
            raise ValueError("SAC paper contract requires state_dim=26 and action_dim=3")
        if self.hidden_dim < 1 or self.critic_hidden_layers < 1:
            raise ValueError("network widths and critic depth must be positive")
        if self.learning_rate <= 0.0 or not np.isfinite(self.learning_rate):
            raise ValueError("learning_rate must be finite and positive")
        if not 0.0 <= self.gamma <= 1.0:
            raise ValueError("gamma must be in [0, 1]")
        if not 0.0 < self.tau <= 1.0:
            raise ValueError("tau must be in (0, 1]")
        if self.alpha <= 0.0 or not np.isfinite(self.alpha):
            raise ValueError("alpha must be finite and positive")
        if self.target_entropy is not None and not np.isfinite(self.target_entropy):
            raise ValueError("target_entropy must be finite")
        if self.replay_capacity < 1 or self.batch_size < 1:
            raise ValueError("replay_capacity and batch_size must be positive")
        if self.warmup_steps < 0 or self.update_every < 1:
            raise ValueError("warmup_steps must be non-negative and update_every positive")
        if self.goal_transition_oversampling < 1:
            raise ValueError("goal_transition_oversampling must be positive")
        if self.log_std_min >= self.log_std_max:
            raise ValueError("log_std_min must be smaller than log_std_max")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SACAgent:
    """Paper-contract SAC with explicit normalized/physical action methods."""

    checkpoint_format = "hydrone_icra2021_sac_v1"
    checkpoint_schema_version = 1

    def __init__(
        self,
        config: SACConfig = SACConfig(),
        observation_contract: ObservationContract = DEFAULT_OBSERVATION_CONTRACT,
        action_contract: ActionContract = DEFAULT_ACTION_CONTRACT,
    ) -> None:
        self.config = config
        self.observation_contract = observation_contract
        self.action_contract = action_contract
        if config.state_dim != observation_contract.state_dim:
            raise ValueError("SAC state dimension does not match observation contract")
        if config.action_dim != action_contract.dim:
            raise ValueError("SAC action dimension does not match action contract")

        # Seed all RNGs before constructing modules so initialization is
        # reproducible across independent smoke-test processes.
        random.seed(config.seed)
        np.random.seed(config.seed)
        torch.manual_seed(config.seed)
        if torch.cuda.is_available():  # pragma: no cover - depends on host GPU
            torch.cuda.manual_seed_all(config.seed)

        self.device = torch.device(config.device)
        self.policy = PaperSACPolicy(
            config.state_dim,
            config.action_dim,
            config.hidden_dim,
            log_std_min=config.log_std_min,
            log_std_max=config.log_std_max,
        ).to(self.device)
        self.critic = PaperTwinQ(
            config.state_dim,
            config.action_dim,
            config.hidden_dim,
            hidden_layers=config.critic_hidden_layers,
        ).to(self.device)
        self.target_critic = PaperTwinQ(
            config.state_dim,
            config.action_dim,
            config.hidden_dim,
            hidden_layers=config.critic_hidden_layers,
        ).to(self.device)
        self.target_critic.load_state_dict(self.critic.state_dict())

        self.policy_optimizer = torch.optim.Adam(
            self.policy.parameters(), lr=config.learning_rate
        )
        self.critic_optimizer = torch.optim.Adam(
            self.critic.parameters(), lr=config.learning_rate
        )

        initial_log_alpha = float(np.log(config.alpha))
        self.log_alpha = torch.tensor(
            [initial_log_alpha], dtype=torch.float32, device=self.device,
            requires_grad=config.automatic_entropy_tuning,
        )
        if config.automatic_entropy_tuning:
            self.alpha_optimizer = torch.optim.Adam(
                [self.log_alpha], lr=config.learning_rate
            )
        else:
            self.alpha_optimizer = None
        self.target_entropy = (
            float(config.target_entropy)
            if config.target_entropy is not None
            else -float(config.action_dim)
        )

        self.replay = ReplayBuffer(config.replay_capacity, seed=config.seed)
        self.global_step = 0
        self.update_count = 0
        self.last_metrics: Dict[str, float] = {}

    @property
    def alpha(self) -> float:
        """Current entropy coefficient as a finite Python scalar."""

        return float(self.log_alpha.detach().exp().cpu().item())

    def _alpha_tensor(self) -> Tensor:
        return self.log_alpha.exp()

    def _config_hash(self) -> str:
        serialized = json.dumps(
            self.config.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def _state_tensor(self, state: Sequence[float]) -> Tensor:
        values = self.observation_contract.validate_state(state)
        return torch.as_tensor(values, dtype=torch.float32, device=self.device).unsqueeze(0)

    def sample_normalized_action(self, state: Sequence[float]) -> np.ndarray:
        """Sample a stochastic policy action in normalized space ``[-1, 1]``."""

        with torch.no_grad():
            normalized = self.policy.sample_normalized_action(self._state_tensor(state))[0]
        values = normalized[0].cpu().numpy()
        values = self.action_contract.clip_normalized(values)
        return self.action_contract.validate_normalized(values).astype(np.float32, copy=False)

    def deterministic_normalized_action(self, state: Sequence[float]) -> np.ndarray:
        """Return deterministic ``tanh(mean)`` policy output in normalized space."""

        with torch.no_grad():
            normalized = self.policy.deterministic_normalized_action(self._state_tensor(state))
        values = normalized[0].cpu().numpy()
        values = self.action_contract.clip_normalized(values)
        return self.action_contract.validate_normalized(values).astype(np.float32, copy=False)

    def to_physical_action(self, normalized: Sequence[float]) -> np.ndarray:
        """Convert a normalized policy action to bounded physical units."""

        return self.action_contract.validate_physical(
            _to_physical_action(normalized, contract=self.action_contract)
        )

    def to_normalized_action(self, physical: Sequence[float]) -> np.ndarray:
        """Convert bounded physical units to normalized policy space."""

        return self.action_contract.validate_normalized(
            _to_normalized_action(physical, contract=self.action_contract)
        )

    def select_normalized_action(self, state: Sequence[float], explore: bool = True) -> np.ndarray:
        """Compatibility adapter returning stochastic or deterministic normalized action."""

        return self.sample_normalized_action(state) if explore else self.deterministic_normalized_action(state)

    def select_action(self, state: Sequence[float], explore: bool = True) -> np.ndarray:
        """Return the bounded physical command accepted by ``PaperEnvironment``."""

        return self.to_physical_action(self.select_normalized_action(state, explore=explore))

    @staticmethod
    def _assert_finite(name: str, value: Tensor) -> None:
        if not torch.isfinite(value).all():
            raise FloatingPointError("non-finite " + name)

    def observe(
        self,
        state: Sequence[float],
        normalized_action: Sequence[float],
        reward: float,
        next_state: Sequence[float],
        terminated: bool,
        truncated: bool = False,
    ) -> Optional[Dict[str, float]]:
        """Store one environment transition and update when warmup permits.

        Reward-100 transitions are intentionally inserted three times by
        default, matching the public loop's oversampling behavior.  The global
        environment step is incremented once, not once per replay copy.
        """

        self.observation_contract.validate_state(state)
        self.observation_contract.validate_state(next_state)
        self.action_contract.validate_normalized(normalized_action)
        repeat = (
            self.config.goal_transition_oversampling
            if float(reward) == 100.0
            else 1
        )
        for _ in range(repeat):
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

    def _soft_update(self) -> None:
        tau = self.config.tau
        with torch.no_grad():
            for target_param, source_param in zip(
                self.target_critic.parameters(), self.critic.parameters()
            ):
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
        # Time-limit truncation bootstraps like a non-terminal transition.
        del truncated
        state_batch = torch.as_tensor(states, dtype=torch.float32, device=self.device)
        action_batch = torch.as_tensor(actions, dtype=torch.float32, device=self.device)
        reward_batch = torch.as_tensor(rewards, dtype=torch.float32, device=self.device).unsqueeze(-1)
        next_state_batch = torch.as_tensor(next_states, dtype=torch.float32, device=self.device)
        terminated_batch = torch.as_tensor(terminated, dtype=torch.float32, device=self.device).unsqueeze(-1)

        alpha = self._alpha_tensor().detach()
        with torch.no_grad():
            next_action, next_log_prob, _, _ = self.policy.sample_normalized_action(next_state_batch)
            next_q1, next_q2 = self.target_critic(next_state_batch, next_action)
            next_q = torch.min(next_q1, next_q2) - alpha * next_log_prob
            target_q = reward_batch + (1.0 - terminated_batch) * self.config.gamma * next_q

        q1, q2 = self.critic(state_batch, action_batch)
        q1_loss = F.mse_loss(q1, target_q)
        q2_loss = F.mse_loss(q2, target_q)
        critic_loss = q1_loss + q2_loss
        self._assert_finite("critic loss", critic_loss)
        self.critic_optimizer.zero_grad(set_to_none=True)
        critic_loss.backward()
        self.critic_optimizer.step()

        for parameter in self.critic.parameters():
            parameter.requires_grad_(False)
        try:
            policy_action, log_prob, _, _ = self.policy.sample_normalized_action(state_batch)
            policy_q1, policy_q2 = self.critic(state_batch, policy_action)
            min_policy_q = torch.min(policy_q1, policy_q2)
            policy_loss = (alpha * log_prob - min_policy_q).mean()
            self._assert_finite("policy loss", policy_loss)
            self.policy_optimizer.zero_grad(set_to_none=True)
            policy_loss.backward()
            self.policy_optimizer.step()
        finally:
            for parameter in self.critic.parameters():
                parameter.requires_grad_(True)

        if self.config.automatic_entropy_tuning:
            alpha_loss = -(
                self.log_alpha * (log_prob + self.target_entropy).detach()
            ).mean()
            self._assert_finite("alpha loss", alpha_loss)
            assert self.alpha_optimizer is not None
            self.alpha_optimizer.zero_grad(set_to_none=True)
            alpha_loss.backward()
            self.alpha_optimizer.step()
        else:
            alpha_loss = torch.zeros((), dtype=torch.float32, device=self.device)

        self._soft_update()
        self.update_count += 1
        metrics = {
            "q1_loss": float(q1_loss.detach().cpu().item()),
            "q2_loss": float(q2_loss.detach().cpu().item()),
            "critic_loss": float(critic_loss.detach().cpu().item()),
            "policy_loss": float(policy_loss.detach().cpu().item()),
            "alpha_loss": float(alpha_loss.detach().cpu().item()),
            "alpha": self.alpha,
            "log_prob_mean": float(log_prob.detach().mean().cpu().item()),
            "q_mean": float(torch.min(q1, q2).detach().mean().cpu().item()),
            "target_mean": float(target_q.detach().mean().cpu().item()),
        }
        if not all(np.isfinite(value) for value in metrics.values()):
            raise FloatingPointError("non-finite SAC metrics")
        return metrics

    def checkpoint_dict(
        self,
        stage: int = 1,
        episode: int = 0,
        metrics: Optional[Dict[str, float]] = None,
        manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return one strict, self-contained resume dictionary."""

        manifest = manifest or {}
        return {
            "schema_version": self.checkpoint_schema_version,
            "format": self.checkpoint_format,
            "algorithm": "sac",
            "stage": int(stage),
            "episode": int(episode),
            "global_step": int(self.global_step),
            "update_count": int(self.update_count),
            "config": self.config.to_dict(),
            "config_hash": self._config_hash(),
            "git": copy.deepcopy(manifest.get("git", {})),
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
                "normalized_low": -1.0,
                "normalized_high": 1.0,
                "policy_action_semantics": "normalized",
                "environment_action_semantics": "physical",
                "observation_previous_action_semantics": "physical",
            },
            "policy": self.policy.state_dict(),
            # The generic key makes algorithm-independent checkpoint tooling
            # possible while retaining the descriptive policy key above.
            "actor_or_policy": self.policy.state_dict(),
            "critic": self.critic.state_dict(),
            "target_networks": {"critic": self.target_critic.state_dict()},
            "policy_optimizer": self.policy_optimizer.state_dict(),
            "critic_optimizer": self.critic_optimizer.state_dict(),
            "alpha_optimizer": (
                self.alpha_optimizer.state_dict()
                if self.alpha_optimizer is not None
                else None
            ),
            "optimizers": {
                "policy": self.policy_optimizer.state_dict(),
                "critic": self.critic_optimizer.state_dict(),
                "alpha": (
                    self.alpha_optimizer.state_dict()
                    if self.alpha_optimizer is not None
                    else None
                ),
            },
            "log_alpha": self.log_alpha.detach().cpu().clone(),
            "alpha": self.alpha,
            "replay": self.replay.state_dict(),
            "rng_state": {
                "python": random.getstate(),
                "numpy": np.random.get_state(),
                "torch": torch.get_rng_state(),
            },
            # Legacy-compatible aliases are useful to existing experiment
            # inspection scripts and do not change the strict contract above.
            "python_random_state": random.getstate(),
            "numpy_random_state": np.random.get_state(),
            "torch_random_state": torch.get_rng_state(),
            "metrics": copy.deepcopy(metrics if metrics is not None else self.last_metrics),
            "manifest": copy.deepcopy(manifest),
        }

    def save_checkpoint(
        self,
        path: str,
        stage: int = 1,
        episode: int = 0,
        metrics: Optional[Dict[str, float]] = None,
        manifest: Optional[Dict[str, Any]] = None,
    ) -> None:
        torch.save(
            self.checkpoint_dict(
                stage=stage, episode=episode, metrics=metrics, manifest=manifest
            ),
            path,
        )

    def load_checkpoint(self, path: str, map_location: str = "cpu") -> Dict[str, Any]:
        try:
            payload = torch.load(path, map_location=map_location, weights_only=False)
        except TypeError:  # pragma: no cover - older torch compatibility
            payload = torch.load(path, map_location=map_location)
        if not isinstance(payload, dict):
            raise ValueError("SAC checkpoint must contain a dictionary")
        if payload.get("schema_version") != self.checkpoint_schema_version:
            raise ValueError("unsupported SAC checkpoint schema")
        if payload.get("algorithm") != "sac" or payload.get("format") != self.checkpoint_format:
            raise ValueError("checkpoint is not a Hydrone ICRA2021 SAC checkpoint")
        checkpoint_config = payload.get("config", {})
        checkpoint_hash = payload.get("config_hash")
        if checkpoint_hash != self._config_hash():
            raise ValueError("checkpoint/config hash mismatch")
        for field_name in (
            "state_dim",
            "action_dim",
            "hidden_dim",
            "critic_hidden_layers",
            "replay_capacity",
        ):
            if checkpoint_config.get(field_name) != getattr(self.config, field_name):
                raise ValueError(
                    "checkpoint/config mismatch for %s: checkpoint=%r agent=%r"
                    % (field_name, checkpoint_config.get(field_name), getattr(self.config, field_name))
                )
        contract = payload.get("observation_contract", {})
        if contract.get("state_dim") != self.observation_contract.state_dim:
            raise ValueError("checkpoint observation contract mismatch")
        action_contract = payload.get("action_contract", {})
        if action_contract.get("dim") != self.action_contract.dim:
            raise ValueError("checkpoint action contract mismatch")

        self.policy.load_state_dict(payload["policy"], strict=True)
        self.critic.load_state_dict(payload["critic"], strict=True)
        self.target_critic.load_state_dict(payload["target_networks"]["critic"], strict=True)
        self.policy_optimizer.load_state_dict(payload["policy_optimizer"])
        self.critic_optimizer.load_state_dict(payload["critic_optimizer"])
        loaded_log_alpha = torch.as_tensor(payload["log_alpha"], dtype=torch.float32, device=self.device)
        if loaded_log_alpha.shape != self.log_alpha.shape:
            raise ValueError("checkpoint log_alpha shape mismatch")
        self.log_alpha.data.copy_(loaded_log_alpha)
        if self.alpha_optimizer is not None:
            if payload.get("alpha_optimizer") is None:
                raise ValueError("checkpoint is missing automatic alpha optimizer state")
            self.alpha_optimizer.load_state_dict(payload["alpha_optimizer"])
        self.replay.load_state_dict(payload["replay"])
        self.global_step = int(payload["global_step"])
        self.update_count = int(payload.get("metrics", {}).get("update_count", payload.get("update_count", 0)))
        rng_state = payload.get("rng_state", {})
        random.setstate(rng_state.get("python", payload["python_random_state"]))
        np.random.set_state(rng_state.get("numpy", payload["numpy_random_state"]))
        torch.set_rng_state(rng_state.get("torch", payload["torch_random_state"]))
        self.last_metrics = dict(payload.get("metrics", {}))
        return dict(payload.get("manifest", {}))
