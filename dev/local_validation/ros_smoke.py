#!/usr/bin/env python3
"""Bounded, isolated ROS/Gazebo empty-world smoke; never starts project training."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import xmlrpc.client


class ShortTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = 1.0
        return connection


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def is_descendant(pid, ancestor):
    # roslaunch starts nodes with setsid(), so process-group equality would
    # reject our own ROS master. Verify its actual Linux parent chain instead.
    visited = set()
    while pid > 1 and pid not in visited:
        if pid == ancestor:
            return True
        visited.add(pid)
        try:
            fields = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def stop_processes(processes):
    # Every child starts its own session. Never use pkill/killall or inspect and
    # terminate unrelated ROS/Gazebo processes on the machine.
    for process in reversed(processes):
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3.0
    for process in reversed(processes):
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for process in reversed(processes):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=50, choices=range(10, 56),
                        metavar="10..55", help="wall-time limit, excluding up to 5s cleanup")
    args = parser.parse_args()
    if args.output_dir is None:
        output = Path(tempfile.mkdtemp(prefix="hydrone-ros-smoke-"))
    else:
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=False)
    processes = []
    handles = []
    result = {"level": "L2", "scope": "empty-world ROS/Gazebo only",
              "server_only_validated": False, "output_dir": str(output),
              "status": "failed", "timeout_seconds": args.timeout}
    started = time.monotonic()

    def interrupted(signum, frame):
        raise TimeoutError("smoke timed out or was interrupted (signal {})".format(signum))

    signal.signal(signal.SIGALRM, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.setitimer(signal.ITIMER_REAL, args.timeout)
    try:
        ros_port = free_port()
        gazebo_port = free_port()
        while gazebo_port == ros_port:
            gazebo_port = free_port()
        environment = os.environ.copy()
        environment.update({"ROS_MASTER_URI": "http://127.0.0.1:{}".format(ros_port),
                            "GAZEBO_MASTER_URI": "http://127.0.0.1:{}".format(gazebo_port),
                            "ROS_IP": "127.0.0.1", "ROS_HOME": str(output / "ros-home"),
                            "ROS_LOG_DIR": str(output / "ros-logs"),
                            "GAZEBO_MODEL_DATABASE_URI": "", "LIBGL_ALWAYS_SOFTWARE": "1",
                            "HOME": str(output / "home")})
        Path(environment["HOME"]).mkdir()
        environment.pop("ROS_HOSTNAME", None)
        environment.pop("ROS_NAMESPACE", None)
        os.environ.update(environment)
        os.environ.pop("ROS_HOSTNAME", None)
        os.environ.pop("ROS_NAMESPACE", None)
        result["ros_master_uri"] = environment["ROS_MASTER_URI"]
        result["gazebo_master_uri"] = environment["GAZEBO_MASTER_URI"]
        world = output / "empty.world"
        world.write_text("<?xml version='1.0'?>\n<sdf version='1.6'><world name='default'>"
                         "<physics type='ode'><max_step_size>0.001</max_step_size>"
                         "<real_time_update_rate>1000</real_time_update_rate></physics>"
                         "</world></sdf>\n", encoding="utf-8")

        def start(name, command):
            handle = (output / (name + ".log")).open("wb")
            handles.append(handle)
            process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                                       env=environment, start_new_session=True)
            processes.append(process)
            return process

        roscore = start("roscore", ["roscore", "-p", str(ros_port)])
        master = xmlrpc.client.ServerProxy(environment["ROS_MASTER_URI"],
                                          transport=ShortTransport())
        while True:
            if roscore.poll() is not None:
                raise RuntimeError("roscore exited; inspect roscore.log")
            try:
                master_pid = master.getPid("/hydrone_local_smoke")
                if master_pid[0] == 1:
                    if not is_descendant(master_pid[2], roscore.pid):
                        raise RuntimeError("selected ROS port belongs to an unrelated master")
                    break
            except (OSError, xmlrpc.client.Error):
                pass
            time.sleep(0.1)
        master.setParam("/hydrone_local_smoke", "/use_sim_time", True)
        gazebo = start("gazebo", ["xvfb-run", "-a", "-s", "-screen 0 640x480x24",
                                   "gzserver", "--verbose", str(world),
                                   "-s", "libgazebo_ros_api_plugin.so"])
        import rospy
        from gazebo_msgs.srv import GetWorldProperties
        from rosgraph_msgs.msg import Clock

        rospy.init_node("hydrone_local_smoke", anonymous=True, disable_signals=True)
        samples = []
        subscriber = rospy.Subscriber("/clock", Clock,
                                      lambda message: samples.append(message.clock.to_sec()),
                                      queue_size=100)
        while True:
            if roscore.poll() is not None or gazebo.poll() is not None:
                raise RuntimeError("ROS/Gazebo child exited; inspect logs")
            positive = [value for value in samples if value > 0]
            services = master.getSystemState("/hydrone_local_smoke")[2][2]
            if (len(positive) >= 3 and positive[-1] > positive[0]
                    and any(name == "/gazebo/get_world_properties" for name, _ in services)):
                break
            time.sleep(0.05)
        response = rospy.ServiceProxy("/gazebo/get_world_properties", GetWorldProperties)()
        if not response.success or response.sim_time <= 0:
            raise RuntimeError("world service did not report a running world")
        if any(b < a for a, b in zip(samples, samples[1:])):
            raise RuntimeError("simulation clock moved backwards")
        subscriber.unregister()
        result.update({"status": "passed", "clock_samples": len(samples),
                       "first_positive_clock": positive[0], "last_clock": positive[-1],
                       "world_service_success": response.success,
                       "world_sim_time": response.sim_time,
                       "world_models": list(response.model_names)})
        rospy.signal_shutdown("bounded local smoke completed")
    except Exception as error:
        result["error"] = "{}: {}".format(type(error).__name__, error)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        stop_processes(processes)
        for handle in handles:
            handle.close()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
