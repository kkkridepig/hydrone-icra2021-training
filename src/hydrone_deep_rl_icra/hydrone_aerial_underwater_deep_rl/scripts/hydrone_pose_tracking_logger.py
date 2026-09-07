#!/usr/bin/env python3

import os
import csv
import math
from datetime import datetime

import rospy

from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry


class PoseTrackingLogger:

    def __init__(self):

        self.run_name = rospy.get_param(
            "~run_name",
            "stage2_pose_transition"
        )

        self.namespace = rospy.get_param(
            "~namespace",
            "/hydrone_aerial_underwater0"
        ).rstrip("/")

        self.output_dir = os.path.expanduser(
            rospy.get_param(
                "~output_dir",
                "~/hydrone_repro/stage2_demo/tracking"
            )
        )

        os.makedirs(
            self.output_dir,
            exist_ok=True
        )

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        self.prefix = (
            timestamp
            + "_"
            + self.run_name
        )

        self.command_topic = (
            self.namespace
            + "/command/pose"
        )

        self.odom_topic = (
            self.namespace
            + "/ground_truth/odometry"
        )

        self.desired = None

        self.samples = []

        self.start_time = None
        self.saved = False

        self.command_sub = rospy.Subscriber(
            self.command_topic,
            PoseStamped,
            self.command_callback,
            queue_size=50
        )

        self.odom_sub = rospy.Subscriber(
            self.odom_topic,
            Odometry,
            self.odom_callback,
            queue_size=200
        )

        rospy.on_shutdown(
            self.save_results
        )

        rospy.loginfo(
            "Hydrone pose tracking logger started"
        )

        rospy.loginfo(
            "Desired pose topic = %s",
            self.command_topic
        )

        rospy.loginfo(
            "Actual pose topic = %s",
            self.odom_topic
        )

        rospy.loginfo(
            "Output directory = %s",
            self.output_dir
        )


    def command_callback(self, msg):

        p = msg.pose.position

        self.desired = (
            float(p.x),
            float(p.y),
            float(p.z)
        )


    def odom_callback(self, msg):

        if self.desired is None:
            return

        stamp = msg.header.stamp.to_sec()

        if stamp <= 0:
            stamp = rospy.Time.now().to_sec()

        if self.start_time is None:
            self.start_time = stamp

        t = (
            stamp
            - self.start_time
        )

        p = msg.pose.pose.position

        ax = float(p.x)
        ay = float(p.y)
        az = float(p.z)

        dx, dy, dz = self.desired

        ex = ax - dx
        ey = ay - dy
        ez = az - dz

        e3 = math.sqrt(
            ex * ex
            + ey * ey
            + ez * ez
        )

        self.samples.append(
            (
                t,

                ax,
                dx,
                ex,

                ay,
                dy,
                ey,

                az,
                dz,
                ez,

                e3
            )
        )


    @staticmethod
    def rmse(values):

        if not values:
            return float("nan")

        return math.sqrt(
            sum(
                v * v
                for v in values
            )
            / len(values)
        )


    def save_csv(self):

        path = os.path.join(
            self.output_dir,
            self.prefix
            + "_tracking.csv"
        )

        with open(
            path,
            "w",
            newline=""
        ) as f:

            writer = csv.writer(f)

            writer.writerow(
                [
                    "time_s",

                    "actual_x_m",
                    "desired_x_m",
                    "error_x_m",

                    "actual_y_m",
                    "desired_y_m",
                    "error_y_m",

                    "actual_z_m",
                    "desired_z_m",
                    "error_z_m",

                    "error_3d_m"
                ]
            )

            writer.writerows(
                self.samples
            )

        return path


    def calculate_metrics(self):

        ex = [
            row[3]
            for row in self.samples
        ]

        ey = [
            row[6]
            for row in self.samples
        ]

        ez = [
            row[9]
            for row in self.samples
        ]

        e3 = [
            row[10]
            for row in self.samples
        ]

        return {
            "samples": len(
                self.samples
            ),

            "rmse_x_m":
                self.rmse(ex),

            "rmse_y_m":
                self.rmse(ey),

            "rmse_z_m":
                self.rmse(ez),

            "max_abs_x_m":
                max(
                    abs(v)
                    for v in ex
                ),

            "max_abs_y_m":
                max(
                    abs(v)
                    for v in ey
                ),

            "max_abs_z_m":
                max(
                    abs(v)
                    for v in ez
                ),

            "mean_3d_error_m":
                sum(e3)
                / len(e3),

            "max_3d_error_m":
                max(e3),
        }


    def save_metrics(
        self,
        metrics
    ):

        path = os.path.join(
            self.output_dir,
            self.prefix
            + "_metrics.csv"
        )

        with open(
            path,
            "w",
            newline=""
        ) as f:

            writer = csv.writer(f)

            writer.writerow(
                [
                    "metric",
                    "value"
                ]
            )

            for key, value \
                    in metrics.items():

                writer.writerow(
                    [
                        key,
                        value
                    ]
                )

        return path


    def plot_axis(
        self,
        times,
        actual,
        desired,
        axis_name
    ):

        import matplotlib

        matplotlib.use(
            "Agg"
        )

        import matplotlib.pyplot as plt

        fig = plt.figure(
            figsize=(9, 5.5)
        )

        ax = fig.add_subplot(
            111
        )

        ax.plot(
            times,
            actual,
            linewidth=1.6,
            label="Actual position"
        )

        ax.plot(
            times,
            desired,
            linewidth=1.6,
            linestyle="--",
            label="Desired position"
        )

        ax.set_xlabel(
            "Time (s)"
        )

        ax.set_ylabel(
            axis_name.upper()
            + " position (m)"
        )

        ax.set_title(
            "Hydrone "
            + axis_name.upper()
            + "-axis Position Tracking"
        )

        ax.grid(
            True,
            alpha=0.3
        )

        ax.legend()

        fig.tight_layout()

        path = os.path.join(
            self.output_dir,
            self.prefix
            + "_"
            + axis_name
            + "_position.png"
        )

        fig.savefig(
            path,
            dpi=300,
            bbox_inches="tight"
        )

        plt.close(fig)

        return path


    def plot_errors(
        self,
        times,
        ex,
        ey,
        ez
    ):

        import matplotlib

        matplotlib.use(
            "Agg"
        )

        import matplotlib.pyplot as plt

        fig = plt.figure(
            figsize=(9, 5.5)
        )

        ax = fig.add_subplot(
            111
        )

        ax.plot(
            times,
            ex,
            label="X error"
        )

        ax.plot(
            times,
            ey,
            label="Y error"
        )

        ax.plot(
            times,
            ez,
            label="Z error"
        )

        ax.axhline(
            0.0,
            linewidth=0.8,
            linestyle="--"
        )

        ax.set_xlabel(
            "Time (s)"
        )

        ax.set_ylabel(
            "Position error (m)"
        )

        ax.set_title(
            "Hydrone XYZ Pose Tracking Error"
        )

        ax.grid(
            True,
            alpha=0.3
        )

        ax.legend()

        fig.tight_layout()

        path = os.path.join(
            self.output_dir,
            self.prefix
            + "_xyz_error.png"
        )

        fig.savefig(
            path,
            dpi=300,
            bbox_inches="tight"
        )

        plt.close(fig)

        return path


    def save_results(self):

        if self.saved:
            return

        self.saved = True

        print()
        print(
            "=========================================="
        )
        print(
            "HYDRONE POSE TRACKING LOGGER FINALIZING"
        )
        print(
            "=========================================="
        )

        print(
            "samples recorded =",
            len(self.samples)
        )

        if len(self.samples) < 2:

            print(
                "Not enough samples."
            )

            return

        csv_path = self.save_csv()

        metrics = (
            self.calculate_metrics()
        )

        metrics_path = (
            self.save_metrics(
                metrics
            )
        )

        times = [
            row[0]
            for row in self.samples
        ]

        actual_x = [
            row[1]
            for row in self.samples
        ]

        desired_x = [
            row[2]
            for row in self.samples
        ]

        ex = [
            row[3]
            for row in self.samples
        ]

        actual_y = [
            row[4]
            for row in self.samples
        ]

        desired_y = [
            row[5]
            for row in self.samples
        ]

        ey = [
            row[6]
            for row in self.samples
        ]

        actual_z = [
            row[7]
            for row in self.samples
        ]

        desired_z = [
            row[8]
            for row in self.samples
        ]

        ez = [
            row[9]
            for row in self.samples
        ]

        x_plot = self.plot_axis(
            times,
            actual_x,
            desired_x,
            "x"
        )

        y_plot = self.plot_axis(
            times,
            actual_y,
            desired_y,
            "y"
        )

        z_plot = self.plot_axis(
            times,
            actual_z,
            desired_z,
            "z"
        )

        error_plot = (
            self.plot_errors(
                times,
                ex,
                ey,
                ez
            )
        )

        print(
            "CSV =",
            csv_path
        )

        print(
            "Metrics =",
            metrics_path
        )

        print(
            "X plot =",
            x_plot
        )

        print(
            "Y plot =",
            y_plot
        )

        print(
            "Z plot =",
            z_plot
        )

        print(
            "Error plot =",
            error_plot
        )

        print()

        print(
            "RMSE X = %.6f m"
            % metrics["rmse_x_m"]
        )

        print(
            "RMSE Y = %.6f m"
            % metrics["rmse_y_m"]
        )

        print(
            "RMSE Z = %.6f m"
            % metrics["rmse_z_m"]
        )

        print(
            "MAX |X error| = %.6f m"
            % metrics["max_abs_x_m"]
        )

        print(
            "MAX |Y error| = %.6f m"
            % metrics["max_abs_y_m"]
        )

        print(
            "MAX |Z error| = %.6f m"
            % metrics["max_abs_z_m"]
        )

        print(
            "Mean 3D error = %.6f m"
            % metrics[
                "mean_3d_error_m"
            ]
        )

        print(
            "Max 3D error = %.6f m"
            % metrics[
                "max_3d_error_m"
            ]
        )

        print(
            "=========================================="
        )


if __name__ == "__main__":

    rospy.init_node(
        "hydrone_pose_tracking_logger"
    )

    logger = PoseTrackingLogger()

    rospy.spin()
