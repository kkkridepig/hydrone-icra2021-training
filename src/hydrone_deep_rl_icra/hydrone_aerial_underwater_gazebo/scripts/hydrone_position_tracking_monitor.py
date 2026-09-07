#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Hydrone position tracking monitor

功能：
1. 记录期望位置
   /hydrone_aerial_underwater/command/pose

2. 记录真实位置
   /hydrone_aerial_underwater/odometry_sensor1/odometry

3. 计算：
   ex = xd - x
   ey = yd - y
   ez = zd - z

4. 实验结束自动生成：
   - CSV
   - 误差统计
   - 三维位置跟踪曲线
   - 二维轨迹图
"""


import rospy
import os
import csv
import time
import math

import numpy as np

from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool



class HydronePositionMonitor:


    def __init__(self):

        rospy.init_node(
            "hydrone_position_tracking_monitor"
        )


        # ===============================
        # 参数
        # ===============================

        self.end_timeout = rospy.get_param(
            "~end_timeout",
            3.0
        )


        self.output_dir = os.path.expanduser(
            "~/hydrone_repro/plots"
        )


        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)



        # ===============================
        # 当前期望位置
        # ===============================

        self.des_x = 0
        self.des_y = 0
        self.des_z = 0


        self.have_command = False


        # ===============================
        # 时间
        # ===============================

        self.start_time = None

        self.last_command_time = None



        # ===============================
        # 数据缓存
        # ===============================


        self.time_data=[]


        self.x_des=[]
        self.y_des=[]
        self.z_des=[]


        self.x_real=[]
        self.y_real=[]
        self.z_real=[]


        self.err_x=[]
        self.err_y=[]
        self.err_z=[]


        self.err_norm=[]



        self.submerged=[]



        # ===============================
        # ROS订阅
        # ===============================


        rospy.Subscriber(
            "/hydrone_aerial_underwater/command/pose",
            PoseStamped,
            self.command_callback
        )



        rospy.Subscriber(
            "/hydrone_aerial_underwater/odometry_sensor1/odometry",
            Odometry,
            self.odom_callback
        )


        rospy.Subscriber(
            "/hydrone_aerial_underwater/is_submerged",
            Bool,
            self.sub_callback
        )


        rospy.loginfo(
            "Hydrone monitor started"
        )


        self.is_submerged=False



    # ==================================
    # 期望位置回调
    # ==================================

    def command_callback(self,msg):


        self.des_x = msg.pose.position.x
        self.des_y = msg.pose.position.y
        self.des_z = msg.pose.position.z


        self.have_command=True


        self.last_command_time=time.time()


        if self.start_time is None:

            self.start_time=rospy.Time.now().to_sec()


            rospy.loginfo(
                "Tracking started"
            )





    # ==================================
    # 水下状态
    # ==================================

    def sub_callback(self,msg):

        self.is_submerged=msg.data





    # ==================================
    # 实际位置
    # ==================================

    def odom_callback(self,msg):


        if not self.have_command:
            return



        t = rospy.Time.now().to_sec()-self.start_time


        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        z = msg.pose.pose.position.z



        ex=self.des_x-x
        ey=self.des_y-y
        ez=self.des_z-z


        en=math.sqrt(
            ex**2+
            ey**2+
            ez**2
        )



        self.time_data.append(t)


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


        self.submerged.append(
            int(self.is_submerged)
        )



        rospy.loginfo_throttle(
            1,
            """
X:
desired %.2f actual %.2f error %.3f

Y:
desired %.2f actual %.2f error %.3f

Z:
desired %.2f actual %.2f error %.3f

3D error %.3f m
""",
            self.des_x,
            x,
            ex,

            self.des_y,
            y,
            ey,

            self.des_z,
            z,
            ez,

            en
        )
    # ==================================
    # 保存CSV
    # ==================================

    def save_csv(self,folder):


        filename=os.path.join(
            folder,
            "tracking_data.csv"
        )


        with open(
            filename,
            "w",
            newline=""
        ) as f:


            writer=csv.writer(f)


            writer.writerow(
                [
                    "time_s",

                    "x_des",
                    "x_real",
                    "x_error",

                    "y_des",
                    "y_real",
                    "y_error",

                    "z_des",
                    "z_real",
                    "z_error",

                    "error_3d",

                    "submerged"
                ]
            )


            for i in range(len(self.time_data)):


                writer.writerow(
                    [

                    self.time_data[i],


                    self.x_des[i],
                    self.x_real[i],
                    self.err_x[i],


                    self.y_des[i],
                    self.y_real[i],
                    self.err_y[i],


                    self.z_des[i],
                    self.z_real[i],
                    self.err_z[i],


                    self.err_norm[i],


                    self.submerged[i]

                    ]
                )


        rospy.loginfo(
            "CSV saved: %s",
            filename
        )






    # ==================================
    # 误差统计
    # ==================================

    def save_statistics(self,folder):


        filename=os.path.join(
            folder,
            "tracking_summary.txt"
        )


        def calc(arr):

            arr=np.array(arr)

            return (

                np.sqrt(
                    np.mean(arr**2)
                ),

                np.mean(
                    np.abs(arr)
                ),

                np.max(
                    np.abs(arr)
                )

            )



        ex_rmse,ex_mae,ex_max=calc(
            self.err_x
        )


        ey_rmse,ey_mae,ey_max=calc(
            self.err_y
        )


        ez_rmse,ez_mae,ez_max=calc(
            self.err_z
        )


        e3_rmse,e3_mae,e3_max=calc(
            self.err_norm
        )



        text=f"""

========== Hydrone Tracking Result ==========


Duration:
{self.time_data[-1]:.2f} s


X error:

RMSE:
{ex_rmse:.5f} m

MAE:
{ex_mae:.5f} m

MAX:
{ex_max:.5f} m



Y error:

RMSE:
{ey_rmse:.5f} m

MAE:
{ey_mae:.5f} m

MAX:
{ey_max:.5f} m



Z error:

RMSE:
{ez_rmse:.5f} m

MAE:
{ez_mae:.5f} m

MAX:
{ez_max:.5f} m



3D position error:


RMSE:
{e3_rmse:.5f} m

MAE:
{e3_mae:.5f} m

MAX:
{e3_max:.5f} m



==============================================

"""


        with open(
            filename,
            "w"
        ) as f:

            f.write(text)


        print(text)



    # ==================================
    # 绘制位置跟踪
    # ==================================

    def plot_position(self,folder):


        import matplotlib.pyplot as plt


        plt.figure(
            figsize=(10,8)
        )


        plt.subplot(3,1,1)

        plt.plot(
            self.time_data,
            self.x_des,
            label="desired"
        )

        plt.plot(
            self.time_data,
            self.x_real,
            label="real"
        )

        plt.ylabel("X (m)")
        plt.legend()



        plt.subplot(3,1,2)

        plt.plot(
            self.time_data,
            self.y_des,
            label="desired"
        )

        plt.plot(
            self.time_data,
            self.y_real,
            label="real"
        )

        plt.ylabel("Y (m)")
        plt.legend()



        plt.subplot(3,1,3)


        plt.plot(
            self.time_data,
            self.z_des,
            label="desired"
        )


        plt.plot(
            self.time_data,
            self.z_real,
            label="real"
        )


        plt.ylabel("Z (m)")
        plt.xlabel("time (s)")

        plt.legend()



        plt.tight_layout()


        filename=os.path.join(
            folder,
            "position_tracking_xyz.png"
        )


        plt.savefig(
            filename,
            dpi=300
        )


        plt.close()






    # ==================================
    # 绘制误差
    # ==================================

    def plot_error(self,folder):


        import matplotlib.pyplot as plt



        plt.figure(
            figsize=(10,6)
        )


        plt.plot(
            self.time_data,
            self.err_x,
            label="error X"
        )


        plt.plot(
            self.time_data,
            self.err_y,
            label="error Y"
        )


        plt.plot(
            self.time_data,
            self.err_z,
            label="error Z"
        )


        plt.plot(
            self.time_data,
            self.err_norm,
            label="3D error"
        )


        plt.xlabel(
            "time (s)"
        )


        plt.ylabel(
            "error (m)"
        )


        plt.legend()


        plt.grid()


        filename=os.path.join(
            folder,
            "position_error_xyz.png"
        )


        plt.savefig(
            filename,
            dpi=300
        )


        plt.close()





    # ==================================
    # 二维轨迹
    # ==================================

    def plot_trajectory(self,folder):


        import matplotlib.pyplot as plt



        plt.figure(
            figsize=(12,4)
        )


        # XY

        plt.subplot(1,3,1)

        plt.plot(
            self.x_des,
            self.y_des,
            label="desired"
        )

        plt.plot(
            self.x_real,
            self.y_real,
            label="real"
        )

        plt.xlabel("X")
        plt.ylabel("Y")

        plt.title("XY")

        plt.legend()



        # XZ

        plt.subplot(1,3,2)


        plt.plot(
            self.x_des,
            self.z_des,
            label="desired"
        )


        plt.plot(
            self.x_real,
            self.z_real,
            label="real"
        )


        plt.xlabel("X")
        plt.ylabel("Z")

        plt.title("XZ")


        plt.legend()



        # YZ

        plt.subplot(1,3,3)


        plt.plot(
            self.y_des,
            self.z_des,
            label="desired"
        )


        plt.plot(
            self.y_real,
            self.z_real,
            label="real"
        )


        plt.xlabel("Y")
        plt.ylabel("Z")

        plt.title("YZ")


        plt.legend()



        plt.tight_layout()



        filename=os.path.join(
            folder,
            "trajectory_2d.png"
        )


        plt.savefig(
            filename,
            dpi=300
        )


        plt.close()




    # ==================================
    # 结束检测
    # ==================================

    def check_finished(self):


        if not self.have_command:

            return False



        if self.last_command_time is None:

            return False



        if (
            time.time()
            -
            self.last_command_time
            >
            self.end_timeout
        ):

            return True



        return False





    # ==================================
    # 主循环
    # ==================================

    def run(self):


        rate=rospy.Rate(20)


        while not rospy.is_shutdown():


            if self.check_finished():


                rospy.loginfo(
                    "Demo finished, generating results..."
                )


                folder=os.path.join(
                    self.output_dir,
                    "tracking_"+time.strftime(
                        "%Y%m%d_%H%M%S"
                    )
                )


                os.makedirs(folder)



                self.save_csv(folder)


                self.save_statistics(folder)


                self.plot_position(folder)


                self.plot_error(folder)


                self.plot_trajectory(folder)



                rospy.loginfo(
                    "All results saved in %s",
                    folder
                )


                break



            rate.sleep()





if __name__=="__main__":


    try:

        node=HydronePositionMonitor()

        node.run()


    except rospy.ROSInterruptException:

        pass


