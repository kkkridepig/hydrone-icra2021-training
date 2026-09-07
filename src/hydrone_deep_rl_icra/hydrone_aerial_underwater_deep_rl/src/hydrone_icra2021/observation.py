"""Pure observation assembly and LaserScan sanitization."""

from typing import Sequence

import numpy as np

from .contracts import DEFAULT_OBSERVATION_CONTRACT, ObservationContract


def sanitize_scan(
    scan: Sequence[float],
    contract: ObservationContract = DEFAULT_OBSERVATION_CONTRACT,
    inf_value: float = 20.0,
    nan_value: float = 0.0,
) -> np.ndarray:
    """Convert a raw 20-beam scan to finite float32 values.

    The Inf/NaN replacement values mirror the current public environment and
    remain explicit so that a later paper configuration can change them.
    """

    values = contract.validate_scan(scan).copy()
    if not np.isfinite(inf_value) or not np.isfinite(nan_value):
        raise ValueError("scan replacement values must be finite")
    values[np.isposinf(values) | np.isneginf(values)] = np.float32(inf_value)
    values[np.isnan(values)] = np.float32(nan_value)
    return values


def build_observation(
    scan: Sequence[float],
    previous_physical_action: Sequence[float],
    heading: float,
    heading_z: float,
    goal_distance: float,
    contract: ObservationContract = DEFAULT_OBSERVATION_CONTRACT,
) -> np.ndarray:
    """Assemble and validate the fixed-order 26-D observation."""

    scan_values = sanitize_scan(scan, contract=contract)
    action_values = np.asarray(previous_physical_action, dtype=np.float32)
    if action_values.shape != (contract.previous_action_dim,):
        raise ValueError(
            "previous physical action must have shape "
            f"({contract.previous_action_dim},), got {action_values.shape}"
        )
    auxiliary = np.asarray([heading, heading_z, goal_distance], dtype=np.float32)
    state = np.concatenate((scan_values, action_values, auxiliary)).astype(
        np.float32, copy=False
    )
    return contract.validate_state(state)

