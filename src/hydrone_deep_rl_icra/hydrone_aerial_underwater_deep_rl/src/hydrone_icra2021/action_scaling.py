"""Conversions between policy-space and physical actions."""

from typing import Sequence

import numpy as np

from .contracts import DEFAULT_ACTION_CONTRACT, ActionContract


def _as_action_array(action: Sequence[float], contract: ActionContract) -> np.ndarray:
    values = np.asarray(action, dtype=np.float32)
    if values.shape[-1:] != (contract.dim,):
        raise ValueError(f"action last dimension must be {contract.dim}")
    if not np.isfinite(values).all():
        raise ValueError("action contains non-finite values")
    return values


def to_physical_action(
    normalized: Sequence[float],
    contract: ActionContract = DEFAULT_ACTION_CONTRACT,
) -> np.ndarray:
    """Map normalized policy action ``[-1, 1]^3`` to physical bounds.

    Clipping at the policy boundary is intentional: the environment still
    receives a separately named, bounded physical action.
    """

    values = _as_action_array(normalized, contract)
    clipped = contract.clip_normalized(values)
    return contract.physical_low + 0.5 * (clipped + 1.0) * (
        contract.physical_high - contract.physical_low
    )


def to_normalized_action(
    physical: Sequence[float],
    contract: ActionContract = DEFAULT_ACTION_CONTRACT,
) -> np.ndarray:
    """Map a physical action to normalized policy space ``[-1, 1]^3``."""

    values = _as_action_array(physical, contract)
    clipped = contract.clip_physical(values)
    normalized = 2.0 * (clipped - contract.physical_low) / (
        contract.physical_high - contract.physical_low
    ) - 1.0
    return contract.clip_normalized(normalized)

