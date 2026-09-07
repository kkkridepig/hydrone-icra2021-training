"""ROS-free helpers for the ICRA2021 experiment runner.

The helpers in this module keep configuration inheritance and reporting logic
testable without importing ``rospy`` or starting Gazebo.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` over ``base`` without mutating either."""

    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml_with_base(path: str, _stack: Optional[Sequence[Path]] = None) -> Dict[str, Any]:
    """Load one YAML mapping and recursively merge its relative base config."""

    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ValueError("config does not exist: %s" % config_path)
    stack = list(_stack or ())
    if config_path in stack:
        chain = " -> ".join(str(item) for item in stack + [config_path])
        raise ValueError("cyclic base_config chain: %s" % chain)
    with config_path.open("r") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("config must contain a YAML mapping: %s" % config_path)
    base_name = data.get("base_config")
    if not base_name:
        return copy.deepcopy(data)
    base_path = Path(str(base_name))
    if not base_path.is_absolute():
        base_path = config_path.parent / base_path
    base = load_yaml_with_base(str(base_path), stack + [config_path])
    return deep_merge(base, data)


def moving_average(values: Iterable[float], window: int) -> Optional[float]:
    """Return the mean of the last ``window`` values, or ``None`` if empty."""

    if window < 1:
        raise ValueError("moving-average window must be positive")
    recent: List[float] = list(values)[-window:]
    if not recent:
        return None
    result = sum(float(value) for value in recent) / float(len(recent))
    if not math.isfinite(result):
        raise FloatingPointError("moving average is non-finite")
    return result


def real_time_factor(sim_elapsed: float, wall_elapsed: float) -> Optional[float]:
    """Compute sim-time / wall-time, returning ``None`` before the clock starts."""

    sim_value = float(sim_elapsed)
    wall_value = float(wall_elapsed)
    if not math.isfinite(sim_value) or not math.isfinite(wall_value):
        raise ValueError("elapsed times must be finite")
    if wall_value <= 0.0 or sim_value < 0.0:
        raise ValueError("elapsed times must be non-negative and wall time positive")
    if sim_value == 0.0:
        return None
    value = sim_value / wall_value
    if not math.isfinite(value):
        raise FloatingPointError("real-time factor is non-finite")
    return value

