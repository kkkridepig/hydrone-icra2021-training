"""Explicit observation and action contracts.

The values in this module are the Phase 2 contract from the user-provided
reproduction prompt.  It is deliberately independent of ROS message types.
"""

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class ObservationContract:
    """Contract for the 26-dimensional single-agent observation."""

    scan_beams: int = 20
    previous_action_dim: int = 3
    auxiliary_dim: int = 3  # heading, heading_z, 3-D goal distance
    state_dim: int = 26

    def __post_init__(self) -> None:
        expected = self.scan_beams + self.previous_action_dim + self.auxiliary_dim
        if expected != self.state_dim:
            raise ValueError(
                "observation dimensions are inconsistent: "
                f"{self.scan_beams}+{self.previous_action_dim}+{self.auxiliary_dim} "
                f"!= {self.state_dim}"
            )

    def validate_scan(self, scan: Sequence[float]) -> np.ndarray:
        values = np.asarray(scan, dtype=np.float32)
        if values.shape != (self.scan_beams,):
            raise ValueError(
                f"scan must have shape ({self.scan_beams},), got {values.shape}"
            )
        return values

    def validate_state(self, state: Sequence[float]) -> np.ndarray:
        values = np.asarray(state, dtype=np.float32)
        if values.shape != (self.state_dim,):
            raise ValueError(
                f"state must have shape ({self.state_dim},), got {values.shape}"
            )
        if not np.isfinite(values).all():
            raise ValueError("state contains non-finite values")
        return values


@dataclass(frozen=True)
class ActionContract:
    """Physical and policy-space bounds for the three action components."""

    dim: int = 3
    physical_low: np.ndarray = field(
        default_factory=lambda: np.asarray([0.0, -0.25, -0.25], dtype=np.float32)
    )
    physical_high: np.ndarray = field(
        default_factory=lambda: np.asarray([0.25, 0.25, 0.25], dtype=np.float32)
    )

    def __post_init__(self) -> None:
        low = np.asarray(self.physical_low, dtype=np.float32)
        high = np.asarray(self.physical_high, dtype=np.float32)
        if low.shape != (self.dim,) or high.shape != (self.dim,):
            raise ValueError("action bounds must have shape (3,)")
        if not np.isfinite(low).all() or not np.isfinite(high).all():
            raise ValueError("action bounds must be finite")
        if np.any(low > high):
            raise ValueError("physical action lower bound exceeds upper bound")
        object.__setattr__(self, "physical_low", low)
        object.__setattr__(self, "physical_high", high)

    def validate_physical(self, action: Sequence[float]) -> np.ndarray:
        values = np.asarray(action, dtype=np.float32)
        if values.shape[-1:] != (self.dim,):
            raise ValueError(f"physical action last dimension must be {self.dim}")
        if not np.isfinite(values).all():
            raise ValueError("physical action contains non-finite values")
        if np.any(values < self.physical_low) or np.any(values > self.physical_high):
            raise ValueError("physical action is outside configured bounds")
        return values

    def validate_normalized(self, action: Sequence[float]) -> np.ndarray:
        values = np.asarray(action, dtype=np.float32)
        if values.shape[-1:] != (self.dim,):
            raise ValueError(f"normalized action last dimension must be {self.dim}")
        if not np.isfinite(values).all():
            raise ValueError("normalized action contains non-finite values")
        if np.any(values < -1.0) or np.any(values > 1.0):
            raise ValueError("normalized action is outside [-1, 1]")
        return values

    def clip_physical(self, action: Sequence[float]) -> np.ndarray:
        values = np.asarray(action, dtype=np.float32)
        if values.shape[-1:] != (self.dim,):
            raise ValueError(f"physical action last dimension must be {self.dim}")
        return np.clip(values, self.physical_low, self.physical_high)

    def clip_normalized(self, action: Sequence[float]) -> np.ndarray:
        values = np.asarray(action, dtype=np.float32)
        if values.shape[-1:] != (self.dim,):
            raise ValueError(f"normalized action last dimension must be {self.dim}")
        return np.clip(values, -1.0, 1.0)


DEFAULT_OBSERVATION_CONTRACT = ObservationContract()
DEFAULT_ACTION_CONTRACT = ActionContract()

