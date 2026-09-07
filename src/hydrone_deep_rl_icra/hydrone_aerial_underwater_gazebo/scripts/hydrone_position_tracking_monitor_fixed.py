#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Hydrone desired-vs-actual position tracking monitor.

Recommended run order:
  1) Gazebo started with paused:=true.
  2) Hydrone spawned.
  3) Start THIS monitor.
  4) Start hydrone_transition_demo.py.

The monitor starts recording when the first desired PoseStamped command arrives.
After the demo stops publishing /command/pose for ~end_timeout wall-clock
seconds, it automatically:
  - computes X/Y/Z and 3D tracking errors,
  - prints RMSE / MAE / max error,
  - saves CSV,
  - saves position tracking plots,
  - saves error plots,
  - saves XY/XZ/YZ trajectory projections.

Error definition:
    e_x = x_desired - x_actual
    e_y = y_desired - y_actual
    e_z = z_desired - z_actual
"""

import csv
import math
import os
import time
from datetime import datetime

import numpy as np
import rospy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool


COMMAND_TOPIC = "/hydrone_aerial_underwater/command/pose"
ODOM_TOPIC = "/hydrone_aerial_underwater/odometry_sensor1/odometry"
SUBMERGED_TOPIC = "/hydrone_aerial_underwater/is_submerged"


class HydronePositionMonitor:
    def __init__(self):
        # ---------------------------
        # Parameters
        # ---------------------------
        self.end_timeout = float(rospy.get_param("~end_timeout", 3.0))
        self.min_record_time = float(rospy.get_param("~min_record_time", 2.0))
        self.show_plot = bool(rospy.get_param("~show_plot", True))
        self.print_rate = float(rospy.get_param("~print_rate", 1.0))
        self.initial_error_warning = float(
            rospy.get_param("~initial_error_warning", 2.0)
        )

        default_root = os.path.expanduser("~/hydrone_repro/plots")
        self.output_root = os.path.expanduser(
            rospy.get_param("~output_dir", default_root)
        )

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = os.path.join(
            self.output_root,
            "tracking_" + stamp
        )
        os.makedirs(self.output_dir, exist_ok=True)

        # ---------------------------
        # Desired state
        # ---------------------------
        self.have_desired = False
        self.des_x = 0.0
        self.des_y = 0.0
        self.des_z = 0.0

        # ---------------------------
        # Experiment state
        # ---------------------------
        self.recording_started = False
        self.start_ros_time = None
        self.last_command_wall_time = None
        self.last_print_wall_time = 0.0
        self.first_odom_checked = False

        # ---------------------------
        # Medium state
        # ---------------------------
        self.have_submerged = False
        self.is_submerged = False

        # ---------------------------
        # Recorded arrays
        # ---------------------------
        self.time_data = []

        self.x_des = []
        self.y_des = []
        self.z_des = []

        self.x_real = []
        self.y_real = []
        self.z_real = []

        self.err_x = []
        self.err_y = []
        self.err_z = []
        self.err_norm = []

        self.submerged_data = []

        # ---------------------------
        # ROS subscribers
        # ---------------------------
        self.command_sub = rospy.Subscriber(
            COMMAND_TOPIC,
            PoseStamped,
            self.command_callback,
            queue_size=20
        )

        self.odom_sub = rospy.Subscriber(
            ODOM_TOPIC,
            Odometry,
            self.odom_callback,
            queue_size=200
        )

        self.submerged_sub = rospy.Subscriber(
            SUBMERGED_TOPIC,
            Bool,
            self.submerged_callback,
            queue_size=20
        )

        rospy.loginfo("Hydrone position monitor started.")
        rospy.loginfo("Desired-position topic: %s", COMMAND_TOPIC)
        rospy.loginfo("Actual-position topic:  %s", ODOM_TOPIC)
        rospy.loginfo("Submerged-state topic:  %s", SUBMERGED_TOPIC)
        rospy.loginfo("Waiting for the demo's first desired-position command ...")
        rospy.loginfo("Output directory: %s", self.output_dir)

    def command_callback(self, msg):
        self.des_x = msg.pose.position.x
        self.des_y = msg.pose.position.y
        self.des_z = msg.pose.position.z

        self.have_desired = True
        self.last_command_wall_time = time.monotonic()

        if not self.recording_started:
            self.recording_started = True
            self.start_ros_time = rospy.Time.now().to_sec()

            rospy.loginfo("")
            rospy.loginfo("First desired-position command received.")
            rospy.loginfo("Tracking recording starts now.")
            rospy.loginfo(
                "Initial desired position = (%.3f, %.3f, %.3f)",
                self.des_x, self.des_y, self.des_z
            )

    def submerged_callback(self, msg):
        self.is_submerged = bool(msg.data)
        self.have_submerged = True

    def odom_callback(self, msg):
        if not self.recording_started or not self.have_desired:
            return

        now_ros = rospy.Time.now().to_sec()
        elapsed = now_ros - self.start_ros_time

        p = msg.pose.pose.position
        x = p.x
        y = p.y
        z = p.z

        ex = self.des_x - x
        ey = self.des_y - y
        ez = self.des_z - z
        en = math.sqrt(ex * ex + ey * ey + ez * ez)

        if not self.first_odom_checked:
            self.first_odom_checked = True

            rospy.loginfo("")
            rospy.loginfo(
                "Initial actual position  = (%.3f, %.3f, %.3f)",
                x, y, z
            )
            rospy.loginfo(
                "Initial desired position = (%.3f, %.3f, %.3f)",
                self.des_x, self.des_y, self.des_z
            )
            rospy.loginfo("Initial 3D position error = %.3f m", en)

            if en > self.initial_error_warning:
                rospy.logwarn("")
                rospy.logwarn("INITIAL POSITION ERROR IS VERY LARGE: %.3f m", en)
                rospy.logwarn(
                    "The UAV may have moved/fallen before the demo started."
                )
                rospy.logwarn(
                    "Recommended: start Gazebo with paused:=true and let the "
                    "demo preload the initial command before unpausing."
                )

        self.time_data.append(elapsed)

        self.x_des.append(self.des_x)
        self.y_des.append(self.des_y)
        self.z_des.append(self.des_z)

        self.x_real.append(x)
        self.y_real.append(y)
        self.z_real.append(z)

        self.err_x.append(ex)
        self.err_y.append(ey)
        self.err_z.append(ez)
        self.err_norm.append(en)

        self.submerged_data.append(
            1 if (self.have_submerged and self.is_submerged) else 0
        )

        now_wall = time.monotonic()
        period = 1.0 / max(self.print_rate, 1e-6)

        if now_wall - self.last_print_wall_time >= period:
            self.last_print_wall_time = now_wall

            medium = "WATER" if (
                self.have_submerged and self.is_submerged
            ) else "AIR"

            rospy.loginfo(
                "[%s] "
                "X des/real/err = %.3f / %.3f / %+.3f | "
                "Y des/real/err = %.3f / %.3f / %+.3f | "
                "Z des/real/err = %.3f / %.3f / %+.3f | "
                "|e| = %.3f m",
                medium,
                self.des_x, x, ex,
                self.des_y, y, ey,
                self.des_z, z, ez,
                en
            )

    @staticmethod
    def _array(values):
        return np.asarray(values, dtype=float)

    @classmethod
    def _rmse(cls, values):
        a = cls._array(values)
        return float(np.sqrt(np.mean(a ** 2)))

    @classmethod
    def _mae(cls, values):
        a = cls._array(values)
        return float(np.mean(np.abs(a)))

    @classmethod
    def _max_abs(cls, values):
        a = cls._array(values)
        return float(np.max(np.abs(a)))

    def save_csv(self):
        path = os.path.join(
            self.output_dir,
            "tracking_data.csv"
        )

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow([
                "time_s",
                "x_des_m", "x_real_m", "x_error_m",
                "y_des_m", "y_real_m", "y_error_m",
                "z_des_m", "z_real_m", "z_error_m",
                "position_error_norm_m",
                "is_submerged"
            ])

            for row in zip(
                self.time_data,
                self.x_des, self.x_real, self.err_x,
                self.y_des, self.y_real, self.err_y,
                self.z_des, self.z_real, self.err_z,
                self.err_norm,
                self.submerged_data
            ):
                writer.writerow(row)

        return path

    def save_summary(self):
        if len(self.time_data) < 2:
            duration = 0.0
        else:
            duration = self.time_data[-1] - self.time_data[0]

        metrics = {
            "x_rmse": self._rmse(self.err_x),
            "x_mae": self._mae(self.err_x),
            "x_max": self._max_abs(self.err_x),

            "y_rmse": self._rmse(self.err_y),
            "y_mae": self._mae(self.err_y),
            "y_max": self._max_abs(self.err_y),

            "z_rmse": self._rmse(self.err_z),
            "z_mae": self._mae(self.err_z),
            "z_max": self._max_abs(self.err_z),

            "e3_rmse": self._rmse(self.err_norm),
            "e3_mae": self._mae(self.err_norm),
            "e3_max": self._max_abs(self.err_norm),
        }

        path = os.path.join(
            self.output_dir,
            "tracking_summary.txt"
        )

        lines = [
            "Hydrone Position Tracking Summary",
            "=================================",
            "Error definition: desired - actual",
            "",
            "Duration: {:.6f} s".format(duration),
            "",
            "X axis:",
            "  RMSE    = {:.6f} m".format(metrics["x_rmse"]),
            "  MAE     = {:.6f} m".format(metrics["x_mae"]),
            "  Max|e|  = {:.6f} m".format(metrics["x_max"]),
            "",
            "Y axis:",
            "  RMSE    = {:.6f} m".format(metrics["y_rmse"]),
            "  MAE     = {:.6f} m".format(metrics["y_mae"]),
            "  Max|e|  = {:.6f} m".format(metrics["y_max"]),
            "",
            "Z axis:",
            "  RMSE    = {:.6f} m".format(metrics["z_rmse"]),
            "  MAE     = {:.6f} m".format(metrics["z_mae"]),
            "  Max|e|  = {:.6f} m".format(metrics["z_max"]),
            "",
            "3D position-error norm:",
            "  RMSE    = {:.6f} m".format(metrics["e3_rmse"]),
            "  MAE     = {:.6f} m".format(metrics["e3_mae"]),
            "  Max     = {:.6f} m".format(metrics["e3_max"]),
            ""
        ]

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        rospy.loginfo("")
        rospy.loginfo("========== Tracking summary ==========")
        rospy.loginfo("Duration: %.3f s", duration)
        rospy.loginfo(
            "X: RMSE=%.4f m, MAE=%.4f m, Max|e|=%.4f m",
            metrics["x_rmse"], metrics["x_mae"], metrics["x_max"]
        )
        rospy.loginfo(
            "Y: RMSE=%.4f m, MAE=%.4f m, Max|e|=%.4f m",
            metrics["y_rmse"], metrics["y_mae"], metrics["y_max"]
        )
        rospy.loginfo(
            "Z: RMSE=%.4f m, MAE=%.4f m, Max|e|=%.4f m",
            metrics["z_rmse"], metrics["z_mae"], metrics["z_max"]
        )
        rospy.loginfo(
            "3D: RMSE=%.4f m, MAE=%.4f m, Max=%.4f m",
            metrics["e3_rmse"], metrics["e3_mae"], metrics["e3_max"]
        )
        rospy.loginfo("======================================")

        return path

    def _shade_submerged_regions(self, ax):
        """
        Shade intervals during which is_submerged == true.
        """
        if len(self.time_data) < 2:
            return

        if not any(self.submerged_data):
            return

        t = np.asarray(self.time_data, dtype=float)
        s = np.asarray(self.submerged_data, dtype=int)

        active = False
        start_t = None

        for i in range(len(s)):
            if s[i] == 1 and not active:
                active = True
                start_t = t[i]

            if active:
                leaving = (s[i] == 0)
                last = (i == len(s) - 1)

                if leaving or last:
                    end_t = t[i]
                    ax.axvspan(
                        start_t,
                        end_t,
                        alpha=0.10
                    )
                    active = False
                    start_t = None

    @staticmethod
    def _disable_axis_offset(ax):
        """
        Prevent Matplotlib from displaying Y=10 as '+1e1' with tiny offsets.
        """
        try:
            ax.ticklabel_format(
                axis="y",
                style="plain",
                useOffset=False
            )
        except Exception:
            pass

    def make_plots(self):
        import matplotlib

        if not self.show_plot:
            matplotlib.use("Agg")

        import matplotlib.pyplot as plt

        t = np.asarray(self.time_data, dtype=float)

        desired = [
            np.asarray(self.x_des, dtype=float),
            np.asarray(self.y_des, dtype=float),
            np.asarray(self.z_des, dtype=float)
        ]

        actual = [
            np.asarray(self.x_real, dtype=float),
            np.asarray(self.y_real, dtype=float),
            np.asarray(self.z_real, dtype=float)
        ]

        errors = [
            np.asarray(self.err_x, dtype=float),
            np.asarray(self.err_y, dtype=float),
            np.asarray(self.err_z, dtype=float)
        ]

        labels = ["X", "Y", "Z"]

        # =====================================================
        # Figure 1: Desired vs actual X/Y/Z
        # =====================================================
        fig1, axes1 = plt.subplots(
            3,
            1,
            figsize=(11, 9),
            sharex=True
        )

        for i, ax in enumerate(axes1):
            ax.plot(
                t,
                desired[i],
                "--",
                linewidth=1.8,
                label="desired"
            )

            ax.plot(
                t,
                actual[i],
                linewidth=1.5,
                label="real"
            )

            self._shade_submerged_regions(ax)
            self._disable_axis_offset(ax)

            ax.set_ylabel("{} (m)".format(labels[i]))
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")

        # Explicit water-surface reference on Z.
        axes1[2].axhline(
            y=0.0,
            linestyle=":",
            linewidth=1.3,
            label="water surface z=0"
        )
        axes1[2].legend(loc="best")

        axes1[-1].set_xlabel("simulation time since demo start (s)")

        fig1.suptitle(
            "Hydrone Position Tracking: Desired vs Actual"
        )
        fig1.tight_layout(rect=[0, 0, 1, 0.97])

        position_path = os.path.join(
            self.output_dir,
            "position_tracking_xyz.png"
        )

        fig1.savefig(
            position_path,
            dpi=200,
            bbox_inches="tight"
        )

        # =====================================================
        # Figure 2: X/Y/Z errors + 3D norm
        # =====================================================
        fig2, axes2 = plt.subplots(
            4,
            1,
            figsize=(11, 11),
            sharex=True
        )

        for i, ax in enumerate(axes2[:3]):
            ax.plot(
                t,
                errors[i],
                linewidth=1.5
            )

            ax.axhline(
                y=0.0,
                linestyle=":",
                linewidth=1.0
            )

            self._shade_submerged_regions(ax)
            self._disable_axis_offset(ax)

            ax.set_ylabel(
                "{} error (m)".format(labels[i])
            )
            ax.grid(True, alpha=0.3)

        axes2[3].plot(
            t,
            np.asarray(self.err_norm, dtype=float),
            linewidth=1.5
        )

        self._shade_submerged_regions(axes2[3])
        self._disable_axis_offset(axes2[3])

        axes2[3].set_ylabel("3D |e| (m)")
        axes2[3].set_xlabel("simulation time since demo start (s)")
        axes2[3].grid(True, alpha=0.3)

        fig2.suptitle(
            "Hydrone Position Tracking Error "
            "(error = desired - actual)"
        )
        fig2.tight_layout(rect=[0, 0, 1, 0.97])

        error_path = os.path.join(
            self.output_dir,
            "position_error_xyz.png"
        )

        fig2.savefig(
            error_path,
            dpi=200,
            bbox_inches="tight"
        )

        # =====================================================
        # Figure 3: 2D trajectory projections
        # =====================================================
        fig3, axes3 = plt.subplots(
            1,
            3,
            figsize=(15, 5)
        )

        # XY
        axes3[0].plot(
            self.x_des,
            self.y_des,
            "--",
            linewidth=1.8,
            label="desired"
        )
        axes3[0].plot(
            self.x_real,
            self.y_real,
            linewidth=1.5,
            label="real"
        )
        axes3[0].set_xlabel("X (m)")
        axes3[0].set_ylabel("Y (m)")
        axes3[0].set_title("XY projection")
        axes3[0].grid(True, alpha=0.3)
        axes3[0].legend(loc="best")
        self._disable_axis_offset(axes3[0])

        # XZ
        axes3[1].plot(
            self.x_des,
            self.z_des,
            "--",
            linewidth=1.8,
            label="desired"
        )
        axes3[1].plot(
            self.x_real,
            self.z_real,
            linewidth=1.5,
            label="real"
        )
        axes3[1].axhline(
            y=0.0,
            linestyle=":",
            linewidth=1.3,
            label="water surface z=0"
        )
        axes3[1].set_xlabel("X (m)")
        axes3[1].set_ylabel("Z (m)")
        axes3[1].set_title("XZ projection")
        axes3[1].grid(True, alpha=0.3)
        axes3[1].legend(loc="best")
        self._disable_axis_offset(axes3[1])

        # YZ
        axes3[2].plot(
            self.y_des,
            self.z_des,
            "--",
            linewidth=1.8,
            label="desired"
        )
        axes3[2].plot(
            self.y_real,
            self.z_real,
            linewidth=1.5,
            label="real"
        )
        axes3[2].axhline(
            y=0.0,
            linestyle=":",
            linewidth=1.3,
            label="water surface z=0"
        )
        axes3[2].set_xlabel("Y (m)")
        axes3[2].set_ylabel("Z (m)")
        axes3[2].set_title("YZ projection")
        axes3[2].grid(True, alpha=0.3)
        axes3[2].legend(loc="best")
        self._disable_axis_offset(axes3[2])

        fig3.suptitle(
            "Hydrone 2D Trajectory Projections"
        )
        fig3.tight_layout(rect=[0, 0, 1, 0.94])

        trajectory_path = os.path.join(
            self.output_dir,
            "trajectory_2d.png"
        )

        fig3.savefig(
            trajectory_path,
            dpi=200,
            bbox_inches="tight"
        )

        rospy.loginfo("Saved: %s", position_path)
        rospy.loginfo("Saved: %s", error_path)
        rospy.loginfo("Saved: %s", trajectory_path)

        if self.show_plot:
            rospy.loginfo(
                "Opening figures. Close the Matplotlib windows to exit."
            )
            plt.show()
        else:
            plt.close("all")

        return position_path, error_path, trajectory_path

    def finalize(self):
        if len(self.time_data) < 2:
            rospy.logwarn(
                "Not enough recorded samples to generate results."
            )
            return

        rospy.loginfo("")
        rospy.loginfo(
            "Demo command stream ended. Generating results ..."
        )

        csv_path = self.save_csv()
        summary_path = self.save_summary()
        self.make_plots()

        rospy.loginfo("CSV saved to: %s", csv_path)
        rospy.loginfo("Summary saved to: %s", summary_path)
        rospy.loginfo("All outputs are in: %s", self.output_dir)

    def run(self):
        check_rate = rospy.Rate(20.0)

        while not rospy.is_shutdown():
            if (
                self.recording_started
                and self.last_command_wall_time is not None
            ):
                inactive = (
                    time.monotonic()
                    - self.last_command_wall_time
                )

                if len(self.time_data) >= 2:
                    duration = (
                        self.time_data[-1]
                        - self.time_data[0]
                    )
                else:
                    duration = 0.0

                if (
                    inactive >= self.end_timeout
                    and duration >= self.min_record_time
                ):
                    self.finalize()
                    return

            check_rate.sleep()


def main():
    rospy.init_node(
        "hydrone_position_tracking_monitor",
        anonymous=False
    )

    try:
        node = HydronePositionMonitor()
        node.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as exc:
        rospy.logerr("Monitor failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
