#!/usr/bin/env python3
"""Parameterized ``cmd_vel`` to trajectory bridge for paper launches.

This is a new bridge for the paper wrappers.  The legacy ``velocity0.py`` is
left untouched because it hard-codes the original namespace.  The message and
frame semantics match that bridge: body-forward velocity is rotated by the
current yaw plus the commanded yaw increment, while vertical velocity and yaw
rate are passed through.
"""

from __future__ import annotations

import math
import re
import threading

import rospy
from geometry_msgs.msg import Transform, Twist
from nav_msgs.msg import Odometry
from trajectory_msgs.msg import MultiDOFJointTrajectory, MultiDOFJointTrajectoryPoint


_SAFE_NAMESPACE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """Return yaw using the same ENU quaternion convention as ``velocity0``."""

    sin_yaw = 2.0 * (w * z + x * y)
    cos_yaw = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(sin_yaw, cos_yaw)


class VelocityBridge:
    def __init__(self) -> None:
        namespace = str(rospy.get_param("~namespace", "hydrone_aerial_underwater0")).strip("/")
        if not _SAFE_NAMESPACE.fullmatch(namespace):
            raise ValueError("namespace must be a simple ROS name without slashes")
        self.namespace = "/" + namespace
        self.cmd_topic = "%s/cmd_vel" % self.namespace
        self.odom_topic = "%s/ground_truth/odometry" % self.namespace
        self.trajectory_topic = "%s/command/trajectory" % self.namespace
        self._lock = threading.Lock()
        self._latest_twist = Twist()
        self._latest_transform = Transform()
        self._latest_transform.rotation.w = 1.0
        self._have_odom = False
        self.publisher = rospy.Publisher(
            self.trajectory_topic, MultiDOFJointTrajectory, queue_size=10
        )
        rospy.Subscriber(self.cmd_topic, Twist, self._on_twist, queue_size=10)
        rospy.Subscriber(self.odom_topic, Odometry, self._on_odometry, queue_size=10)

    def _on_twist(self, message: Twist) -> None:
        with self._lock:
            self._latest_twist = message

    def _on_odometry(self, message: Odometry) -> None:
        orientation = message.pose.pose.orientation
        position = message.pose.pose.position
        yaw = quaternion_to_yaw(
            orientation.x, orientation.y, orientation.z, orientation.w
        )
        transform = Transform()
        transform.translation.x = position.x
        transform.translation.y = position.y
        transform.translation.z = position.z
        transform.rotation = orientation
        with self._lock:
            self._latest_transform = transform
            self._latest_yaw = yaw
            self._have_odom = True

    def publish_trajectory(self, event=None) -> None:
        del event
        with self._lock:
            if not self._have_odom:
                return
            twist = self._latest_twist
            transform = self._latest_transform
            yaw = getattr(self, "_latest_yaw", 0.0)
        command = Twist()
        command.linear.x = twist.linear.x * math.cos(yaw + twist.angular.z)
        command.linear.y = twist.linear.x * math.sin(yaw + twist.angular.z)
        command.linear.z = twist.linear.z
        command.angular.x = twist.angular.x
        command.angular.y = twist.angular.y
        command.angular.z = twist.angular.z
        point = MultiDOFJointTrajectoryPoint()
        point.transforms.append(transform)
        point.velocities.append(command)
        trajectory = MultiDOFJointTrajectory()
        trajectory.header.stamp = rospy.Time.now()
        trajectory.joint_names.append("base_link")
        trajectory.points.append(point)
        self.publisher.publish(trajectory)


def main() -> int:
    rospy.init_node("icra2021_velocity_bridge", anonymous=False)
    try:
        bridge = VelocityBridge()
    except ValueError as exc:
        rospy.logfatal("ICRA2021 velocity bridge configuration invalid: %s", exc)
        return 2
    rospy.Timer(rospy.Duration(0.02), bridge.publish_trajectory)
    rospy.spin()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

