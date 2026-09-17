"""Reproduce the startup race without requiring a ROS installation."""
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import run as runner


def load_adapter():
    modules = {"rospy": ModuleType("rospy")}
    exports = {
        "gazebo_msgs.msg": ["ModelState"],
        "gazebo_msgs.srv": ["SetModelState"],
        "geometry_msgs.msg": ["Transform", "Twist"],
        "nav_msgs.msg": ["Odometry"],
        "sensor_msgs.msg": ["Image", "Imu", "LaserScan"],
        "std_srvs.srv": ["Empty"],
        "trajectory_msgs.msg": ["MultiDOFJointTrajectory", "MultiDOFJointTrajectoryPoint"],
        "uuv_gazebo_ros_plugins_msgs.srv": ["GetFloat", "SetFloat"],
    }
    for name, symbols in exports.items():
        parent = name.split(".")[0]
        modules.setdefault(parent, ModuleType(parent))
        modules[name] = ModuleType(name)
        for symbol in symbols:
            setattr(modules[name], symbol, type(symbol, (), {}))
    spec = importlib.util.spec_from_file_location("adapter_clock_test", HERE/"ros_io.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class FakeROS:
    """Model Noetic's one-time simtime selection, not a flight simulator."""
    def __init__(self, param_at=.1, clock_delay=.1):
        self.wall = 0.
        self.param_at, self.clock_delay = param_at, clock_delay
        self.initialized_at = None
        self.wallclock = True
        self.rostime = SimpleNamespace(is_wallclock=lambda: self.wallclock)
        self.Time = SimpleNamespace(now=lambda: SimpleNamespace(to_sec=self.now))

    def monotonic(self):
        return self.wall

    def sleep(self, seconds):
        self.wall += seconds

    def is_shutdown(self):
        return False

    def get_param(self, key, default=False):
        assert key == "/use_sim_time"
        return self.wall >= self.param_at

    def init_node(self, *args, **kwargs):
        self.initialized_at = self.wall
        self.wallclock = not self.get_param("/use_sim_time")

    def now(self):
        if self.wallclock:
            return 1789565914.87+self.wall
        return .2 if self.wall-self.initialized_at >= self.clock_delay else 0.


class ClockStartup(unittest.TestCase):
    def setUp(self):
        self.adapter = load_adapter()

    def initialize(self, ros, timeout=1.):
        with patch.object(self.adapter, "rospy", ros), patch.object(self.adapter, "time", ros):
            self.adapter.initialize_sim_time(timeout)

    def ready_env(self):
        env = self.adapter.Gazebo.__new__(self.adapter.Gazebo)
        env.lock = threading.RLock()
        env.odom = env.imu = env.scan = env.image = object()
        env.camera_error = None
        env.pub = SimpleNamespace(get_num_connections=lambda: 1)
        env.counts = {"odom": 1}
        return env

    def test_old_start_order_reproduces_epoch_sized_age(self):
        ros = FakeROS()
        ros.init_node("old_worker")
        ros.sleep(.2)
        self.assertTrue(ros.get_param("/use_sim_time"))
        self.assertGreater(ros.now()-.2, 1e9)
        env = self.adapter.Gazebo.__new__(self.adapter.Gazebo)
        with patch.object(self.adapter, "rospy", ros):
            with self.assertRaisesRegex(RuntimeError, "wall clock"):
                env.snapshot()

    def test_waits_for_parameter_before_init_and_positive_clock_after(self):
        ros = FakeROS()
        self.initialize(ros)
        self.assertGreaterEqual(ros.initialized_at, ros.param_at)
        self.assertFalse(ros.wallclock)
        self.assertEqual(ros.now(), .2)

    def test_master_initially_unreachable_is_retried(self):
        ros = FakeROS(param_at=0)
        ros.get_param = Mock(side_effect=[ConnectionRefusedError(), False, True, True])
        self.initialize(ros)
        self.assertFalse(ros.wallclock)

    def test_missing_parameter_times_out_without_initializing(self):
        ros = FakeROS(param_at=99)
        with self.assertRaisesRegex(RuntimeError, "parameter not ready"):
            self.initialize(ros, .2)
        self.assertIsNone(ros.initialized_at)

    def test_missing_clock_times_out_without_wall_time_fallback(self):
        ros = FakeROS(param_at=0, clock_delay=99)
        with self.assertRaisesRegex(RuntimeError, "No positive /clock"):
            self.initialize(ros, .2)
        self.assertFalse(ros.wallclock)

    def test_wall_clock_after_init_is_rejected(self):
        ros = FakeROS(param_at=0)
        ros.init_node = Mock()  # Leaves the worker using wall time.
        with self.assertRaisesRegex(RuntimeError, "selected wall time"):
            self.initialize(ros)

    def test_startup_retries_only_transient_sensor_age_failures(self):
        env, ros = self.ready_env(), FakeROS()
        env.snapshot = Mock(side_effect=[self.adapter.SensorsNotReady("old image"), None])
        with patch.object(self.adapter, "rospy", ros), patch.object(self.adapter, "time", ros):
            env.wait_ready(1.)
        self.assertEqual(env.snapshot.call_count, 2)

    def test_frozen_sensors_still_fail_with_diagnostic(self):
        env, ros = self.ready_env(), FakeROS()
        env.snapshot = Mock(side_effect=self.adapter.SensorsNotReady("stale forever"))
        with patch.object(self.adapter, "rospy", ros), patch.object(self.adapter, "time", ros):
            with self.assertRaisesRegex(RuntimeError, "stale forever"):
                env.wait_ready(.2)

    def test_real_sensor_or_bridge_errors_are_not_retried(self):
        env, ros = self.ready_env(), FakeROS()
        env.snapshot = Mock(side_effect=RuntimeError("NaN LaserScan"))
        with patch.object(self.adapter, "rospy", ros), patch.object(self.adapter, "time", ros):
            with self.assertRaisesRegex(RuntimeError, "NaN LaserScan"):
                env.wait_ready(1.)
        self.assertEqual(env.snapshot.call_count, 1)


class CollectionCompatibility(unittest.TestCase):
    def setUp(self):
        prefix = "tools/interface_experiment/"
        self.before = {
            prefix+"ros_io.py": "20e22059ebadc2b3e041c82b0d8f079ffe14bcafcbbe755acbd32d560175ad20",
            prefix+"run.py": "35c1a287a9ade18488a6f208eac8e37a2c636a2278aac54c179ad5d77511bd4b",
            prefix+"learning.py": "unchanged_learning",
            "devel/lib/libuuv_underwater_object_ros_plugin.so": "unchanged_binary",
        }
        self.after = dict(self.before)
        for f in ("ros_io.py", "run.py", "tests/test_startup.py"):
            self.after[prefix+f] = runner.sha(HERE/f)

    def test_reviewed_clock_fix_accepts_v1_data_and_records_changes(self):
        result = runner.collection_source_compatibility(self.before, self.after)
        self.assertEqual(result["mode"], "clock_startup_hotfix_v1")
        self.assertEqual(len(result["changes"]), 3)

    def test_identical_source_still_passes(self):
        self.assertEqual(runner.collection_source_compatibility(self.after, self.after)["mode"], "exact")

    def test_learning_physics_and_unreviewed_code_changes_are_rejected(self):
        for path in ("tools/interface_experiment/learning.py",
                     "tools/interface_experiment/ros_io.py",
                     "devel/lib/libuuv_underwater_object_ros_plugin.so",
                     "tools/interface_experiment/unknown.py"):
            with self.subTest(path=path):
                changed = dict(self.after, **{path: "unreviewed"})
                with self.assertRaisesRegex(RuntimeError, "outside the reviewed clock hotfix"):
                    runner.collection_source_compatibility(self.before, changed)

    def test_unknown_collection_version_is_rejected(self):
        old = dict(self.before)
        old["tools/interface_experiment/ros_io.py"] = "other_collection_implementation"
        with self.assertRaises(RuntimeError):
            runner.collection_source_compatibility(old, self.after)


if __name__ == "__main__":
    unittest.main()
