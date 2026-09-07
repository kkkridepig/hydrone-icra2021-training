"""Independent single-agent Paper Environment for the ICRA 2021 contract.

The ROS adapter in this module deliberately does not import ROS at module load
time.  This keeps the observation, reward, termination, and truncation logic
unit-testable with the project Python virtual environment while preserving the
existing ``environment_3D.py`` as an untouched upstream reference.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING, Tuple

import numpy as np

from .contracts import DEFAULT_ACTION_CONTRACT, DEFAULT_OBSERVATION_CONTRACT
from .goal_sampling import Goal
from .observation import build_observation, sanitize_scan

if TYPE_CHECKING:
    from .contracts import ActionContract, ObservationContract
    from .goal_manager import GoalManager


@dataclass(frozen=True)
class EnvironmentConfig:
    """ROS names and episode semantics for the independent paper environment."""

    namespace: str = "/hydrone_aerial_underwater0"
    scan_topic: Optional[str] = None
    ground_truth_odom_topic: Optional[str] = None
    cmd_vel_topic: Optional[str] = None
    reset_service: str = "/gazebo/reset_world"
    stage: int = 1
    start: Tuple[float, float, float] = (0.0, 0.0, 2.5)
    start_yaw: float = 0.0
    goal_tolerance: float = 0.25
    collision_threshold: float = 0.5
    z_min: float = -1.5
    z_max: float = 4.8
    max_steps: int = 500
    sensor_timeout: float = 5.0
    service_timeout: float = 10.0
    goal_mode: str = "upstream_public"
    goal_seed: Optional[int] = None

    def __post_init__(self) -> None:
        namespace = "/" + self.namespace.strip("/")
        object.__setattr__(self, "namespace", namespace)
        defaults = {
            "scan_topic": f"{namespace}/scan",
            "ground_truth_odom_topic": f"{namespace}/ground_truth/odometry",
            "cmd_vel_topic": f"{namespace}/cmd_vel",
        }
        for field_name, default in defaults.items():
            value = getattr(self, field_name)
            object.__setattr__(self, field_name, value or default)
        if len(self.start) != 3:
            raise ValueError("start must contain x, y, z")
        if self.stage not in (1, 2):
            raise ValueError("stage must be 1 or 2")
        if self.goal_tolerance <= 0.0:
            raise ValueError("goal_tolerance must be positive")
        if self.collision_threshold <= 0.0:
            raise ValueError("collision_threshold must be positive")
        if self.z_min >= self.z_max:
            raise ValueError("z_min must be smaller than z_max")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.sensor_timeout <= 0.0 or self.service_timeout <= 0.0:
            raise ValueError("sensor and service timeouts must be positive")


@dataclass(frozen=True)
class PoseState:
    """Ground-truth position and yaw used by the fixed observation contract."""

    x: float
    y: float
    z: float
    yaw: float = 0.0


@dataclass(frozen=True)
class TransitionDecision:
    """Pure transition result before a ROS goal replacement is performed."""

    observation: np.ndarray
    reward: float
    terminated: bool
    truncated: bool
    goal_reached: bool
    collision: bool
    goal_distance: float


def wrap_angle(angle: float) -> float:
    """Wrap an angle to ``[-pi, pi]`` without changing its convention."""

    wrapped = (float(angle) + math.pi) % (2.0 * math.pi) - math.pi
    # Preserve +pi for the exact positive boundary when possible.
    if wrapped == -math.pi and angle > 0.0:
        return math.pi
    return wrapped


def goal_metrics(pose: PoseState, goal: Goal) -> Tuple[float, float, float]:
    """Return horizontal heading, vertical heading, and 3-D distance."""

    goal_x, goal_y, goal_z = (float(value) for value in goal)
    delta_x = goal_x - pose.x
    delta_y = goal_y - pose.y
    delta_z = goal_z - pose.z
    horizontal_distance = math.hypot(delta_x, delta_y)
    heading = wrap_angle(math.atan2(delta_y, delta_x) - pose.yaw)
    heading_z = math.atan2(delta_z, horizontal_distance)
    distance = math.sqrt(delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)
    return heading, heading_z, distance


def evaluate_transition(
    scan: Sequence[float],
    pose: PoseState,
    goal: Goal,
    action: Sequence[float],
    step_index: int,
    config: EnvironmentConfig = EnvironmentConfig(),
    observation_contract: "ObservationContract" = DEFAULT_OBSERVATION_CONTRACT,
    action_contract: "ActionContract" = DEFAULT_ACTION_CONTRACT,
) -> TransitionDecision:
    """Compute the next observation and event flags without ROS side effects.

    ``action`` is the physical command that was just executed, so it is the
    previous-action portion of the returned observation.  Collision has
    priority over a simultaneous goal crossing.  A goal event never sets
    ``terminated``; only collision does.  The time limit is represented by
    ``truncated``.
    """

    if step_index < 0:
        raise ValueError("step_index must be non-negative")
    physical_action = action_contract.validate_physical(action)
    scan_values = sanitize_scan(scan, contract=observation_contract)
    heading, heading_z, goal_distance = goal_metrics(pose, goal)
    collision = bool(
        float(np.min(scan_values)) < config.collision_threshold
        or pose.z < config.z_min
        or pose.z > config.z_max
    )
    goal_reached = goal_distance < config.goal_tolerance
    terminated = collision
    truncated = (not terminated) and (step_index + 1 >= config.max_steps)
    reward = -10.0 if collision else 100.0 if goal_reached else 0.0
    observation = build_observation(
        scan_values,
        physical_action,
        heading=heading,
        heading_z=heading_z,
        goal_distance=goal_distance,
        contract=observation_contract,
    )
    return TransitionDecision(
        observation=observation,
        reward=reward,
        terminated=terminated,
        truncated=truncated,
        goal_reached=goal_reached,
        collision=collision,
        goal_distance=goal_distance,
    )


class PaperEnvironment:
    """ROS-backed environment using the existing Hydrone topic contract.

    The class follows the legacy Gym-style ``reset() -> state`` convention and
    returns Gymnasium's explicit five-tuple from ``step``:
    ``(state, reward, terminated, truncated, info)``.
    """

    def __init__(
        self,
        config: EnvironmentConfig = EnvironmentConfig(),
        goal_manager: Optional["GoalManager"] = None,
        observation_contract=DEFAULT_OBSERVATION_CONTRACT,
        action_contract=DEFAULT_ACTION_CONTRACT,
    ) -> None:
        try:
            import rospy
            from geometry_msgs.msg import Twist
            from nav_msgs.msg import Odometry
            from sensor_msgs.msg import LaserScan
            from std_srvs.srv import Empty
        except ImportError as exc:  # pragma: no cover - depends on ROS runtime
            raise RuntimeError(
                "PaperEnvironment requires sourced ROS Noetic Python modules"
            ) from exc

        self.config = config
        self.observation_contract = observation_contract
        self.action_contract = action_contract
        self._rospy = rospy
        self._Twist = Twist
        self._Odometry = Odometry
        self._LaserScan = LaserScan
        self._Empty = Empty
        self.pub_cmd_vel = rospy.Publisher(
            config.cmd_vel_topic, Twist, queue_size=5
        )
        self._reset_proxy = rospy.ServiceProxy(config.reset_service, Empty)
        if goal_manager is None:
            from .goal_manager import GoalManager

            goal_manager = GoalManager(
                stage=config.stage,
                mode=config.goal_mode,
                service_timeout=config.service_timeout,
                seed=config.goal_seed,
            )
        self.goal_manager = goal_manager
        self._goal: Optional[Goal] = None
        self._step_count = 0
        self._previous_action = np.zeros(
            self.action_contract.dim, dtype=np.float32
        )
        self._closed = False
        rospy.on_shutdown(self.close)

    def _wait_for_service(self, service_name: str) -> None:
        if self._rospy.is_shutdown():
            raise RuntimeError("ROS is shutting down")
        try:
            self._rospy.wait_for_service(
                service_name, timeout=self.config.service_timeout
            )
        except self._rospy.ROSException as exc:
            raise TimeoutError(
                f"timed out after {self.config.service_timeout:.1f}s waiting for "
                f"{service_name}"
            ) from exc

    def _reset_world(self) -> None:
        self._wait_for_service(self.config.reset_service)
        try:
            self._reset_proxy()
        except self._rospy.ServiceException as exc:
            raise RuntimeError(
                f"failed to call {self.config.reset_service}"
            ) from exc

    def _wait_for_measurement(self) -> Tuple[Sequence[float], PoseState]:
        try:
            odometry = self._rospy.wait_for_message(
                self.config.ground_truth_odom_topic,
                self._Odometry,
                timeout=self.config.sensor_timeout,
            )
            scan = self._rospy.wait_for_message(
                self.config.scan_topic,
                self._LaserScan,
                timeout=self.config.sensor_timeout,
            )
        except self._rospy.ROSException as exc:
            if self._rospy.is_shutdown():
                raise RuntimeError("ROS shutdown while waiting for sensors") from exc
            raise TimeoutError(
                "timed out waiting for ground-truth odometry and LaserScan"
            ) from exc
        return scan.ranges, self._pose_from_odometry(odometry)

    @staticmethod
    def _pose_from_odometry(odometry: Any) -> PoseState:
        position = odometry.pose.pose.position
        orientation = odometry.pose.pose.orientation
        sin_yaw = 2.0 * (orientation.w * orientation.z + orientation.x * orientation.y)
        cos_yaw = 1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z)
        yaw = math.atan2(sin_yaw, cos_yaw)
        return PoseState(position.x, position.y, position.z, yaw)

    def _publish_action(self, action: np.ndarray) -> None:
        command = self._Twist()
        command.linear.x = float(action[0])
        command.linear.z = float(action[1])
        command.angular.z = float(action[2])
        self.pub_cmd_vel.publish(command)

    def _publish_zero(self) -> None:
        self.pub_cmd_vel.publish(self._Twist())

    def _spawn_or_replace_goal(self) -> Goal:
        if self._goal is None:
            return self.goal_manager.spawn()
        return self.goal_manager.replace()

    def _observation(
        self, scan: Sequence[float], pose: PoseState, goal: Goal, action: Sequence[float]
    ) -> np.ndarray:
        heading, heading_z, distance = goal_metrics(pose, goal)
        return build_observation(
            scan,
            action,
            heading=heading,
            heading_z=heading_z,
            goal_distance=distance,
            contract=self.observation_contract,
        )

    def reset(self) -> np.ndarray:
        """Reset the world and robot, then spawn exactly one fresh goal."""

        if self._closed:
            raise RuntimeError("environment is closed")
        self._reset_world()
        self._step_count = 0
        self._previous_action = np.zeros(
            self.action_contract.dim, dtype=np.float32
        )
        self._goal = self._spawn_or_replace_goal()
        scan, pose = self._wait_for_measurement()
        start_pose = PoseState(
            self.config.start[0], self.config.start[1], self.config.start[2], self.config.start_yaw
        )
        start_error = math.sqrt(
            (pose.x - start_pose.x) ** 2
            + (pose.y - start_pose.y) ** 2
            + (pose.z - start_pose.z) ** 2
        )
        if start_error > 0.5:
            self._rospy.logwarn(
                "reset pose differs from configured start by %.3f m", start_error
            )
        return self._observation(scan, pose, self._goal, self._previous_action)

    def step(self, action: Sequence[float]):
        """Execute one physical action and return explicit event flags."""

        if self._closed:
            raise RuntimeError("environment is closed")
        if self._goal is None:
            raise RuntimeError("call reset() before step()")
        physical_action = self.action_contract.validate_physical(action).copy()
        self._publish_action(physical_action)
        scan, pose = self._wait_for_measurement()
        decision = evaluate_transition(
            scan,
            pose,
            self._goal,
            physical_action,
            step_index=self._step_count,
            config=self.config,
            observation_contract=self.observation_contract,
            action_contract=self.action_contract,
        )
        self._step_count += 1
        old_goal = self._goal
        if decision.goal_reached and not decision.collision:
            self._goal = self._spawn_or_replace_goal()
            observation = self._observation(scan, pose, self._goal, physical_action)
        else:
            observation = decision.observation
        if decision.collision:
            self._publish_zero()
        self._previous_action = physical_action
        info: Dict[str, Any] = {
            "goal_reached": decision.goal_reached and not decision.collision,
            "collision": decision.collision,
            "goal_distance": decision.goal_distance,
            "step": self._step_count,
            "goal_before": old_goal,
            "goal": self._goal,
            "pose": pose,
        }
        return observation, decision.reward, decision.terminated, decision.truncated, info

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._publish_zero()
        except Exception:
            # Shutdown may already have torn down ROS publishers.
            pass
        if self._goal is not None:
            try:
                self.goal_manager.delete()
            except Exception:
                # Goal cleanup is best-effort during ROS shutdown, while the
                # GoalManager itself keeps bounded service waits.
                pass
            self._goal = None
