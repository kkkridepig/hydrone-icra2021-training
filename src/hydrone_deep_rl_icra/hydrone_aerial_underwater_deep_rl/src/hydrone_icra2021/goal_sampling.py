"""Deterministic, ROS-free public goal sampling for Stage 1/2."""

from dataclasses import dataclass, field
import math
import random
from typing import Optional, Sequence, Tuple


Goal = Tuple[float, float, float]


@dataclass(frozen=True)
class GoalSamplingConfig:
    """Configuration matching the public ``respawnGoal_3D.py`` grid."""

    x_values: Tuple[float, ...] = tuple(i / 10.0 for i in range(40))
    y_values: Tuple[float, ...] = tuple(i / 10.0 for i in range(-40, 40))
    z_values: Tuple[float, ...] = tuple(i / 10.0 for i in range(5, 40))
    origin_x_clearance: float = 0.6
    origin_y_clearance: float = 0.6
    obstacle_centers: Tuple[Tuple[float, float], ...] = (
        (2.0, 2.0),
        (-2.0, -2.0),
        (2.0, -2.0),
        (-2.0, 2.0),
    )
    obstacle_radius: float = 0.6
    obstacle_margin: float = 0.0
    obstacle_z_min: float = -1.5
    obstacle_z_max: float = 5.5
    max_attempts: int = 1000


class PublicGoalSampler:
    """Sample public-code-compatible goals with optional Stage 2 exclusion."""

    def __init__(
        self,
        stage: int = 1,
        mode: str = "upstream_public",
        rng: Optional[random.Random] = None,
        config: GoalSamplingConfig = GoalSamplingConfig(),
    ) -> None:
        if stage not in (1, 2):
            raise ValueError("stage must be 1 or 2")
        if mode != "upstream_public":
            raise ValueError(
                "only upstream_public is implemented; reconstruction needs explicit design"
            )
        if config.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not config.x_values or not config.y_values or not config.z_values:
            raise ValueError("goal value grids must not be empty")
        self.stage = stage
        self.mode = mode
        self.rng = rng if rng is not None else random.Random()
        self.config = config

    def _candidate(self) -> Goal:
        return (
            self.rng.choice(self.config.x_values),
            self.rng.choice(self.config.y_values),
            self.rng.choice(self.config.z_values),
        )

    def is_valid(self, goal: Sequence[float]) -> bool:
        if len(goal) != 3:
            raise ValueError("goal must contain x, y, z")
        x, y, z = (float(value) for value in goal)

        # Preserve the public code's start-area exclusion.
        if (
            abs(x) <= self.config.origin_x_clearance
            and abs(y) <= self.config.origin_y_clearance
        ):
            return False

        if self.stage == 2 and self.config.obstacle_z_min <= z <= self.config.obstacle_z_max:
            clearance = self.config.obstacle_radius + self.config.obstacle_margin
            for center_x, center_y in self.config.obstacle_centers:
                if math.hypot(x - center_x, y - center_y) < clearance:
                    return False
        return True

    def sample(self) -> Goal:
        for _ in range(self.config.max_attempts):
            candidate = self._candidate()
            if self.is_valid(candidate):
                return tuple(float(value) for value in candidate)  # type: ignore[return-value]
        raise RuntimeError(
            "unable to sample a valid goal after "
            f"{self.config.max_attempts} attempts"
        )

