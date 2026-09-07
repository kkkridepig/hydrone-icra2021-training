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
    return math.atan2(math.sin(angle), math.cos(angle))


def quaternion_to_yaw(q):
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class ReactivePlanner:
    """Reactive mapless planner with explicit obstacle-avoidance recovery.

    States:
        GOAL      : normal goal tracking
        AVOID     : commit to one side of an obstacle
        REACQUIRE : after bypass, remove side bias and deliberately
                    rotate/translate back toward the original goal
    """

    def __init__(self):
        # Action limits used by the current platform.
        self.max_forward = 0.25
        self.max_vertical = 0.18
        self.max_yaw_command = 0.25
        self.surface_vertical_limit = 0.15

        # Laser clearance thresholds [m].
        self.emergency_distance = 0.65
        self.block_distance = 1.00
        self.release_distance = 1.35
        self.slow_distance = 1.80

        # Arrival tolerances.
        self.arriving_distance = 0.18
        self.vertical_arrival_tolerance = 0.10

        # State-machine settings. Planner runs at approximately 5 Hz.
        self.avoid_min_cycles = 8
        self.clear_cycles_required = 3
        self.reacquire_cycles = 12

        self.state = "GOAL"
        self.state_counter = 0
        self.clear_counter = 0
        self.avoid_direction = 0  # +1 left, -1 right
        self.last_direction = 0.0

    def reset_avoidance(self):
        self.state = "GOAL"
        self.state_counter = 0
        self.clear_counter = 0
        self.avoid_direction = 0
        self.last_direction = 0.0

    def local_clearance(self, ranges):
        result = np.zeros_like(ranges)
        for i in range(len(ranges)):
            i0 = max(0, i - 1)
            i1 = min(len(ranges), i + 2)
            result[i] = np.min(ranges[i0:i1])
        return result

    @staticmethod
    def sector_min(clearance, angles, center, half_width, fallback):
        error = np.abs(
            np.arctan2(
                np.sin(angles - center),
                np.cos(angles - center)
            )
        )
        mask = error <= half_width
        if np.any(mask):
            return float(np.min(clearance[mask]))
        return fallback

    def choose_avoid_direction(self, left_clearance, right_clearance):
        if left_clearance >= right_clearance:
            self.avoid_direction = 1
        else:
            self.avoid_direction = -1

    def enter_avoid(self, left_clearance, right_clearance):
        # Preserve the current side if already known; otherwise select
        # the side with the larger mean clearance.
        if self.avoid_direction == 0:
            self.choose_avoid_direction(
                left_clearance,
                right_clearance
            )
        self.state = "AVOID"
        self.state_counter = 0
        self.clear_counter = 0

    def score_scan_direction(
        self,
        clearance,
        angles,
        goal_body_angle,
        avoid_mode
    ):
        best_score = -1e9
        best_direction = 0.0

        for i, direction in enumerate(angles):
            # Do not deliberately select very far rear headings. The
            # vehicle can rotate in place and then move forward.
            if abs(direction) > math.radians(135.0):
                continue

            clear_norm = clamp(
                clearance[i] / self.slow_distance,
                0.0,
                1.0
            )

            goal_error = wrap_angle(
                goal_body_angle - direction
            )
            goal_score = math.cos(goal_error)

            smooth_score = math.cos(
                wrap_angle(direction - self.last_direction)
            )

            turn_penalty = abs(direction) / math.pi

            if avoid_mode:
                # A committed side bias prevents left/right chattering.
                side = self.avoid_direction * direction
                if side > math.radians(12.0):
                    side_bonus = 1.25
                elif side > 0.0:
                    side_bonus = 0.45
                else:
                    side_bonus = -0.90

                score = (
                    0.35 * goal_score
                    + 3.00 * clear_norm
                    + 0.35 * smooth_score
                    + side_bonus
                    - 0.10 * turn_penalty
                )
            else:
                score = (
                    3.20 * goal_score
                    + 1.25 * clear_norm
                    + 0.25 * smooth_score
                    - 0.08 * turn_penalty
                )

            if clearance[i] < self.emergency_distance:
                score -= 8.0

            if score > best_score:
                best_score = score
                best_direction = float(direction)

        return best_direction

    def vertical_guidance(self, z, vz, goal_z):
        """Depth guidance with underwater stall compensation.

        Normal motion uses PD-like position/velocity feedback.

        The important additional rule is:
        if the vehicle is already underwater, still outside the
        vertical arrival tolerance, but its measured vertical
        velocity is almost zero, temporarily provide enough
        vertical authority to escape the buoyancy/hydrodynamic
        equilibrium.

        Unlike the old fixed minimum-speed controller, the
        compensation is removed as soon as real vertical motion
        resumes, which reduces target-depth overshoot.
        """

        dz = goal_z - z
        abs_error = abs(dz)

        # Normal command limit.
        limit = self.max_vertical

        # Reduce aggressive commands while crossing the surface.
        if abs(z) < 0.20:
            limit = self.surface_vertical_limit

        # Base PD-like depth guidance.
        vertical = (
            0.65 * dz
            - 0.35 * vz
        )

        vertical = clamp(
            vertical,
            -limit,
            limit
        )

        # ------------------------------------------------------
        # 1. Preserve useful authority when very far from depth
        # ------------------------------------------------------
        far_error = 0.35

        if dz < -far_error:
            vertical = min(
                vertical,
                -0.12
            )

        elif dz > far_error:
            vertical = max(
                vertical,
                0.12
            )

        # ------------------------------------------------------
        # 2. Underwater stall compensation
        #
        # Test 4 V3 showed:
        #
        #   z_goal ~= -0.50 m
        #   z      ~= -0.34 m
        #   dz     ~= -0.16 m
        #   vz     ~= 0
        #
        # while a command around -0.107 m/s was not sufficient
        # to produce further downward motion.
        #
        # Only boost the command when:
        #
        #   - fully underwater
        #   - outside arrival tolerance
        #   - actual vertical motion has almost stopped
        #
        # As soon as |vz| increases, normal PD control resumes.
        # ------------------------------------------------------

        underwater = z < -0.20

        stalled = abs(vz) < 0.03

        outside_arrival_band = (
            abs_error
            > self.vertical_arrival_tolerance
        )

        if (
            underwater
            and stalled
            and outside_arrival_band
        ):
            # More authority for errors above 14 cm.
            if abs_error > 0.14:
                min_authority = 0.15
            else:
                # Softer final approach between approximately
                # 10 cm and 14 cm.
                min_authority = 0.12

            if dz < 0.0:
                vertical = min(
                    vertical,
                    -min_authority
                )
            else:
                vertical = max(
                    vertical,
                    min_authority
                )

        # ------------------------------------------------------
        # 3. Very close to the requested depth:
        #    remove positional aggression and damp velocity.
        # ------------------------------------------------------

        if abs_error < 0.06:
            vertical = clamp(
                -0.40 * vz,
                -0.06,
                0.06
            )

        return clamp(
            vertical,
            -limit,
            limit
        )

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
        vx,
        vy,
        vz,
        goal_x,
        goal_y,
        goal_z
    ):
        ranges = np.asarray(ranges, dtype=float)

        if len(ranges) == 0:
            return np.zeros(3), {"mode": "NO_SCAN"}

        ranges = np.nan_to_num(
            ranges,
            nan=range_max,
            posinf=range_max,
            neginf=0.0
        )
        ranges = np.clip(ranges, 0.0, range_max)

        angles = (
            angle_min
            + np.arange(len(ranges)) * angle_increment
        )
        clearance = self.local_clearance(ranges)

        dx = goal_x - x
        dy = goal_y - y
        dz = goal_z - z

        horizontal_distance = math.hypot(dx, dy)
        total_distance = math.sqrt(
            dx * dx + dy * dy + dz * dz
        )

        # Use the ROS parameter instead of a hard-coded unused threshold.
        if (
            horizontal_distance <= self.arriving_distance
            and abs(dz) <= self.vertical_arrival_tolerance
        ):
            self.reset_avoidance()
            return (
                np.zeros(3),
                {
                    "mode": "ARRIVED",
                    "distance": total_distance,
                    "horizontal_distance": horizontal_distance,
                    "vertical_error": abs(dz)
                }
            )

        goal_world_angle = math.atan2(dy, dx)
        goal_body_angle = wrap_angle(goal_world_angle - yaw)

        global_min = float(np.min(clearance))

        front_clearance = self.sector_min(
            clearance,
            angles,
            0.0,
            math.radians(25.0),
            global_min
        )

        # Clearance specifically in the direction of the original goal.
        goal_clearance = self.sector_min(
            clearance,
            angles,
            goal_body_angle,
            math.radians(12.0),
            global_min
        )

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

        blocked = front_clearance < self.block_distance
        emergency = front_clearance <= self.emergency_distance

        # -------------------- State transitions --------------------
        if self.state == "GOAL" and blocked:
            self.enter_avoid(
                left_clearance,
                right_clearance
            )

        elif self.state == "REACQUIRE" and blocked:
            # Same obstacle (or a new one) blocks the goal again.
            self.enter_avoid(
                left_clearance,
                right_clearance
            )

        if self.state == "AVOID":
            self.state_counter += 1

            # We only leave AVOID after BOTH the front and the original
            # goal corridor are clear for several consecutive scans.
            path_clear = (
                front_clearance > self.release_distance
                and goal_clearance > self.block_distance
            )

            if (
                self.state_counter >= self.avoid_min_cycles
                and path_clear
            ):
                self.clear_counter += 1
            else:
                self.clear_counter = 0

            if self.clear_counter >= self.clear_cycles_required:
                self.state = "REACQUIRE"
                self.state_counter = 0
                self.clear_counter = 0
                self.avoid_direction = 0
                # Remove the old side-heading memory immediately. This
                # is the key fix for goal reacquisition.
                self.last_direction = clamp(
                    goal_body_angle,
                    -math.radians(45.0),
                    math.radians(45.0)
                )

        elif self.state == "REACQUIRE":
            self.state_counter += 1
            if (
                abs(goal_body_angle) < math.radians(12.0)
                or self.state_counter >= self.reacquire_cycles
            ):
                self.state = "GOAL"
                self.state_counter = 0
                self.clear_counter = 0
                self.last_direction = clamp(
                    goal_body_angle,
                    -math.radians(30.0),
                    math.radians(30.0)
                )

        # -------------------- Steering --------------------
        if self.state == "AVOID":
            best_direction = self.score_scan_direction(
                clearance,
                angles,
                goal_body_angle,
                avoid_mode=True
            )

            # During emergency turning, force a definite side turn rather
            # than allowing the scorer to choose a nearly straight ray.
            if emergency:
                best_direction = (
                    self.avoid_direction
                    * math.radians(70.0)
                )

            alpha = 0.45
            self.last_direction = (
                (1.0 - alpha) * self.last_direction
                + alpha * best_direction
            )
            mode = (
                "EMERGENCY_TURN"
                if emergency
                else "AVOID"
            )

        elif self.state == "REACQUIRE":
            # Deliberately point back to the original target. Clearance
            # still limits forward motion, so this is not blind tracking.
            best_direction = clamp(
                goal_body_angle,
                -math.radians(65.0),
                math.radians(65.0)
            )
            alpha = 0.55
            self.last_direction = (
                (1.0 - alpha) * self.last_direction
                + alpha * best_direction
            )
            mode = "REACQUIRE"

        else:
            # In normal GOAL mode, use the direct goal heading whenever
            # the goal corridor is safely open. This prevents residual
            # free-space scoring from keeping the vehicle off-axis.
            if goal_clearance > self.block_distance:
                best_direction = clamp(
                    goal_body_angle,
                    -math.radians(80.0),
                    math.radians(80.0)
                )
            else:
                best_direction = self.score_scan_direction(
                    clearance,
                    angles,
                    goal_body_angle,
                    avoid_mode=False
                )

            alpha = 0.50
            self.last_direction = (
                (1.0 - alpha) * self.last_direction
                + alpha * best_direction
            )
            mode = "GOAL"

        steering = self.last_direction
        yaw_command = clamp(
            1.05 * steering,
            -self.max_yaw_command,
            self.max_yaw_command
        )

        # -------------------- Forward speed --------------------
        if emergency:
            forward = 0.0
        else:
            clearance_factor = clamp(
                (
                    front_clearance - self.emergency_distance
                )
                /
                (
                    self.slow_distance - self.emergency_distance
                ),
                0.0,
                1.0
            )

            heading_factor = max(
                0.0,
                math.cos(
                    min(abs(steering), math.pi / 2.0)
                )
            )

            goal_factor = clamp(
                horizontal_distance / 1.0,
                0.15,
                1.0
            )

            forward = (
                self.max_forward
                * clearance_factor
                * heading_factor
                * goal_factor
            )

            if self.state == "AVOID":
                # Once the nose is no longer in the emergency zone, keep
                # a small translational crawl. Pure turning alone often
                # leaves the vehicle trapped beside a cylindrical object.
                if abs(steering) < math.radians(75.0):
                    forward = max(forward, 0.055)
                forward = min(forward, 0.16)

            elif self.state == "REACQUIRE":
                # Conservative acceleration while returning to goal line.
                forward = min(forward, 0.20)

            if abs(steering) > math.radians(75.0):
                forward = 0.0

        if horizontal_distance < self.arriving_distance:
            forward = 0.0
            yaw_command = 0.0

        vertical = self.vertical_guidance(
            z,
            vz,
            goal_z
        )

        action = np.array(
            [forward, vertical, yaw_command],
            dtype=float
        )

        debug = {
            "mode": mode,
            "planner_state": self.state,
            "distance": total_distance,
            "horizontal_distance": horizontal_distance,
            "goal_body_angle": goal_body_angle,
            "selected_direction": best_direction,
            "front_clearance": front_clearance,
            "goal_clearance": goal_clearance,
            "left_clearance": left_clearance,
            "right_clearance": right_clearance,
            "avoid_direction": self.avoid_direction,
            "state_counter": self.state_counter,
            "vx": vx,
            "vy": vy,
            "vz": vz
        }
        return action, debug


class ReactiveNavigationNode:
    def __init__(self):
        self.goal_x = rospy.get_param("~goal_x", 2.0)
        self.goal_y = rospy.get_param("~goal_y", 3.0)
        self.goal_z = rospy.get_param("~goal_z", -0.5)

        self.namespace = rospy.get_param(
            "~namespace",
            "/hydrone_aerial_underwater0"
        ).rstrip("/")

        self.planner = ReactivePlanner()
        self.planner.arriving_distance = rospy.get_param(
            "~arriving_distance",
            0.18
        )
        self.planner.vertical_arrival_tolerance = rospy.get_param(
            "~vertical_arrival_tolerance",
            0.10
        )

        self.scan = None
        self.odom = None
        self.counter = 0

        self.pub = rospy.Publisher(
            self.namespace + "/cmd_vel",
            Twist,
            queue_size=5
        )
        self.scan_sub = rospy.Subscriber(
            self.namespace + "/scan",
            LaserScan,
            self.scan_callback,
            queue_size=1
        )
        self.odom_sub = rospy.Subscriber(
            self.namespace + "/ground_truth/odometry",
            Odometry,
            self.odom_callback,
            queue_size=1
        )

        rospy.on_shutdown(self.stop)

    def scan_callback(self, msg):
        self.scan = msg

    def odom_callback(self, msg):
        self.odom = msg

    def stop(self):
        try:
            self.pub.publish(Twist())
        except Exception:
            pass

    def run(self):
        rate = rospy.Rate(5)

        rospy.loginfo("Reactive mapless navigation started")
        rospy.loginfo(
            "Goal = (%.2f, %.2f, %.2f)",
            self.goal_x,
            self.goal_y,
            self.goal_z
        )
        rospy.loginfo("Robot namespace = %s", self.namespace)

        while not rospy.is_shutdown():
            if self.scan is None or self.odom is None:
                rate.sleep()
                continue

            p = self.odom.pose.pose.position
            q = self.odom.pose.pose.orientation
            v = self.odom.twist.twist.linear

            yaw = quaternion_to_yaw(q)

            action, debug = self.planner.compute_action(
                self.scan.ranges,
                self.scan.angle_min,
                self.scan.angle_increment,
                self.scan.range_max,
                p.x,
                p.y,
                p.z,
                yaw,
                v.x,
                v.y,
                v.z,
                self.goal_x,
                self.goal_y,
                self.goal_z
            )

            cmd = Twist()
            cmd.linear.x = float(action[0])
            cmd.linear.z = float(action[1])
            cmd.angular.z = float(action[2])
            self.pub.publish(cmd)

            self.counter += 1
            if self.counter % 5 == 0:
                rospy.loginfo(
                    "mode=%s "
                    "pos=(%.2f, %.2f, %.2f) "
                    "dist=%.2f front=%.2f goal_clear=%.2f "
                    "goal_err=%.1fdeg dir=%.1fdeg "
                    "avoid_side=%d action=(%.3f, %.3f, %.3f)",
                    debug.get("mode"),
                    p.x,
                    p.y,
                    p.z,
                    debug.get("distance", -1.0),
                    debug.get("front_clearance", -1.0),
                    debug.get("goal_clearance", -1.0),
                    math.degrees(
                        debug.get("goal_body_angle", 0.0)
                    ),
                    math.degrees(
                        debug.get("selected_direction", 0.0)
                    ),
                    int(debug.get("avoid_direction", 0)),
                    action[0],
                    action[1],
                    action[2]
                )

            if debug.get("mode") == "ARRIVED":
                self.stop()
                rospy.loginfo("GOAL REACHED")
                break

            rate.sleep()

        self.stop()


if __name__ == "__main__":
    rospy.init_node("hydrone_reactive_mapless_nav")
    node = ReactiveNavigationNode()
    node.run()
