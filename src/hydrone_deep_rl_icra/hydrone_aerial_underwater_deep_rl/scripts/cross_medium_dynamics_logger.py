#!/usr/bin/env python3

import csv
import math
import os
import threading
from datetime import datetime

import numpy as np
import rospy

from mav_msgs.msg import Actuators
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def quaternion_to_euler(q):
    # roll
    sinr_cosp = 2.0 * (q.w * q.x + q.y * q.z)
    cosr_cosp = 1.0 - 2.0 * (q.x * q.x + q.y * q.y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # pitch
    sinp = 2.0 * (q.w * q.y - q.z * q.x)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sinp)
    else:
        pitch = math.asin(sinp)

    # yaw
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return roll, pitch, yaw


def rad_s_to_rpm(value):
    if not np.isfinite(value):
        return float("nan")
    return value * 60.0 / (2.0 * math.pi)


class CrossMediumDynamicsLogger:
    def __init__(self):
        self.namespace = rospy.get_param(
            "~namespace",
            "/hydrone_aerial_underwater0"
        ).rstrip("/")
        self.output_dir = os.path.expanduser(
            rospy.get_param(
                "~output_dir",
                "/home/rosnoetic/hydrone_repro/dynamics"
            )
        )
        self.run_name = rospy.get_param(
            "~run_name",
            "cross_medium"
        )
        self.sample_rate = float(
            rospy.get_param("~sample_rate", 50.0)
        )
        self.duration = float(
            rospy.get_param("~duration", 0.0)
        )
        self.water_z = float(
            rospy.get_param("~water_z", 0.0)
        )
        self.transition_half = float(
            rospy.get_param(
                "~transition_half_thickness",
                0.20
            )
        )

        self.odom_topic = rospy.get_param(
            "~odom_topic",
            self.namespace + "/ground_truth/odometry"
        )
        self.motor_topic = rospy.get_param(
            "~motor_topic",
            self.namespace + "/command/motor_speed"
        )
        self.submerged_topic = rospy.get_param(
            "~submerged_topic",
            self.namespace + "/is_submerged"
        )

        os.makedirs(self.output_dir, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.prefix = os.path.join(
            self.output_dir,
            "{}_{}".format(stamp, self.run_name)
        )

        self.lock = threading.Lock()
        self.odom = None
        self.motor_cmd = [float("nan")] * 4
        self.motor_msg_len = 0
        self.submerged_signal = False
        self.submerged_received = False
        self.rows = []
        self.start_sim_time = None
        self.last_sample_t = None
        self.last_velocity = None
        self.finished = False

        self.odom_sub = rospy.Subscriber(
            self.odom_topic,
            Odometry,
            self.odom_callback,
            queue_size=1
        )
        self.motor_sub = rospy.Subscriber(
            self.motor_topic,
            Actuators,
            self.motor_callback,
            queue_size=10
        )
        self.submerged_sub = rospy.Subscriber(
            self.submerged_topic,
            Bool,
            self.submerged_callback,
            queue_size=10
        )

        self.timer = rospy.Timer(
            rospy.Duration(1.0 / self.sample_rate),
            self.sample
        )
        rospy.on_shutdown(self.finalize)

        rospy.loginfo("Cross-medium dynamics logger started")
        rospy.loginfo("namespace        = %s", self.namespace)
        rospy.loginfo("odometry topic   = %s", self.odom_topic)
        rospy.loginfo("motor cmd topic  = %s", self.motor_topic)
        rospy.loginfo("submerged topic  = %s", self.submerged_topic)
        rospy.loginfo("water surface z  = %.3f m", self.water_z)
        rospy.loginfo("output prefix    = %s", self.prefix)
        rospy.loginfo(
            "NOTE: command/motor_speed is the controller motor-speed command, "
            "not independently measured rotor feedback."
        )

    def odom_callback(self, msg):
        with self.lock:
            self.odom = msg

    def motor_callback(self, msg):
        values = list(msg.angular_velocities)
        with self.lock:
            self.motor_msg_len = len(values)
            padded = values[:4]
            while len(padded) < 4:
                padded.append(float("nan"))
            self.motor_cmd = padded

    def submerged_callback(self, msg):
        with self.lock:
            self.submerged_signal = bool(msg.data)
            self.submerged_received = True

    def geometric_medium_state(self, z):
        upper = self.water_z + self.transition_half
        lower = self.water_z - self.transition_half
        if z > upper:
            return "AIR"
        if z < lower:
            return "WATER"
        return "TRANSITION"

    def sample(self, _event):
        if self.finished:
            return

        with self.lock:
            if self.odom is None:
                return
            odom = self.odom
            motors = list(self.motor_cmd)
            submerged_signal = self.submerged_signal
            submerged_received = self.submerged_received

        now = rospy.Time.now().to_sec()
        if self.start_sim_time is None:
            self.start_sim_time = now
        t = now - self.start_sim_time

        p = odom.pose.pose.position
        q = odom.pose.pose.orientation
        lv = odom.twist.twist.linear
        av = odom.twist.twist.angular

        roll, pitch, yaw = quaternion_to_euler(q)
        speed = math.sqrt(
            lv.x * lv.x + lv.y * lv.y + lv.z * lv.z
        )
        angular_speed = math.sqrt(
            av.x * av.x + av.y * av.y + av.z * av.z
        )

        ax = ay = az = accel_norm = float("nan")
        if (
            self.last_sample_t is not None
            and self.last_velocity is not None
        ):
            dt = t - self.last_sample_t
            if dt > 1e-6:
                ax = (lv.x - self.last_velocity[0]) / dt
                ay = (lv.y - self.last_velocity[1]) / dt
                az = (lv.z - self.last_velocity[2]) / dt
                accel_norm = math.sqrt(
                    ax * ax + ay * ay + az * az
                )

        self.last_sample_t = t
        self.last_velocity = (lv.x, lv.y, lv.z)

        motor_rpm = [rad_s_to_rpm(v) for v in motors]
        valid_rpm = [v for v in motor_rpm if np.isfinite(v)]
        if valid_rpm:
            motor_mean = float(np.mean(valid_rpm))
            motor_std = float(np.std(valid_rpm))
            motor_spread = float(
                np.max(valid_rpm) - np.min(valid_rpm)
            )
        else:
            motor_mean = motor_std = motor_spread = float("nan")

        medium_geom = self.geometric_medium_state(p.z)
        submerged_value = (
            int(submerged_signal)
            if submerged_received
            else int(p.z < self.water_z - self.transition_half)
        )
        submerged_source = (
            "TOPIC"
            if submerged_received
            else "GEOMETRIC_FALLBACK"
        )

        row = {
            "time_s": t,
            "x_m": p.x,
            "y_m": p.y,
            "z_m": p.z,
            "vx_mps": lv.x,
            "vy_mps": lv.y,
            "vz_mps": lv.z,
            "speed_mps": speed,
            "ax_mps2": ax,
            "ay_mps2": ay,
            "az_mps2": az,
            "accel_norm_mps2": accel_norm,
            "roll_deg": math.degrees(roll),
            "pitch_deg": math.degrees(pitch),
            "yaw_deg": math.degrees(yaw),
            "wx_rad_s": av.x,
            "wy_rad_s": av.y,
            "wz_rad_s": av.z,
            "angular_speed_rad_s": angular_speed,
            "medium_geom": medium_geom,
            "is_submerged": submerged_value,
            "submerged_source": submerged_source,
            "motor_msg_count": self.motor_msg_len,
            "motor0_rad_s": motors[0],
            "motor1_rad_s": motors[1],
            "motor2_rad_s": motors[2],
            "motor3_rad_s": motors[3],
            "motor0_rpm": motor_rpm[0],
            "motor1_rpm": motor_rpm[1],
            "motor2_rpm": motor_rpm[2],
            "motor3_rpm": motor_rpm[3],
            "motor_mean_rpm": motor_mean,
            "motor_std_rpm": motor_std,
            "motor_spread_rpm": motor_spread
        }
        self.rows.append(row)

        if (
            self.duration > 0.0
            and t >= self.duration
        ):
            rospy.signal_shutdown("requested logging duration reached")

    def save_csv(self):
        csv_path = self.prefix + "_dynamics.csv"
        if not self.rows:
            return None
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=list(self.rows[0].keys())
            )
            writer.writeheader()
            writer.writerows(self.rows)
        return csv_path

    def add_medium_markers(self, ax, t, states):
        # Mark geometric water-surface transition times with vertical lines.
        last_state = states[0] if states else None
        for i in range(1, len(states)):
            if states[i] != last_state:
                ax.axvline(t[i], linestyle="--", linewidth=0.8)
                last_state = states[i]

    def save_plots(self):
        if not self.rows:
            return []

        t = np.array([r["time_s"] for r in self.rows])
        states = [r["medium_geom"] for r in self.rows]
        paths = []

        # 1. Motor command speed in RPM.
        fig, ax = plt.subplots(figsize=(10, 5.5))
        for i in range(4):
            rpm = np.array([
                r["motor{}_rpm".format(i)]
                for r in self.rows
            ])
            ax.plot(t, rpm, label="Motor {}".format(i))
        self.add_medium_markers(ax, t, states)
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Motor speed command [RPM]")
        ax.set_title("Hydrone motor-speed command across media")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        p = self.prefix + "_motor_rpm.png"
        fig.savefig(p, dpi=180)
        plt.close(fig)
        paths.append(p)

        # 2. Pitch.
        pitch = np.array([r["pitch_deg"] for r in self.rows])
        fig, ax = plt.subplots(figsize=(10, 5.0))
        ax.plot(t, pitch)
        self.add_medium_markers(ax, t, states)
        ax.axhline(0.0, linewidth=0.8)
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Pitch [deg]")
        ax.set_title("Pitch response across air-water transition")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        p = self.prefix + "_pitch.png"
        fig.savefig(p, dpi=180)
        plt.close(fig)
        paths.append(p)

        # 3. Translational speed and vertical speed.
        speed = np.array([r["speed_mps"] for r in self.rows])
        vz = np.array([r["vz_mps"] for r in self.rows])
        fig, ax = plt.subplots(figsize=(10, 5.0))
        ax.plot(t, speed, label="|v|")
        ax.plot(t, vz, label="vz")
        self.add_medium_markers(ax, t, states)
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Velocity [m/s]")
        ax.set_title("Velocity response across media")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        p = self.prefix + "_velocity.png"
        fig.savefig(p, dpi=180)
        plt.close(fig)
        paths.append(p)

        # 4. Depth and submerged state.
        z = np.array([r["z_m"] for r in self.rows])
        submerged = np.array([
            r["is_submerged"] for r in self.rows
        ])
        fig, ax = plt.subplots(figsize=(10, 5.0))
        ax.plot(t, z, label="z")
        ax.axhline(self.water_z, linestyle="--", linewidth=0.8)
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Z [m]")
        ax2 = ax.twinx()
        ax2.step(t, submerged, where="post", label="submerged")
        ax2.set_ylabel("Submerged [0/1]")
        ax2.set_ylim(-0.1, 1.1)
        ax.set_title("Depth and immersion state")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        p = self.prefix + "_immersion.png"
        fig.savefig(p, dpi=180)
        plt.close(fig)
        paths.append(p)

        return paths

    def save_phase_summary(self):
        if not self.rows:
            return None

        summary_path = self.prefix + "_phase_summary.csv"
        metrics = []

        for phase in ["AIR", "TRANSITION", "WATER"]:
            subset = [
                r for r in self.rows
                if r["medium_geom"] == phase
            ]
            if not subset:
                continue

            def finite_values(key):
                values = np.array(
                    [r[key] for r in subset],
                    dtype=float
                )
                return values[np.isfinite(values)]

            pitch = finite_values("pitch_deg")
            speed = finite_values("speed_mps")
            vz = finite_values("vz_mps")
            accel = finite_values("accel_norm_mps2")
            rpm = finite_values("motor_mean_rpm")
            spread = finite_values("motor_spread_rpm")

            metrics.append({
                "phase": phase,
                "samples": len(subset),
                "duration_s": (
                    subset[-1]["time_s"]
                    - subset[0]["time_s"]
                    if len(subset) > 1
                    else 0.0
                ),
                "speed_mean_mps": (
                    float(np.mean(speed)) if len(speed) else float("nan")
                ),
                "speed_std_mps": (
                    float(np.std(speed)) if len(speed) else float("nan")
                ),
                "vz_mean_mps": (
                    float(np.mean(vz)) if len(vz) else float("nan")
                ),
                "pitch_mean_deg": (
                    float(np.mean(pitch)) if len(pitch) else float("nan")
                ),
                "pitch_rms_deg": (
                    float(np.sqrt(np.mean(pitch ** 2)))
                    if len(pitch)
                    else float("nan")
                ),
                "pitch_peak_abs_deg": (
                    float(np.max(np.abs(pitch)))
                    if len(pitch)
                    else float("nan")
                ),
                "accel_mean_mps2": (
                    float(np.mean(accel)) if len(accel) else float("nan")
                ),
                "motor_mean_rpm": (
                    float(np.mean(rpm)) if len(rpm) else float("nan")
                ),
                "motor_spread_mean_rpm": (
                    float(np.mean(spread))
                    if len(spread)
                    else float("nan")
                )
            })

        if not metrics:
            return None

        with open(summary_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=list(metrics[0].keys())
            )
            writer.writeheader()
            writer.writerows(metrics)

        return summary_path

    def finalize(self):
        if self.finished:
            return
        self.finished = True

        try:
            self.timer.shutdown()
        except Exception:
            pass

        rospy.loginfo("==========================================")
        rospy.loginfo("CROSS-MEDIUM DYNAMICS LOGGER FINALIZING")
        rospy.loginfo("==========================================")
        rospy.loginfo("samples recorded = %d", len(self.rows))

        if not self.rows:
            rospy.logwarn("No odometry samples were recorded.")
            return

        csv_path = self.save_csv()
        summary_path = self.save_phase_summary()
        plot_paths = self.save_plots()

        rospy.loginfo("CSV = %s", csv_path)
        rospy.loginfo("phase summary = %s", summary_path)
        for p in plot_paths:
            rospy.loginfo("plot = %s", p)

        if not self.submerged_received:
            rospy.logwarn(
                "No message was received from %s; immersion flag in the CSV "
                "used the geometric z-based fallback.",
                self.submerged_topic
            )

        if self.motor_msg_len == 0:
            rospy.logwarn(
                "No motor-speed message was received from %s. Check the "
                "topic with: rostopic list | grep -Ei 'motor|speed'",
                self.motor_topic
            )


if __name__ == "__main__":
    rospy.init_node("hydrone_cross_medium_dynamics_logger")
    logger = CrossMediumDynamicsLogger()
    rospy.spin()
