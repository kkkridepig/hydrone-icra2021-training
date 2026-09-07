#!/usr/bin/env python3

import os
import csv
import math
from datetime import datetime

import rospy
from nav_msgs.msg import Odometry


class TrackingLogger:

    def __init__(self):

        self.run_name = rospy.get_param(
            "~run_name",
            "hydrone_test"
        )

        self.output_dir = os.path.expanduser(
            rospy.get_param(
                "~output_dir",
                "~/hydrone_repro"
            )
        )

        self.goal_x = float(
            rospy.get_param(
                "~goal_x",
                2.0
            )
        )

        self.goal_y = float(
            rospy.get_param(
                "~goal_y",
                3.0
            )
        )

        self.goal_z = float(
            rospy.get_param(
                "~goal_z",
                -0.5
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

        self.start_time = None
        self.samples = []
        self.saved = False

        self.odom_topic = (
            "/hydrone_aerial_underwater0/"
            "ground_truth/odometry"
        )

        self.sub = rospy.Subscriber(
            self.odom_topic,
            Odometry,
            self.odom_callback,
            queue_size=100
        )

        rospy.on_shutdown(
            self.save_results
        )

        rospy.loginfo(
            "Hydrone tracking logger started"
        )

        rospy.loginfo(
            "Desired goal = "
            "(%.3f, %.3f, %.3f)",
            self.goal_x,
            self.goal_y,
            self.goal_z
        )

        rospy.loginfo(
            "Output directory = %s",
            self.output_dir
        )


    def odom_callback(self, msg):

        stamp = msg.header.stamp.to_sec()

        if stamp <= 0:
            stamp = rospy.Time.now().to_sec()

        if self.start_time is None:
            self.start_time = stamp

        t = stamp - self.start_time

        p = msg.pose.pose.position

        ax = float(p.x)
        ay = float(p.y)
        az = float(p.z)

        dx = self.goal_x
        dy = self.goal_y
        dz = self.goal_z

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


    def save_csv(self, rows=None):

        if rows is None:
            rows = list(self.samples)

        path = os.path.join(
            self.output_dir,
            self.prefix + "_tracking.csv"
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
                rows
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
        matplotlib.use("Agg")

        import matplotlib.pyplot as plt

        fig = plt.figure(
            figsize=(9, 5.5)
        )

        ax = fig.add_subplot(111)

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
            + "-axis Position"
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


    @staticmethod
    def rmse(values):

        return math.sqrt(
            sum(
                value * value
                for value in values
            )
            / len(values)
        )


    def save_results(self):

        if self.saved:
            return

        self.saved = True

        print()
        print(
            "=========================================="
        )
        print(
            "HYDRONE TRACKING LOGGER FINALIZING"
        )
        print(
            "=========================================="
        )

        # Stop new odometry callbacks before generating files.
        try:
            self.sub.unregister()
        except Exception:
            pass

        # IMPORTANT:
        # Work only from one immutable snapshot.  Without this,
        # odometry callbacks can append while t/x/y/z lists are
        # being constructed, giving arrays of different lengths.
        samples = list(self.samples)

        print(
            "samples recorded =",
            len(samples)
        )

        if len(samples) < 2:

            print(
                "Not enough samples; "
                "no files generated."
            )

            return

        csv_path = self.save_csv(samples)

        t = [
            row[0]
            for row in samples
        ]

        actual_x = [
            row[1]
            for row in samples
        ]

        desired_x = [
            row[2]
            for row in samples
        ]

        error_x = [
            row[3]
            for row in samples
        ]

        actual_y = [
            row[4]
            for row in samples
        ]

        desired_y = [
            row[5]
            for row in samples
        ]

        error_y = [
            row[6]
            for row in samples
        ]

        actual_z = [
            row[7]
            for row in samples
        ]

        desired_z = [
            row[8]
            for row in samples
        ]

        error_z = [
            row[9]
            for row in samples
        ]

        error_3d = [
            row[10]
            for row in samples
        ]

        x_plot = self.plot_axis(
            t,
            actual_x,
            desired_x,
            "x"
        )

        y_plot = self.plot_axis(
            t,
            actual_y,
            desired_y,
            "y"
        )

        z_plot = self.plot_axis(
            t,
            actual_z,
            desired_z,
            "z"
        )

        print(
            "CSV =",
            csv_path
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

        print()
        print(
            "RMSE X = %.6f m"
            % self.rmse(error_x)
        )

        print(
            "RMSE Y = %.6f m"
            % self.rmse(error_y)
        )

        print(
            "RMSE Z = %.6f m"
            % self.rmse(error_z)
        )

        print(
            "MAX |X error| = %.6f m"
            % max(
                abs(v)
                for v in error_x
            )
        )

        print(
            "MAX |Y error| = %.6f m"
            % max(
                abs(v)
                for v in error_y
            )
        )

        print(
            "MAX |Z error| = %.6f m"
            % max(
                abs(v)
                for v in error_z
            )
        )

        print(
            "Final 3D error = %.6f m"
            % error_3d[-1]
        )

        print(
            "Minimum 3D error = %.6f m"
            % min(error_3d)
        )

        print(
            "=========================================="
        )


if __name__ == "__main__":

    rospy.init_node(
        "hydrone_tracking_logger"
    )

    logger = TrackingLogger()

    rospy.spin()
