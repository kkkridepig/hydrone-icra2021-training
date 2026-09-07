"""ROS GoalManager with bounded service waits and explicit spawn/delete calls."""

from pathlib import Path
import random
from typing import Optional, Sequence, Tuple

import rospkg
import rospy
from gazebo_msgs.srv import DeleteModel, SpawnModel
from geometry_msgs.msg import Pose

from .goal_sampling import Goal, GoalSamplingConfig, PublicGoalSampler


class GoalManager:
    """Manage one named goal without model-state busy-waits."""

    def __init__(
        self,
        stage: int = 1,
        mode: str = "upstream_public",
        model_path: Optional[str] = None,
        model_name: str = "goal",
        service_timeout: float = 10.0,
        seed: Optional[int] = None,
        sampling_config: GoalSamplingConfig = GoalSamplingConfig(),
    ) -> None:
        if service_timeout <= 0.0:
            raise ValueError("service_timeout must be positive")
        self.stage = stage
        self.model_name = model_name
        self.service_timeout = float(service_timeout)
        self.model_path = self._resolve_model_path(model_path)
        self.model_xml = self.model_path.read_text()
        self.rng = random.Random(seed)
        self.sampler = PublicGoalSampler(
            stage=stage, mode=mode, rng=self.rng, config=sampling_config
        )
        self.current_goal: Optional[Goal] = None
        self._spawned = False

    @staticmethod
    def _resolve_model_path(model_path: Optional[str]) -> Path:
        if model_path:
            path = Path(model_path).expanduser().resolve()
        else:
            package_path = Path(
                rospkg.RosPack().get_path("hydrone_aerial_underwater_deep_rl")
            )
            path = package_path / "models" / "goal_box" / "model0.sdf"
        if not path.is_file():
            raise FileNotFoundError(f"goal SDF does not exist: {path}")
        return path

    def _wait_for_service(self, service_name: str) -> None:
        if rospy.is_shutdown():
            raise RuntimeError("ROS is shutting down")
        try:
            rospy.wait_for_service(service_name, timeout=self.service_timeout)
        except rospy.ROSException as exc:
            raise TimeoutError(
                f"timed out after {self.service_timeout:.1f}s waiting for {service_name}"
            ) from exc

    @staticmethod
    def _pose(goal: Goal) -> Pose:
        pose = Pose()
        pose.position.x, pose.position.y, pose.position.z = goal
        pose.orientation.w = 1.0
        return pose

    @staticmethod
    def _check_response(response, operation: str) -> None:
        if hasattr(response, "success") and not response.success:
            message = getattr(response, "status_message", "")
            raise RuntimeError(f"Gazebo {operation} failed: {message}")

    def sample_goal(self) -> Goal:
        return self.sampler.sample()

    def spawn(self, goal: Optional[Sequence[float]] = None) -> Goal:
        selected = self.sample_goal() if goal is None else tuple(float(v) for v in goal)
        if not self.sampler.is_valid(selected):
            raise ValueError(f"goal is outside the configured sampling contract: {selected}")
        service_name = "/gazebo/spawn_sdf_model"
        self._wait_for_service(service_name)
        proxy = rospy.ServiceProxy(service_name, SpawnModel)
        response = proxy(
            self.model_name,
            self.model_xml,
            "",
            self._pose(selected),
            "world",
        )
        self._check_response(response, "spawn")
        self.current_goal = selected  # type: ignore[assignment]
        self._spawned = True
        return selected  # type: ignore[return-value]

    def delete(self) -> bool:
        if not self._spawned:
            return False
        service_name = "/gazebo/delete_model"
        self._wait_for_service(service_name)
        proxy = rospy.ServiceProxy(service_name, DeleteModel)
        response = proxy(self.model_name)
        if (
            hasattr(response, "success")
            and not response.success
            and "does not exist" in getattr(response, "status_message", "").lower()
        ):
            # ``reset_world`` can remove a separately spawned goal in some
            # Gazebo/plugin combinations. Treat that response as an idempotent
            # delete so the next reset can safely spawn a fresh goal.
            self._spawned = False
            self.current_goal = None
            return True
        self._check_response(response, "delete")
        self._spawned = False
        self.current_goal = None
        return True

    def replace(self) -> Goal:
        if self._spawned:
            self.delete()
        return self.spawn()
