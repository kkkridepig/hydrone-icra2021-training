#!/usr/bin/env python3

import rospy
import torch

from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool

from bc_policy import BCPolicy


class BCNavigation:

    def __init__(self):

        self.goal=[
            rospy.get_param("~goal_x",10),
            rospy.get_param("~goal_y",10),
            rospy.get_param("~goal_z",-1)
        ]

        self.state=None
        self.submerged=0

        self.model=BCPolicy()

        weight=rospy.get_param(
            "~model",
            "bc_policy.pth"
        )

        self.model.load_state_dict(
            torch.load(weight)
        )

        self.model.eval()


        ns=rospy.get_param(
            "~namespace",
            "/hydrone_aerial_underwater"
        )

        self.pub=rospy.Publisher(
            ns+"/cmd_vel",
            Twist,
            queue_size=5
        )


        rospy.Subscriber(
            ns+"/odometry_sensor1/odometry",
            Odometry,
            self.odom_cb
        )


        rospy.Subscriber(
            ns+"/is_submerged",
            Bool,
            self.sub_cb
        )


        self.timer=rospy.Timer(
            rospy.Duration(0.05),
            self.control
        )


    def odom_cb(self,msg):

        p=msg.pose.pose.position
        v=msg.twist.twist.linear

        self.state=[
            p.x,p.y,p.z,
            v.x,v.y,v.z
        ]


    def sub_cb(self,msg):
        self.submerged=int(msg.data)


    def control(self,event):

        if self.state is None:
            return

        x=torch.tensor(
            self.state+
            self.goal+
            [self.submerged],
            dtype=torch.float32
        )

        with torch.no_grad():
            a=self.model(x).numpy()


        cmd=Twist()

        cmd.linear.x=float(a[0])
        cmd.linear.y=float(a[1])
        cmd.linear.z=float(a[2])
        cmd.angular.z=float(a[3])

        self.pub.publish(cmd)


if __name__=="__main__":
    rospy.init_node("bc_navigation_node")
    BCNavigation()
    rospy.spin()
