#!/usr/bin/env python3

import math
import numpy as np
import rospy

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan


def clamp(value, low, high):
    return max(low, min(high, value))


def wrap_angle(angle):
    return math.atan2(
        math.sin(angle),
        math.cos(angle)
    )


def quaternion_to_yaw(q):
    siny = 2.0 * (
        q.w * q.z
        + q.x * q.y
    )

    cosy = 1.0 - 2.0 * (
        q.y * q.y
        + q.z * q.z
    )

    return math.atan2(
        siny,
        cosy
    )


class ReactivePlanner:

    def __init__(self):

        # Same action limits used by the paper code.
        self.max_forward = 0.25
        self.max_vertical = 0.18
        self.max_yaw_command = 0.25

        # Conservative surface-transition limit.
        self.surface_vertical_limit = 0.15

        # Obstacle distances.
        self.emergency_distance = 0.65
        self.block_distance = 1.00
        self.slow_distance = 1.80

        self.arriving_distance = 0.25

        self.last_direction = 0.0

        # Hysteresis avoids left-right oscillation.
        self.avoid_direction = 0
        self.avoid_counter = 0


    def local_clearance(self, ranges):

        result = np.zeros_like(ranges)

        for i in range(len(ranges)):

            i0 = max(
                0,
                i - 1
            )

            i1 = min(
                len(ranges),
                i + 2
            )

            result[i] = np.min(
                ranges[i0:i1]
            )

        return result


    def compute_action(
        self,
        ranges,
        angle_min,
        angle_increment,
        range_max,
        x,
        y,
        z,
        yaw,
        goal_x,
        goal_y,
        goal_z
    ):

        ranges = np.asarray(
            ranges,
            dtype=float
        )

        if len(ranges) == 0:
            return (
                np.zeros(3),
                {
                    "mode": "NO_SCAN"
                }
            )

        # Replace invalid LaserScan values.
        ranges = np.nan_to_num(
            ranges,
            nan=range_max,
            posinf=range_max,
            neginf=0.0
        )

        ranges = np.clip(
            ranges,
            0.0,
            range_max
        )

        angles = (
            angle_min
            + np.arange(len(ranges))
            * angle_increment
        )

        clearance = self.local_clearance(
            ranges
        )

        dx = goal_x - x
        dy = goal_y - y
        dz = goal_z - z

        horizontal_distance = math.hypot(
            dx,
            dy
        )

        total_distance = math.sqrt(
            dx * dx
            + dy * dy
            + dz * dz
        )

        # Precise point-to-point arrival criterion.
        #
        # A pure 3-D spherical threshold can declare
        # success while the vehicle is still too far
        # from the requested depth. Therefore horizontal
        # and vertical errors are checked separately.
        horizontal_arrival_tolerance = 0.15
        vertical_arrival_tolerance = 0.08

        if (
            horizontal_distance
            <= horizontal_arrival_tolerance
            and abs(dz)
            <= vertical_arrival_tolerance
        ):

            return (
                np.zeros(3),
                {
                    "mode": "ARRIVED",
                    "distance": total_distance,
                    "horizontal_distance":
                        horizontal_distance,
                    "vertical_error": abs(dz)
                }
            )

        goal_world_angle = math.atan2(
            dy,
            dx
        )

        goal_body_angle = wrap_angle(
            goal_world_angle
            - yaw
        )

        # Front sector.
        front_mask = np.abs(
            angles
        ) <= math.radians(25.0)

        if np.any(front_mask):
            front_clearance = float(
                np.min(
                    clearance[front_mask]
                )
            )
        else:
            front_clearance = float(
                np.min(clearance)
            )

        # Left/right clearance used for hysteretic
        # obstacle circumnavigation.
        left_mask = (
            (angles > math.radians(15.0))
            & (angles < math.radians(120.0))
        )

        right_mask = (
            (angles < -math.radians(15.0))
            & (angles > -math.radians(120.0))
        )

        left_clearance = (
            float(np.mean(clearance[left_mask]))
            if np.any(left_mask)
            else 0.0
        )

        right_clearance = (
            float(np.mean(clearance[right_mask]))
            if np.any(right_mask)
            else 0.0
        )

        blocked = (
            front_clearance
            < self.block_distance
        )

        # Start an avoidance episode.
        if blocked and self.avoid_counter <= 0:

            if left_clearance >= right_clearance:
                self.avoid_direction = 1
            else:
                self.avoid_direction = -1

            # About 2 seconds at a 5 Hz scan rate.
            self.avoid_counter = 10

        if self.avoid_counter > 0:

            self.avoid_counter -= 1

            # Release avoidance early once front
            # becomes comfortably clear.
            if (
                front_clearance
                > self.slow_distance
                and self.avoid_counter <= 3
            ):
                self.avoid_counter = 0
                self.avoid_direction = 0

        # Score every visible Laser direction.
        best_score = -1e9
        best_direction = 0.0

        for i, direction in enumerate(angles):

            clear_norm = clamp(
                clearance[i]
                / self.slow_distance,
                0.0,
                1.0
            )

            goal_error = wrap_angle(
                goal_body_angle
                - direction
            )

            goal_score = math.cos(
                goal_error
            )

            smooth_score = math.cos(
                wrap_angle(
                    direction
                    - self.last_direction
                )
            )

            # Prefer headings that do not require
            # excessive rotation.
            turn_penalty = (
                abs(direction)
                / math.pi
            )

            side_bonus = 0.0

            if self.avoid_direction != 0:

                if (
                    self.avoid_direction
                    * direction
                    > math.radians(10.0)
                ):
                    side_bonus = 0.8
                else:
                    side_bonus = -0.5

            if blocked:

                score = (
                    0.7 * goal_score
                    + 2.4 * clear_norm
                    + 0.25 * smooth_score
                    + side_bonus
                    - 0.15 * turn_penalty
                )

            else:

                score = (
                    2.0 * goal_score
                    + 1.4 * clear_norm
                    + 0.35 * smooth_score
                    + side_bonus
                    - 0.10 * turn_penalty
                )

            # Never deliberately choose a direction
            # that is already extremely close to
            # an obstacle.
            if (
                clearance[i]
                < self.emergency_distance
            ):
                score -= 5.0

            if score > best_score:

                best_score = score
                best_direction = float(
                    direction
                )

        self.last_direction = (
            0.75 * self.last_direction
            + 0.25 * best_direction
        )

        steering = self.last_direction

        yaw_command = clamp(
            0.9 * steering,
            -self.max_yaw_command,
            self.max_yaw_command
        )

        # Horizontal velocity.
        if front_clearance <= self.emergency_distance:

            forward = 0.0
            mode = "EMERGENCY_TURN"

        else:

            clearance_factor = clamp(
                (
                    front_clearance
                    - self.emergency_distance
                )
                /
                (
                    self.slow_distance
                    - self.emergency_distance
                ),
                0.0,
                1.0
            )

            steering_factor = max(
                0.0,
                math.cos(
                    min(
                        abs(steering),
                        math.pi / 2.0
                    )
                )
            )

            goal_factor = clamp(
                horizontal_distance
                / 1.0,
                0.15,
                1.0
            )

            forward = (
                self.max_forward
                * clearance_factor
                * steering_factor
                * goal_factor
            )

            # Rotate in place when facing strongly
            # away from the selected free direction.
            if abs(steering) > math.radians(65.0):
                forward = 0.0

            if blocked:
                mode = "AVOID"
            else:
                mode = "GOAL"

        # If horizontally aligned with the goal,
        # do not wander while only correcting depth.
        if horizontal_distance < 0.15:
            forward = 0.0
            yaw_command = 0.0

        # Vertical guidance.
        vertical_limit = self.max_vertical

        # Slow down in the medium-transition region.
        if abs(z) < 0.20:
            vertical_limit = (
                self.surface_vertical_limit
            )

        vertical = clamp(
            0.8 * dz,
            -vertical_limit,
            vertical_limit
        )

        # Maintain an effective vertical command
        # until the vehicle is genuinely close to
        # the requested depth.
        #
        # Runtime tests showed that commands around
        # 0.10-0.12 m/s can stall underwater, while
        # 0.15 m/s is sufficient to continue the
        # descent.
        vertical_deadband = 0.08

        if dz < -vertical_deadband:

            vertical = min(
                vertical,
                -0.15
            )

        elif dz > vertical_deadband:

            vertical = max(
                vertical,
                0.15
            )

        else:

            vertical = clamp(
                0.8 * dz,
                -0.10,
                0.10
            )

        action = np.array(
            [
                forward,
                vertical,
                yaw_command
            ],
            dtype=float
        )

        debug = {
            "mode": mode,
            "distance": total_distance,
            "horizontal_distance":
                horizontal_distance,
            "goal_body_angle":
                goal_body_angle,
            "selected_direction":
                best_direction,
            "front_clearance":
                front_clearance,
            "left_clearance":
                left_clearance,
            "right_clearance":
                right_clearance
        }

        return action, debug


class ReactiveNavigationNode:

    def __init__(self):

        self.goal_x = rospy.get_param(
            "~goal_x",
            2.0
        )

        self.goal_y = rospy.get_param(
            "~goal_y",
            3.0
        )

        self.goal_z = rospy.get_param(
            "~goal_z",
            -0.5
        )

        self.planner = ReactivePlanner()

        self.planner.arriving_distance = (
            rospy.get_param(
                "~arriving_distance",
                0.25
            )
        )

        self.scan = None
        self.odom = None

        self.finished = False
        self.counter = 0

        self.pub = rospy.Publisher(
            "/hydrone_aerial_underwater0/cmd_vel",
            Twist,
            queue_size=5
        )

        self.scan_sub = rospy.Subscriber(
            "/hydrone_aerial_underwater0/scan",
            LaserScan,
            self.scan_callback,
            queue_size=1
        )

        self.odom_sub = rospy.Subscriber(
            "/hydrone_aerial_underwater0/"
            "ground_truth/odometry",
            Odometry,
            self.odom_callback,
            queue_size=1
        )

        rospy.on_shutdown(
            self.stop
        )


    def scan_callback(self, msg):
        self.scan = msg


    def odom_callback(self, msg):
        self.odom = msg


    def stop(self):

        try:
            self.pub.publish(
                Twist()
            )
        except Exception:
            pass


    def run(self):

        rate = rospy.Rate(5)

        rospy.loginfo(
            "Reactive mapless navigation started"
        )

        rospy.loginfo(
            "Goal = (%.2f, %.2f, %.2f)",
            self.goal_x,
            self.goal_y,
            self.goal_z
        )

        while not rospy.is_shutdown():

            if self.scan is None or self.odom is None:

                rate.sleep()
                continue

            p = (
                self.odom
                .pose
                .pose
                .position
            )

            q = (
                self.odom
                .pose
                .pose
                .orientation
            )

            yaw = quaternion_to_yaw(
                q
            )

            action, debug = (
                self.planner.compute_action(
                    self.scan.ranges,
                    self.scan.angle_min,
                    self.scan.angle_increment,
                    self.scan.range_max,
                    p.x,
                    p.y,
                    p.z,
                    yaw,
                    self.goal_x,
                    self.goal_y,
                    self.goal_z
                )
            )

            cmd = Twist()

            cmd.linear.x = float(
                action[0]
            )

            cmd.linear.z = float(
                action[1]
            )

            cmd.angular.z = float(
                action[2]
            )

            self.pub.publish(
                cmd
            )

            self.counter += 1

            if (
                self.counter % 5
                == 0
            ):

                rospy.loginfo(
                    "mode=%s "
                    "pos=(%.2f, %.2f, %.2f) "
                    "dist=%.2f "
                    "front=%.2f "
                    "dir=%.1fdeg "
                    "action=(%.3f, %.3f, %.3f)",
                    debug.get(
                        "mode"
                    ),
                    p.x,
                    p.y,
                    p.z,
                    debug.get(
                        "distance",
                        -1.0
                    ),
                    debug.get(
                        "front_clearance",
                        -1.0
                    ),
                    math.degrees(
                        debug.get(
                            "selected_direction",
                            0.0
                        )
                    ),
                    action[0],
                    action[1],
                    action[2]
                )

            if (
                debug.get("mode")
                == "ARRIVED"
            ):

                self.stop()

                rospy.loginfo(
                    "GOAL REACHED"
                )

                break

            rate.sleep()

        self.stop()


if __name__ == "__main__":

    rospy.init_node(
        "hydrone_reactive_mapless_nav"
    )

    node = ReactiveNavigationNode()

    node.run()
