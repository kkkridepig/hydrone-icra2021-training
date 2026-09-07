#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Hydrone air-water transition demonstration.

Default route:
  Start hover: (0, 10, 1)
  Outbound:    (0, 10, 1) -> (10, 10, -1)
  Underwater hold
  Return:      (10, 10, -1) -> (0, 10, 1)
  Final hover

The commanded X-axis cruise speed is 0.5 m/s. A short acceleration and
deceleration ramp is used to reduce sudden attitude changes. Z is interpolated
according to X progress, so the outbound and return paths are identical.
"""

import math
import sys
from typing import Optional

import rospy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool
from std_srvs.srv import Empty


COMMAND_TOPIC = "/hydrone_aerial_underwater/command/pose"
ODOM_TOPIC = "/hydrone_aerial_underwater/odometry_sensor1/odometry"
SUBMERGED_TOPIC = "/hydrone_aerial_underwater/is_submerged"


class HydroneTransitionDemo:
    def __init__(self) -> None:
        self.frame_id = rospy.get_param("~frame_id", "world")

        self.start_x = float(rospy.get_param("~start_x", 0.0))
        self.start_y = float(rospy.get_param("~start_y", 10.0))
        self.start_z = float(rospy.get_param("~start_z", 1.0))

        self.end_x = float(rospy.get_param("~end_x", 10.0))
        self.end_y = float(rospy.get_param("~end_y", 10.0))
        self.end_z = float(rospy.get_param("~end_z", -1.0))

        self.cruise_speed = float(rospy.get_param("~cruise_speed", 0.5))
        self.acceleration = float(rospy.get_param("~acceleration", 0.20))
        self.publish_rate = float(rospy.get_param("~publish_rate", 20.0))

        self.start_hold = float(rospy.get_param("~start_hold", 15.0))
        self.underwater_hold = float(rospy.get_param("~underwater_hold", 25.0))
        self.final_hold = float(rospy.get_param("~final_hold", 15.0))
        self.auto_unpause = bool(rospy.get_param("~auto_unpause", True))

        if self.cruise_speed <= 0.0:
            raise ValueError("~cruise_speed must be greater than zero")
        if self.acceleration <= 0.0:
            raise ValueError("~acceleration must be greater than zero")
        if self.publish_rate <= 0.0:
            raise ValueError("~publish_rate must be greater than zero")
        if abs(self.end_x - self.start_x) < 1e-6:
            raise ValueError("start_x and end_x must be different")

        self.actual_odom: Optional[Odometry] = None
        self.is_submerged: Optional[bool] = None

        self.pose_pub = rospy.Publisher(COMMAND_TOPIC, PoseStamped, queue_size=10)
        rospy.Subscriber(ODOM_TOPIC, Odometry, self._odom_callback, queue_size=1)
        rospy.Subscriber(SUBMERGED_TOPIC, Bool, self._submerged_callback, queue_size=1)

    def _odom_callback(self, msg: Odometry) -> None:
        self.actual_odom = msg

    def _submerged_callback(self, msg: Bool) -> None:
        self.is_submerged = bool(msg.data)

    def _publish_pose(self, x: float, y: float, z: float) -> None:
        msg = PoseStamped()
        msg.header.stamp = rospy.Time.now()
        msg.header.frame_id = self.frame_id
        msg.pose.position.x = x
        msg.pose.position.y = y
        msg.pose.position.z = z
        msg.pose.orientation.w = 1.0
        self.pose_pub.publish(msg)

        if self.actual_odom is not None:
            p = self.actual_odom.pose.pose.position
            submerged_text = "unknown" if self.is_submerged is None else str(self.is_submerged)
            rospy.loginfo_throttle(
                1.0,
                "cmd=(%.2f, %.2f, %.2f), actual=(%.2f, %.2f, %.2f), submerged=%s",
                x, y, z, p.x, p.y, p.z, submerged_text,
            )

    def _hold(self, x: float, y: float, z: float, seconds: float, label: str) -> None:
        rospy.loginfo("%s: hold %.1f seconds at (%.2f, %.2f, %.2f)",
                      label, seconds, x, y, z)
        rate = rospy.Rate(self.publish_rate)
        start_time = rospy.Time.now()

        while not rospy.is_shutdown():
            elapsed = (rospy.Time.now() - start_time).to_sec()
            if elapsed >= seconds:
                break
            self._publish_pose(x, y, z)
            rate.sleep()

    def _profile_times(self, distance: float):
        vmax = self.cruise_speed
        accel = self.acceleration
        t_accel = vmax / accel
        d_accel = 0.5 * accel * t_accel * t_accel

        if 2.0 * d_accel >= distance:
            t_accel = math.sqrt(distance / accel)
            peak_speed = accel * t_accel
            return t_accel, 0.0, 2.0 * t_accel, peak_speed, True

        d_cruise = distance - 2.0 * d_accel
        t_cruise = d_cruise / vmax
        total_time = 2.0 * t_accel + t_cruise
        return t_accel, t_cruise, total_time, vmax, False

    def _distance_at_time(self, t, distance, t_accel, t_cruise, peak_speed, triangular):
        accel = self.acceleration

        if t <= t_accel:
            return 0.5 * accel * t * t

        if triangular:
            t_decel = t - t_accel
            d_half = 0.5 * distance
            return d_half + peak_speed * t_decel - 0.5 * accel * t_decel * t_decel

        d_accel = 0.5 * accel * t_accel * t_accel
        if t <= t_accel + t_cruise:
            return d_accel + peak_speed * (t - t_accel)

        t_decel = t - t_accel - t_cruise
        d_before_decel = d_accel + peak_speed * t_cruise
        return d_before_decel + peak_speed * t_decel - 0.5 * accel * t_decel * t_decel

    def _move(self, x0, y0, z0, x1, y1, z1, label: str) -> None:
        dx = x1 - x0
        distance_x = abs(dx)
        direction = 1.0 if dx >= 0.0 else -1.0

        t_accel, t_cruise, total_time, peak_speed, triangular = self._profile_times(distance_x)

        rospy.loginfo(
            "%s: X distance=%.2f m, cruise/peak speed=%.2f m/s, duration=%.2f s",
            label, distance_x, peak_speed, total_time,
        )

        rate = rospy.Rate(self.publish_rate)
        start_time = rospy.Time.now()

        while not rospy.is_shutdown():
            t = (rospy.Time.now() - start_time).to_sec()
            if t >= total_time:
                break

            travelled = self._distance_at_time(
                t, distance_x, t_accel, t_cruise, peak_speed, triangular
            )
            travelled = max(0.0, min(distance_x, travelled))
            progress = travelled / distance_x

            x = x0 + direction * travelled
            y = y0 + (y1 - y0) * progress
            z = z0 + (z1 - z0) * progress
            self._publish_pose(x, y, z)
            rate.sleep()

        endpoint_rate = rospy.Rate(self.publish_rate)
        for _ in range(max(1, int(self.publish_rate))):
            if rospy.is_shutdown():
                return
            self._publish_pose(x1, y1, z1)
            endpoint_rate.sleep()

    def _wait_for_controller(self) -> None:
        rospy.loginfo("Waiting for a subscriber on %s ...", COMMAND_TOPIC)
        while not rospy.is_shutdown() and self.pose_pub.get_num_connections() == 0:
            rospy.sleep(0.2)
        if not rospy.is_shutdown():
            rospy.loginfo("Controller subscriber detected")

    def _unpause(self) -> None:
        if not self.auto_unpause:
            return
        try:
            rospy.wait_for_service("/gazebo/unpause_physics", timeout=5.0)
            rospy.ServiceProxy("/gazebo/unpause_physics", Empty)()
            rospy.loginfo("Gazebo physics is unpaused")
        except (rospy.ROSException, rospy.ServiceException) as exc:
            rospy.logwarn("Could not unpause Gazebo automatically: %s", exc)

    def run(self) -> None:
        self._wait_for_controller()
        self._unpause()

        self._hold(self.start_x, self.start_y, self.start_z,
                   self.start_hold, "Stage 1 - start hover")

        self._move(self.start_x, self.start_y, self.start_z,
                   self.end_x, self.end_y, self.end_z,
                   "Stage 2 - outbound air-to-water transition")

        self._hold(self.end_x, self.end_y, self.end_z,
                   self.underwater_hold, "Stage 3 - underwater observation")

        self._move(self.end_x, self.end_y, self.end_z,
                   self.start_x, self.start_y, self.start_z,
                   "Stage 4 - return water-to-air transition")

        self._hold(self.start_x, self.start_y, self.start_z,
                   self.final_hold, "Stage 5 - final hover")

        rospy.loginfo("Hydrone transition demonstration completed")


def main() -> int:
    rospy.init_node("hydrone_transition_demo", anonymous=False)
    try:
        HydroneTransitionDemo().run()
        return 0
    except (ValueError, rospy.ROSInterruptException) as exc:
        rospy.logerr("Transition demonstration stopped: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
