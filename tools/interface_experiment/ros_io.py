"""ROS/Gazebo adapter. Imported only by real simulation workers."""
import copy
import math
import threading
import time

import numpy as np
import rospy
from gazebo_msgs.msg import ModelState
from gazebo_msgs.srv import SetModelState
from geometry_msgs.msg import Transform, Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, Imu, LaserScan
from std_srvs.srv import Empty
from trajectory_msgs.msg import MultiDOFJointTrajectory, MultiDOFJointTrajectoryPoint
from uuv_gazebo_ros_plugins_msgs.srv import GetFloat, SetFloat

from core import action_clip, decode_rgb, rotate, rpy, teacher, wrap


def image_rgb(msg):
    return decode_rgb(msg.height, msg.width, msg.step, msg.encoding, msg.data)


class Gazebo:
    def __init__(self, config):
        self.c = config
        self.ns = "/" + config["namespace"]
        self.lock = threading.RLock()
        self.odom = self.imu = self.scan = self.image = None
        self.camera_error = None
        self.rgb = None
        self.counts = dict(odom=0, imu=0, scan=0, image=0, trajectory=0, watchdog=0)
        self.cmd = np.zeros(3, dtype=np.float32)
        self.cmd_wall = time.monotonic()
        self.yaw_ref = None
        self.last_bridge_time = None
        self.last_damping = None
        self.bridge_error = None
        self.last_published = None
        self.stop_event = threading.Event()
        rospy.init_node("hydrone_interface_pilot", anonymous=False, disable_signals=True)
        self.pub = rospy.Publisher(self.ns+"/command/trajectory", MultiDOFJointTrajectory,
                                   queue_size=1)
        rospy.Subscriber(self.ns+"/ground_truth/odometry", Odometry,
                         lambda m: self._store("odom", m), queue_size=1)
        rospy.Subscriber(self.ns+"/imu", Imu, lambda m: self._store("imu", m), queue_size=1)
        rospy.Subscriber(self.ns+"/scan", LaserScan, lambda m: self._store("scan", m), queue_size=1)
        rospy.Subscriber(self.ns+"/interface_camera/image_raw", Image, self._image, queue_size=1)
        self.bridge = threading.Thread(target=self._bridge_loop, daemon=True)
        self.bridge.start()
        self.reset_world = self._service("/gazebo/reset_world", Empty)
        self.pause = self._service("/gazebo/pause_physics", Empty)
        self.unpause = self._service("/gazebo/unpause_physics", Empty)
        self.set_state = self._service("/gazebo/set_model_state", SetModelState)
        self.set_damping = self._service(self.ns+"/set_damping_scaling", SetFloat)
        self.get_damping = self._service(self.ns+"/get_damping_scaling", GetFloat)
        self.wait_ready(90)
        self.damping(config["nominal_damping"])

    def _service(self, name, typ):
        rospy.wait_for_service(name, timeout=90)
        return rospy.ServiceProxy(name, typ)

    def _store(self, name, msg):
        with self.lock:
            setattr(self, name, msg)
            self.counts[name] += 1

    def _image(self, msg):
        try:
            rgb = image_rgb(msg)
            with self.lock:
                self.image, self.rgb = msg, rgb
                self.counts["image"] += 1
        except Exception as e:
            self.camera_error = str(e)

    def wait_ready(self, seconds):
        end = time.monotonic()+seconds
        while time.monotonic() < end and not rospy.is_shutdown():
            with self.lock:
                ready = all(x is not None for x in (self.odom, self.imu, self.scan, self.image))
            if self.camera_error:
                raise RuntimeError(self.camera_error)
            if ready and self.pub.get_num_connections() > 0:
                self.snapshot()
                return
            time.sleep(0.05)
        raise RuntimeError("Missing/frozen odom, IMU, scan, RGB or Lee subscriber: " + str(self.counts))

    def command(self, action):
        with self.lock:
            self.cmd = action_clip(action)
            self.cmd_wall = time.monotonic()
        return float(rospy.Time.now().to_sec())

    def _bridge_loop(self):
        """50 Hz wall loop, publish only as simulation time advances.

        TTL uses wall time so stale policy commands are not held indefinitely.
        Legacy mode preserves yaw+command rotation and current-pose reference.
        All methods in one experiment use the same selected bridge.
        """
        while not self.stop_event.wait(0.01):
            if rospy.is_shutdown():
                return
            with self.lock:
                odom = self.odom
                cmd = self.cmd.copy()
                age = time.monotonic()-self.cmd_wall
            if odom is None:
                continue
            now = rospy.Time.now().to_sec()
            if self.last_bridge_time is not None and 0 <= now-self.last_bridge_time < 0.019:
                continue
            dt = 0.02 if self.last_bridge_time is None else float(np.clip(now-self.last_bridge_time, 0, 0.05))
            self.last_bridge_time = now
            if age > 1.0:
                cmd[:] = 0
                with self.lock:
                    self.counts["watchdog"] += 1
            q = odom.pose.pose.orientation
            try:
                yaw = rpy([q.x, q.y, q.z, q.w])[2]
            except Exception as exc:
                self.bridge_error = repr(exc)
                return
            transform = Transform()
            transform.translation.x = odom.pose.pose.position.x
            transform.translation.y = odom.pose.pose.position.y
            transform.translation.z = odom.pose.pose.position.z
            if self.c["bridge"] == "legacy":
                transform.rotation = q
                angle = yaw + cmd[2]
            else:
                if self.yaw_ref is None:
                    self.yaw_ref = yaw
                self.yaw_ref = wrap(self.yaw_ref+float(cmd[2])*dt)
                transform.rotation.z = math.sin(self.yaw_ref/2)
                transform.rotation.w = math.cos(self.yaw_ref/2)
                angle = yaw
            velocity = Twist()
            velocity.linear.x = float(cmd[0])*math.cos(angle)
            velocity.linear.y = float(cmd[0])*math.sin(angle)
            velocity.linear.z = float(cmd[1])
            velocity.angular.z = float(cmd[2])
            point = MultiDOFJointTrajectoryPoint()
            point.transforms = [transform]
            point.velocities = [velocity]
            msg = MultiDOFJointTrajectory()
            msg.header.stamp = rospy.Time.from_sec(now)
            msg.joint_names = ["base_link"]
            msg.points = [point]
            self.pub.publish(msg)
            with self.lock:
                self.counts["trajectory"] += 1
                self.last_published = dict(simulation_time=now, reference=cmd.tolist(),
                                           watchdog=age > 1.0)

    def snapshot(self):
        if self.bridge_error:
            raise RuntimeError("Bridge failed: "+self.bridge_error)
        if not self.bridge.is_alive():
            raise RuntimeError("Bridge thread is not alive")
        with self.lock:
            o, im, scan, image = self.odom, self.imu, self.scan, self.image
            rgb = None if self.rgb is None else self.rgb.copy()
            counts = self.counts.copy()
            bridge = copy.deepcopy(self.last_published)
        if any(x is None for x in (o, im, scan, image)):
            raise RuntimeError("Sensors not ready")
        now = rospy.Time.now().to_sec()
        ages = dict(odom=now-o.header.stamp.to_sec(), imu=now-im.header.stamp.to_sec(),
                    scan=now-scan.header.stamp.to_sec(), image=now-image.header.stamp.to_sec())
        if any(v < -0.05 for v in ages.values()) or ages["odom"] > 0.5 or ages["imu"] > 0.5:
            raise RuntimeError("Stale/invalid motion feedback: " + str(ages))
        if ages["image"] > 1 or ages["scan"] > 1:
            raise RuntimeError("Stale camera/scan: " + str(ages))
        q = o.pose.pose.orientation
        qv = [q.x, q.y, q.z, q.w]
        p, v = o.pose.pose.position, o.twist.twist.linear
        # nav_msgs/Odometry twist is in child_frame_id. RotorS ground truth
        # publishes child-frame velocity (verified in plugin source).
        velocity = rotate(qv, [v.x, v.y, v.z])
        ranges = np.asarray(scan.ranges, dtype=float)
        if np.isnan(ranges).any():
            raise RuntimeError("NaN LaserScan; do not relabel as a physical collision")
        valid = ranges[np.isfinite(ranges)]
        minimum = float(valid.min()) if len(valid) else float(scan.range_max)
        state = dict(time=float(now), position=[p.x, p.y, p.z], rpy=rpy(qv).tolist(),
                     velocity=velocity.tolist(),
                     gyro=[im.angular_velocity.x, im.angular_velocity.y, im.angular_velocity.z],
                     accel=[im.linear_acceleration.x, im.linear_acceleration.y, im.linear_acceleration.z],
                     min_scan=minimum, ages=ages, counts=counts, bridge=bridge)
        return state, rgb

    def advance(self, start, dt):
        deadline = time.monotonic()+self.c["wall_timeout_seconds"]
        while not rospy.is_shutdown() and time.monotonic() < deadline:
            now = rospy.Time.now().to_sec()
            if now < start-0.01:
                raise RuntimeError("Unexpected backwards simulation clock")
            if now >= start+dt:
                return self.snapshot()
            time.sleep(0.003)
        raise TimeoutError("Simulation clock did not advance")

    def damping(self, value):
        if self.last_damping is not None and abs(self.last_damping-value) < 1e-9:
            return
        response = self.set_damping(float(value))
        if not response.success:
            raise RuntimeError("UUV damping service failed: " + response.message)
        measured = self.get_damping().data
        if abs(measured-value) > 1e-6:
            raise RuntimeError("Damping readback mismatch")
        self.last_damping = float(measured)

    def reset(self, spec):
        self.command([0, 0, 0])
        self.pause()
        try:
            self.reset_world()
            self.last_damping = None
            self.damping(self.c["nominal_damping"])
            msg = ModelState()
            msg.model_name = self.c["namespace"]
            msg.reference_frame = "world"
            msg.pose.position.x, msg.pose.position.y, msg.pose.position.z = spec["start"]
            msg.pose.orientation.z = math.sin(spec["yaw"]/2)
            msg.pose.orientation.w = math.cos(spec["yaw"]/2)
            response = self.set_state(msg)
            if not response.success:
                raise RuntimeError(response.status_message)
            self.yaw_ref = spec["yaw"]
            self.last_bridge_time = None
        finally:
            self.unpause()
        # Wait for post-reset sensor messages, then settle with the same oracle
        # in all methods. The initial state is recorded, not assumed identical.
        with self.lock:
            before = self.counts.copy()
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            with self.lock:
                fresh = all(self.counts[k] > before[k] for k in ("odom", "imu", "scan", "image"))
            if fresh:
                break
            time.sleep(0.02)
        else:
            raise RuntimeError("Post-reset sensors did not refresh")
        state, image = self.snapshot()
        start = state["time"]
        while state["time"]-start < self.c["settle_seconds"]:
            self.command(teacher(state, spec["start"]))
            state, image = self.advance(state["time"], self.c["dt"])
        position_error = float(np.linalg.norm(np.asarray(state["position"])-spec["start"]))
        if position_error > 0.35 or np.max(np.abs(state["rpy"][:2])) > 0.2:
            raise RuntimeError("Reset/settling failed: position error %.3f" % position_error)
        self.command([0, 0, 0])
        return state, image

    def close(self):
        self.command([0, 0, 0])
        try:
            self.damping(self.c["nominal_damping"])
        finally:
            self.stop_event.set()
            self.bridge.join(timeout=2)
