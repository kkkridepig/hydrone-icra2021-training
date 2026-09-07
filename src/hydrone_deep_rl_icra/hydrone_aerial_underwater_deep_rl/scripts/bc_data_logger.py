#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import rospy
import csv
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool


class BCDataLogger:
    def __init__(self):
        self.goal = [
            rospy.get_param("~goal_x", 10.0),
            rospy.get_param("~goal_y", 10.0),
            rospy.get_param("~goal_z", -1.0),
        ]

        self.path = rospy.get_param(
            "~save_path",
            "/tmp/hydrone_bc_dataset.csv"
        )

        self.state = None
        self.action = None
        self.submerged = 0

        self.f = open(self.path, "w", newline="")
        self.writer = csv.writer(self.f)

        self.writer.writerow([
            "x","y","z",
            "vx","vy","vz",
            "gx","gy","gz",
            "submerged",
            "cmd_vx","cmd_vy","cmd_vz","cmd_yaw"
        ])

        ns = rospy.get_param(
            "~namespace",
            "/hydrone_aerial_underwater"
        )

        rospy.Subscriber(
            ns+"/odometry_sensor1/odometry",
            Odometry,
            self.odom_cb,
            queue_size=10
        )

        rospy.Subscriber(
            ns+"/cmd_vel",
            Twist,
            self.cmd_cb,
            queue_size=10
        )

        rospy.Subscriber(
            ns+"/is_submerged",
            Bool,
            self.sub_cb,
            queue_size=10
        )

        self.timer = rospy.Timer(
            rospy.Duration(0.05),
            self.save
        )

        rospy.on_shutdown(self.close)

    def odom_cb(self,msg):
        p = msg.pose.pose.position
        v = msg.twist.twist.linear

        self.state = [
            p.x,p.y,p.z,
            v.x,v.y,v.z
        ]

    def cmd_cb(self,msg):
        self.action = [
            msg.linear.x,
            msg.linear.y,
            msg.linear.z,
            msg.angular.z
        ]

    def sub_cb(self,msg):
        self.submerged = int(msg.data)

    def save(self,event):
        if self.state is None or self.action is None:
            return

        self.writer.writerow(
            self.state +
            self.goal +
            [self.submerged] +
            self.action
        )
        self.f.flush()

    def close(self):
        self.f.close()


if __name__=="__main__":
    rospy.init_node("bc_data_logger")
    BCDataLogger()
    rospy.spin()
