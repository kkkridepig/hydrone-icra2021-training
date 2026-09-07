#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Hydrone aerial-underwater transition demo.

Recommended run order:
  1) Start Gazebo with paused:=true.
  2) Spawn Hydrone with spawn_hydrone_only.launch.
  3) Start hydrone_position_tracking_monitor.py.
  4) Start this demo.

Important fix:
  Before Gazebo is unpaused, this script continuously publishes the initial
  hover command for a short wall-clock period. Therefore the Lee controller
  already has the correct target when physics starts, preventing free fall.

Default path:
    (0, 10, 1)
        ->
    (10, 10, -1)
        ->
    (0, 10, 1)

The x reference uses a trapezoidal/triangular speed profile. z is interpolated
from the x progress, so the return path is geometrically the reverse of the
outbound path.
"""

import math
import time

import rospy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool
from std_srvs.srv import Empty


COMMAND_TOPIC = "/hydrone_aerial_underwater0/command/pose"
ODOM_TOPIC = "/hydrone_aerial_underwater0/odometry_sensor1/odometry"
SUBMERGED_TOPIC = "/hydrone_aerial_underwater0/is_submerged"


class HydroneTransitionDemo:
    def __init__(self):
        # ---------------------------
        # Parameters
        # ---------------------------
        self.start_x = float(rospy.get_param("~start_x", 0.0))
        self.start_y = float(rospy.get_param("~start_y", 10.0))
        self.start_z = float(rospy.get_param("~start_z", 1.0))

        self.end_x = float(rospy.get_param("~end_x", 10.0))
        self.end_y = float(rospy.get_param("~end_y", self.start_y))
        self.end_z = float(rospy.get_param("~end_z", -1.0))

        self.cruise_speed = abs(float(rospy.get_param("~cruise_speed", 0.5)))
        self.acceleration = abs(float(rospy.get_param("~acceleration", 0.20)))
        self.publish_rate = float(rospy.get_param("~publish_rate", 20.0))

        self.start_hold = float(rospy.get_param("~start_hold", 15.0))
        self.underwater_hold = float(rospy.get_param("~underwater_hold", 25.0))
        self.final_hold = float(rospy.get_param("~final_hold", 15.0))

        self.auto_unpause = bool(rospy.get_param("~auto_unpause", True))

        # Wall-clock preload while Gazebo is paused.
        self.preload_wall_time = float(rospy.get_param("~preload_wall_time", 1.0))
        self.preload_rate = float(rospy.get_param("~preload_rate", 20.0))

        # ---------------------------
        # Runtime state
        # ---------------------------
        self.have_odom = False
        self.actual_x = 0.0
        self.actual_y = 0.0
        self.actual_z = 0.0

        self.have_submerged = False
        self.is_submerged = False

        self.last_log_wall = 0.0

        # ---------------------------
        # ROS communication
        # ---------------------------
        self.pose_pub = rospy.Publisher(
            COMMAND_TOPIC,
            PoseStamped,
            queue_size=10
        )

        self.odom_sub = rospy.Subscriber(
            ODOM_TOPIC,
            Odometry,
            self.odom_callback,
            queue_size=50
        )

        self.submerged_sub = rospy.Subscriber(
            SUBMERGED_TOPIC,
            Bool,
            self.submerged_callback,
            queue_size=10
        )

    def odom_callback(self, msg):
        p = msg.pose.pose.position
        self.actual_x = p.x
        self.actual_y = p.y
        self.actual_z = p.z
        self.have_odom = True

    def submerged_callback(self, msg):
        self.is_submerged = bool(msg.data)
        self.have_submerged = True

    @staticmethod
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    def make_pose(self, x, y, z):
        msg = PoseStamped()
        msg.header.stamp = rospy.Time.now()
        msg.header.frame_id = "world"

        msg.pose.position.x = x
        msg.pose.position.y = y
        msg.pose.position.z = z

        # Keep desired yaw = 0.
        msg.pose.orientation.x = 0.0
        msg.pose.orientation.y = 0.0
        msg.pose.orientation.z = 0.0
        msg.pose.orientation.w = 1.0
        return msg

    def publish_pose(self, x, y, z, stage=""):
        msg = self.make_pose(x, y, z)
        self.pose_pub.publish(msg)

        now_wall = time.monotonic()
        if now_wall - self.last_log_wall >= 1.0:
            self.last_log_wall = now_wall

            medium = "WATER" if (self.have_submerged and self.is_submerged) else "AIR"

            if self.have_odom:
                rospy.loginfo(
                    "[%s][%s] cmd=(%.3f, %.3f, %.3f), "
                    "actual=(%.3f, %.3f, %.3f)",
                    stage,
                    medium,
                    x, y, z,
                    self.actual_x, self.actual_y, self.actual_z
                )
            else:
                rospy.loginfo(
                    "[%s][%s] cmd=(%.3f, %.3f, %.3f), waiting for odometry...",
                    stage,
                    medium,
                    x, y, z
                )

    def wait_for_controller_subscriber(self):
        rospy.loginfo("Waiting for Lee controller subscriber on %s ...", COMMAND_TOPIC)

        while not rospy.is_shutdown():
            if self.pose_pub.get_num_connections() > 0:
                rospy.loginfo(
                    "Controller subscriber connected (%d connection(s)).",
                    self.pose_pub.get_num_connections()
                )
                return
            time.sleep(0.1)

    def preload_initial_target(self):
        """
        Gazebo may be paused here. Use wall-clock sleep rather than rospy.sleep,
        because /use_sim_time=true causes ROS time to stop while Gazebo is paused.
        """
        rospy.loginfo("")
        rospy.loginfo("Preloading initial target BEFORE physics is unpaused.")
        rospy.loginfo(
            "Initial target = (%.3f, %.3f, %.3f)",
            self.start_x, self.start_y, self.start_z
        )

        if self.preload_rate <= 0:
            self.preload_rate = 20.0

        period = 1.0 / self.preload_rate
        end_wall = time.monotonic() + max(self.preload_wall_time, period)

        count = 0
        while not rospy.is_shutdown() and time.monotonic() < end_wall:
            self.publish_pose(
                self.start_x,
                self.start_y,
                self.start_z,
                stage="PRELOAD"
            )
            count += 1
            time.sleep(period)

        rospy.loginfo("Initial target preloaded %d times.", count)

    def unpause_gazebo(self):
        if not self.auto_unpause:
            rospy.loginfo("auto_unpause=false; leaving Gazebo physics unchanged.")
            return

        rospy.loginfo("Waiting for /gazebo/unpause_physics ...")
        rospy.wait_for_service("/gazebo/unpause_physics")

        unpause = rospy.ServiceProxy("/gazebo/unpause_physics", Empty)
        unpause()

        rospy.loginfo("Gazebo physics unpaused.")

        # Wall-clock pause only to let callbacks begin flowing immediately.
        time.sleep(0.2)

    def hold_pose(self, x, y, z, duration, stage):
        if duration <= 0:
            return

        rospy.loginfo(
            "%s: holding (%.3f, %.3f, %.3f) for %.2f s simulation time.",
            stage, x, y, z, duration
        )

        start = rospy.Time.now().to_sec()
        rate = rospy.Rate(self.publish_rate)

        while not rospy.is_shutdown():
            elapsed = rospy.Time.now().to_sec() - start
            if elapsed >= duration:
                break

            self.publish_pose(x, y, z, stage=stage)
            rate.sleep()

    def motion_profile(self, distance):
        """
        Return trapezoidal/triangular x-axis profile parameters.

        distance: positive travel distance in x.
        """
        if distance <= 1e-9:
            return {
                "triangular": True,
                "t_acc": 0.0,
                "t_cruise": 0.0,
                "t_total": 0.0,
                "v_peak": 0.0,
                "d_acc": 0.0
            }

        if self.cruise_speed <= 0 or self.acceleration <= 0:
            raise ValueError("~cruise_speed and ~acceleration must be > 0.")

        t_acc_nominal = self.cruise_speed / self.acceleration
        d_acc_nominal = 0.5 * self.acceleration * t_acc_nominal ** 2

        if 2.0 * d_acc_nominal >= distance:
            # Triangular profile.
            t_acc = math.sqrt(distance / self.acceleration)
            v_peak = self.acceleration * t_acc
            return {
                "triangular": True,
                "t_acc": t_acc,
                "t_cruise": 0.0,
                "t_total": 2.0 * t_acc,
                "v_peak": v_peak,
                "d_acc": 0.5 * self.acceleration * t_acc ** 2
            }

        # Trapezoidal profile.
        d_cruise = distance - 2.0 * d_acc_nominal
        t_cruise = d_cruise / self.cruise_speed

        return {
            "triangular": False,
            "t_acc": t_acc_nominal,
            "t_cruise": t_cruise,
            "t_total": 2.0 * t_acc_nominal + t_cruise,
            "v_peak": self.cruise_speed,
            "d_acc": d_acc_nominal
        }

    def profile_distance_at_time(self, t, distance, profile):
        if distance <= 1e-9:
            return 0.0

        t_acc = profile["t_acc"]
        t_cruise = profile["t_cruise"]
        t_total = profile["t_total"]
        v_peak = profile["v_peak"]
        d_acc = profile["d_acc"]

        t = self.clamp(t, 0.0, t_total)

        if t <= t_acc:
            return 0.5 * self.acceleration * t ** 2

        if t <= t_acc + t_cruise:
            return d_acc + v_peak * (t - t_acc)

        # Deceleration.
        tau = t - (t_acc + t_cruise)
        d_before_decel = d_acc + v_peak * t_cruise
        return (
            d_before_decel
            + v_peak * tau
            - 0.5 * self.acceleration * tau ** 2
        )

    def move_between(self, p0, p1, stage):
        """
        Move along x with a speed-limited reference profile.
        y and z are interpolated according to x progress.
        """
        x0, y0, z0 = p0
        x1, y1, z1 = p1

        dx = x1 - x0
        x_distance = abs(dx)

        if x_distance <= 1e-9:
            self.publish_pose(x1, y1, z1, stage=stage)
            return

        direction = 1.0 if dx >= 0.0 else -1.0
        profile = self.motion_profile(x_distance)

        rospy.loginfo("")
        rospy.loginfo(
            "%s: x %.3f -> %.3f m, reference cruise speed %.3f m/s, "
            "profile duration %.3f s.",
            stage, x0, x1, self.cruise_speed, profile["t_total"]
        )

        start_sim = rospy.Time.now().to_sec()
        rate = rospy.Rate(self.publish_rate)

        while not rospy.is_shutdown():
            elapsed = rospy.Time.now().to_sec() - start_sim

            if elapsed >= profile["t_total"]:
                break

            travelled = self.profile_distance_at_time(
                elapsed,
                x_distance,
                profile
            )

            progress = self.clamp(travelled / x_distance, 0.0, 1.0)

            x = x0 + direction * travelled
            y = y0 + progress * (y1 - y0)
            z = z0 + progress * (z1 - z0)

            self.publish_pose(x, y, z, stage=stage)
            rate.sleep()

        # Publish exact endpoint briefly.
        endpoint_rate = rospy.Rate(self.publish_rate)
        for _ in range(max(1, int(self.publish_rate * 0.5))):
            if rospy.is_shutdown():
                return
            self.publish_pose(x1, y1, z1, stage=stage + "_END")
            endpoint_rate.sleep()

    def run(self):
        self.wait_for_controller_subscriber()

        # Critical ordering fix.
        self.preload_initial_target()
        self.unpause_gazebo()

        start = (self.start_x, self.start_y, self.start_z)
        end = (self.end_x, self.end_y, self.end_z)

        # Stage 1: start hover.
        self.hold_pose(*start, self.start_hold, "START_HOLD")

        # Stage 2: air -> water.
        self.move_between(start, end, "OUTBOUND")

        # Stage 3: underwater hold.
        self.hold_pose(*end, self.underwater_hold, "UNDERWATER_HOLD")

        # Stage 4: reverse path.
        self.move_between(end, start, "RETURN")

        # Stage 5: final hover.
        self.hold_pose(*start, self.final_hold, "FINAL_HOLD")

        # A short final burst helps downstream monitors receive the exact endpoint.
        final_rate = rospy.Rate(self.publish_rate)
        for _ in range(max(1, int(self.publish_rate * 0.5))):
            if rospy.is_shutdown():
                break
            self.publish_pose(*start, stage="DONE")
            final_rate.sleep()

        rospy.loginfo("")
        rospy.loginfo("Hydrone transition demo finished.")
        rospy.loginfo(
            "Final commanded position = (%.3f, %.3f, %.3f)",
            self.start_x, self.start_y, self.start_z
        )


def main():
    rospy.init_node("hydrone_transition_demo", anonymous=False)

    try:
        node = HydroneTransitionDemo()
        node.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as exc:
        rospy.logerr("Transition demo failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
