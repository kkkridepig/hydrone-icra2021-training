"""Offline correctness tests. Fixtures are never experimental evidence."""
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from core import (ACTION_SCALE, ChunkExecutor, HeightFilter, action_clip, decision_probs, decode_rgb,
                  expert_features, history_at, history_row, interventions, load_config,
                  resize_rgb, rotate, scenario, sense, success_sample, terminal_reason)
from report import collection_gate, make_report, paired


class Contracts(unittest.TestCase):
    def setUp(self):
        self.c = load_config(HERE/"config.json")

    def state(self):
        return dict(position=[0, 0, 0.7], rpy=[0, 0, 0], velocity=[0, 0, 0],
                    gyro=[0, 0, 0], accel=[0, 0, 9.8], min_scan=5)

    def test_scenario_split_overlap_is_rejected(self):
        c = copy.deepcopy(self.c)
        c["test_seeds"] = [0]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"c.json"
            path.write_text(json.dumps(c))
            with self.assertRaises(ValueError):
                load_config(path)

    def test_absolute_height_and_injection_do_not_enter_sensor(self):
        a, b = self.state(), self.state()
        b["position"][2] = -0.8
        b["damping"] = 100
        b["condition"] = "both"
        x = sense(a, np.random.RandomState(42), self.c)
        y = sense(b, np.random.RandomState(42), self.c)
        np.testing.assert_array_equal(x, y)
        self.assertEqual(history_row(x, np.zeros(3), .2).shape, (19,))
        self.assertEqual(expert_features(x, .4, [1, 0, -.6], np.zeros(3), .1).shape, (21,))

    def test_chunk_consumes_distinct_rows_and_expires(self):
        ex = ChunkExecutor("long", self.c)
        chunk = np.array([[.01*i, .02*i, -.01*i] for i in range(4)])
        ex.replace(chunk)
        for i in range(4):
            self.assertFalse(ex.needs_plan(1, 1, 0, -.2))
            np.testing.assert_allclose(ex.next(), chunk[i], atol=1e-7)
        self.assertTrue(ex.needs_plan(0, 0, 1, 0))
        with self.assertRaises(RuntimeError):
            ex.next()

    def test_visual_only_alarm_does_not_force_separated_replan(self):
        chunk = np.zeros((self.c["horizon"], 3))
        a, b = ChunkExecutor("separated", self.c), ChunkExecutor("unified", self.c)
        a.replace(chunk)
        b.replace(chunk)
        self.assertFalse(a.needs_plan(.9, .1, .8, .1))
        self.assertTrue(b.needs_plan(.9, .1, .8, .1))
        self.assertTrue(a.needs_plan(.1, .9, .8, .1))

    def test_union_ablation_has_same_threshold_and_geometry_guard(self):
        self.assertEqual(decision_probs(.1, .8, "unified"), (.8, .8))
        self.assertEqual(decision_probs(.1, .8, "separated"), (.1, .8))
        for method in ("separated", "unified"):
            e = ChunkExecutor(method, self.c)
            e.replace(np.zeros((self.c["horizon"], 3)))
            self.assertTrue(e.needs_plan(0, 0, .1, -.1))

    def test_history_never_reads_future_rows(self):
        rows = np.ones((10, 19), dtype=np.float32)
        first = history_at(rows, 3, 8)
        rows[4:] = 900
        np.testing.assert_array_equal(first, history_at(rows, 3, 8))
        self.assertTrue(np.all(first[:4] == 0))

    def test_factorial_interventions_are_independent(self):
        spec = scenario(0, "air_to_water", self.c)
        spec.update(perturb_center=0., visual_shift=0.)
        self.assertEqual(interventions(0, spec, "clean", self.c), (False, False, 1.0))
        vis = interventions(0, spec, "visual", self.c)
        dyn = interventions(0, spec, "dynamics", self.c)
        self.assertEqual(vis, (True, False, 1.0))
        self.assertEqual(dyn[:2], (False, True))
        self.assertGreater(dyn[2], 1)
        self.assertEqual(interventions(2, spec, "both", self.c), (False, False, 1.0))

    def test_scenarios_force_two_directions(self):
        for seed in range(12):
            a = scenario(seed, "air_to_water", self.c)
            b = scenario(seed, "water_to_air", self.c)
            self.assertGreater(a["start"][2], 0)
            self.assertLess(a["goal"][2], 0)
            self.assertLess(b["start"][2], 0)
            self.assertGreater(b["goal"][2], 0)

    def test_physical_zero_is_stop_and_invalid_actions_fail(self):
        np.testing.assert_array_equal(action_clip([0, 0, 0]), [0, 0, 0])
        np.testing.assert_allclose(action_clip([1, -1, 1]), [.25, -.25, .25])
        with self.assertRaises(ValueError):
            action_clip([np.nan, 0, 0])

    def test_body_velocity_rotates_into_world(self):
        np.testing.assert_allclose(rotate([0, 0, math.sin(math.pi/4), math.cos(math.pi/4)],
                                         [1, 0, 0]), [0, 1, 0], atol=1e-7)

    def test_filter_does_not_take_a_hidden_true_height(self):
        f = HeightFilter(.5)
        self.assertEqual(f.update(.7, 0, .2, .9, "separated"), .7)
        actual = f.update(-.5, -.1, .2, 1., "separated")
        self.assertAlmostEqual(actual, .68)

    def test_failure_reasons_are_not_collapsed_into_collision(self):
        s = self.state()
        s["position"][2] = 3
        self.assertEqual(terminal_reason(s, [0, 0, 3], 1, self.c), "height_boundary")
        s = self.state()
        s["min_scan"] = .1
        self.assertEqual(terminal_reason(s, [0, 0, 0], 1, self.c), "laser_proximity")

    def test_goal_requires_low_velocity(self):
        s = self.state()
        self.assertTrue(success_sample(s, [0, 0, .7], self.c))
        s["velocity"] = [0, 0, 1]
        self.assertFalse(success_sample(s, [0, 0, .7], self.c))

    def test_empty_report_cannot_pass(self):
        with tempfile.TemporaryDirectory() as d:
            gate = collection_gate(d, self.c)
            self.assertFalse(gate["passed"])
            r = make_report(d, self.c, "phase1", "ROS not installed in test")
            self.assertEqual(r["diagnosis"], "INCOMPLETE")
            self.assertIsNone(r["hypothesis_supported"])
            self.assertTrue((Path(d)/"REPORT.md").exists())

    def test_pairing_does_not_mix_directions_or_model_seeds(self):
        a = dict(method="separated", model_seed=0, scenario=dict(seed=100, direction="air_to_water"),
                 condition="both", reason="success")
        b = dict(a, method="unified", reason="timeout")
        wrong = dict(b, model_seed=1, reason="success")
        result = paired([(a, []), (b, []), (wrong, [])])
        self.assertEqual(result[0]["success_difference"], 1)

    def test_rgb_resize_keeps_channel_order(self):
        a = np.zeros((96, 96, 3), dtype=np.uint8)
        a[:, :, 0] = 170
        out = resize_rgb(a, 48)
        self.assertEqual(out.shape, (48, 48, 3))
        self.assertEqual(int(out[0, 0, 0]), 170)
        self.assertEqual(int(out[0, 0, 2]), 0)

    def test_padded_camera_rows_and_bgr_decode(self):
        data = bytes([1, 2, 3, 90, 90, 90, 4, 5, 6, 90, 90, 90])
        out = decode_rgb(2, 1, 6, "bgr8", data)
        np.testing.assert_array_equal(out[:, 0], [[3, 2, 1], [6, 5, 4]])
        with self.assertRaises(ValueError):
            decode_rgb(2, 1, 6, "bgr8", data[:9])

    def test_collection_labels_are_causal_and_bundle_has_real_rows(self):
        from worker import episode
        from run import bundle
        class FixtureEnv:
            def __init__(self):
                self.i = 0
                self.last_damping = 1
            def sample(self):
                z = [.8, .2, -.2, -.6, -.6, -.6, -.6, -.6, -.6][min(self.i, 8)]
                s = dict(time=self.i*.2, position=[0, 0, z], rpy=[0, 0, 0],
                         velocity=[0, 0, 0], gyro=[0, 0, 0], accel=[0, 0, 9.8],
                         min_scan=5, counts=dict(watchdog=0),
                         ages=dict(odom=0, imu=0, scan=0, image=0))
                return s, np.arange(48*48*3, dtype=np.uint8).reshape(48, 48, 3)
            def reset(self, spec):
                return self.sample()
            def command(self, action):
                pass
            def damping(self, value):
                self.last_damping = value
            def advance(self, start, dt):
                self.i += 1
                return self.sample()
        spec = scenario(0, "air_to_water", self.c)
        spec.update(goal=[0, 0, -.6], perturb_center=0., visual_shift=0.)
        with tempfile.TemporaryDirectory() as d:
            episode(FixtureEnv(), self.c, spec, "both", "teacher", d)
            path = next((Path(d)/"episodes").glob("*.npz"))
            with np.load(path, allow_pickle=False) as data:
                self.assertEqual(data["labels"][1, 1], 0)
                self.assertEqual(data["labels"][2, 1], 1)
                self.assertEqual(len(data["actions"]), len(data["images"]))
            from report import episodes
            self.assertEqual(len(episodes(d)), 1)  # preview metadata isn't an episode
            self.assertTrue(bundle(Path(d)).exists())


if __name__ == "__main__":
    unittest.main()
