import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from diagnostic_core import (LEGACY, METHODS, EventWindow, Execution, choose_profile,
                             load_protocol, make_spec, routing)
from diagnostic_report import make_report
from diagnostic_worker import episode
from diagnose import validate_source
from core import canonical_hash, json_write, load_config


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.c = load_config(LEGACY/'config.json')
        self.p = load_protocol(HERE/'protocol.json', self.c)
        self.profile = self.p['profiles'][0]

    def check_protocol_rejected(self, p):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'p.json'
            json_write(path, p)
            with self.assertRaises(ValueError):
                load_protocol(path, self.c)

    def test_reused_test_seed_rejected(self):
        self.p['test_seeds'][0] = 100
        self.check_protocol_rejected(self.p)

    def test_extreme_force_rejected(self):
        self.p['profiles'][0]['force_y_N'] = 500
        self.check_protocol_rejected(self.p)

    def test_geometry_is_near_only(self):
        ex = Execution('geometry', self.c)
        ex.replace(np.zeros((4, 3)))
        self.assertIsNone(ex.reason(1., 1., .1))
        self.assertEqual(ex.reason(0., .1, .1), 'near_only')
        self.assertEqual(routing('geometry', .9, .8, True, True), (0., 0., 'short'))

    def test_chunk_consumption_has_no_repeated_tail(self):
        chunk = np.arange(12).reshape(4, 3)*.01
        for method in METHODS[1:]:
            ex = Execution(method, self.c)
            ex.replace(chunk)
            for row in chunk:
                np.testing.assert_allclose(ex.next(), row)
            self.assertEqual(ex.reason(0., 2., 0.), 'empty_or_expired')
            with self.assertRaises(RuntimeError):
                ex.next()

    def test_oracle_and_unified_routing(self):
        self.assertEqual(routing('oracle', .1, .9, True, False), (1., 0., 'separated'))
        self.assertEqual(routing('unified', .1, .9, True, False), (.9, .9, 'unified'))

    def test_motion_label_only_acknowledged_completed_overlap(self):
        e = EventWindow(self.profile, 'both', .3)
        e.receipt = dict(start_upper=1.1, end_lower=1.8, end_upper=1.9)
        self.assertFalse(e.completed_interval_label(.8, 1.05))
        self.assertTrue(e.completed_interval_label(1., 1.2))
        self.assertFalse(e.completed_interval_label(1.8, 2.))

    def test_one_shot_visual_window_and_restore(self):
        e = EventWindow(self.profile, 'both', .3)
        rng = np.random.RandomState(0)
        black, white = np.zeros((48, 48, 3), dtype=np.uint8), np.full((48, 48, 3), 255, dtype=np.uint8)
        _, active = e.observe(1., .2, black, rng)
        self.assertTrue(active)
        self.assertTrue(e.needs_pulse())
        e.receipt = dict(start_upper=1., end_lower=1.8, end_upper=1.8)
        self.assertFalse(e.needs_pulse())
        image, active = e.observe(3., .2, white, rng)
        self.assertFalse(active)
        np.testing.assert_array_equal(image, white)
        self.assertEqual(e.anchor, 1.)

    def test_interventions_are_factorial(self):
        image = np.zeros((48, 48, 3), dtype=np.uint8)
        for condition in self.p['conditions']:
            e = EventWindow(self.profile, condition, .3)
            _, visual = e.observe(1., 0., image, np.random.RandomState(0))
            self.assertEqual(visual, condition in ('visual', 'both'))
            self.assertEqual(e.needs_pulse(), condition in ('dynamics', 'both'))

    def calibration(self):
        metas = []
        for profile in ['control', 'mild', 'moderate']:
            for seed in self.p['calibration_seeds']:
                for direction in ['air_to_water', 'water_to_air']:
                    for method in ['teacher', 'short', 'long']:
                        metas.append(dict(split='calibration', profile=profile, seed=seed, direction=direction,
                            method=method, reason='success', watchdog_ticks=0, pulse_receipt={}, visual_frames=3,
                            peak_roll_pitch_deg=1. if profile=='control' else 2., max_cross_track_m=.01))
        return metas

    def test_selection_not_based_on_student_advantage(self):
        metas = self.calibration()
        for m in metas:
            if m['profile']=='mild' and m['method']!='teacher':
                m['reason'] = 'tilt'
        self.assertEqual(choose_profile(metas, self.p)['selected'], 'mild')

    def test_infeasible_teacher_rejects_profile(self):
        metas = self.calibration()
        next(m for m in metas if m['profile']=='mild' and m['method']=='teacher')['reason'] = 'tilt'
        self.assertEqual(choose_profile(metas, self.p)['selected'], 'moderate')

    def test_no_measurable_stress_rejects_profiles(self):
        metas = self.calibration()
        for m in metas:
            m['peak_roll_pitch_deg'] = 1.
        self.assertIsNone(choose_profile(metas, self.p)['selected'])

    def test_test_data_cannot_select_profile(self):
        metas = self.calibration()
        metas[0]['split'] = 'test'
        with self.assertRaises(ValueError):
            choose_profile(metas, self.p)

    def test_completion_requires_episode_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            json_write(root/'worker_result.json', dict(status='completed'))
            report = make_report(root, self.p)
            self.assertEqual(report['diagnosis'], 'INCOMPLETE')
            self.assertEqual(report['expected_test_episodes'], 168)

    def test_screening_requires_calibration_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            json_write(root/'worker_result.json', dict(status='no_eligible_profile'))
            json_write(root/'selection.json', dict(selected=None))
            self.assertEqual(make_report(root, self.p)['diagnosis'], 'INCOMPLETE')

    def test_source_identity_and_checkpoint_required(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            sources = {'source.py': 'exacthash'}
            json_write(root/'report.json', dict(execution_complete=True, actual_episodes=120, expected_episodes=120))
            json_write(root/'provenance.json', dict(phase='phase2', config_sha256=canonical_hash(self.c), source_sha256=sources))
            (root/'models/seed_0').mkdir(parents=True)
            (root/'models/seed_0/model.pt').write_bytes(b'fixture; not loaded')
            self.assertEqual(validate_source(root, self.c, sources).name, 'model.pt')
            with self.assertRaises(RuntimeError):
                validate_source(root, self.c, {'source.py': 'changed'} )
            (root/'models/seed_0/model.pt').unlink()
            with self.assertRaises(RuntimeError):
                validate_source(root, self.c, sources)

    def test_real_episode_driver_causal_labels_and_common_expert_input(self):
        c = dict(self.c, success_hold_seconds=.4)
        spec = make_spec(200, 'air_to_water', c, 'test')
        spec.update(start=[0., 0., .2], goal=[0., 0., -.2])
        class Env:
            def __init__(self):
                self.t = 0.
                self.cleared = False
            def state(self, z):
                return dict(time=self.t, position=[0., 0., z], rpy=[0., 0., 0.], velocity=[0., 0., 0.],
                            gyro=[0., 0., 0.], accel=[0., 0., 0.], min_scan=5., counts={'watchdog': 0})
            def reset(self, spec):
                return self.state(.2), np.zeros((48, 48, 3), dtype=np.uint8)
            def command(self, action):
                return self.t
            def pulse(self, profile, sign):
                return dict(start_upper=self.t, end_lower=self.t+.8, end_upper=self.t+.8)
            def advance(self, start, dt):
                self.t += dt
                return self.state(-.2), np.zeros((48, 48, 3), dtype=np.uint8)
            def clear_pulse(self):
                self.cleared = True
        class Predictor:
            seed = 0
            def __init__(self):
                self.features = []
            def events(self, image, history):
                return .1, .05, .123
            def actions(self, features):
                self.features.append(features)
                return np.zeros((4, 3))
        for method in ['oracle', 'unified', 'geometry', 'separated']:
            with tempfile.TemporaryDirectory() as root:
                env, predictor = Env(), Predictor()
                meta = episode(env, predictor, c, self.p, spec, self.profile, 'both', method, root)
                self.assertEqual(meta['reason'], 'success')
                rows = [json.loads(s) for s in next((Path(root)/'episodes').glob('*.jsonl')).read_text().splitlines()]
                self.assertFalse(rows[0]['motion_label_previous_interval'])
                self.assertTrue(rows[1]['motion_label_previous_interval'])
                if method=='oracle':
                    self.assertEqual([r['motion_used'] for r in rows], [0., 1.])
                for f in predictor.features:
                    self.assertAlmostEqual(float(f[-3]), .123, places=6)
                self.assertTrue(env.cleared)

    def test_wrench_request_world_frame_and_timing_bounds(self):
        calls = []
        times = iter([1., 1.01])
        def request():
            return SimpleNamespace(wrench=SimpleNamespace(force=SimpleNamespace(y=0.), torque=SimpleNamespace(x=0.)))
        fake_ros = SimpleNamespace(Time=SimpleNamespace(now=lambda: SimpleNamespace(to_sec=lambda: next(times)),
                                                       from_sec=lambda t: t), Duration=SimpleNamespace(from_sec=lambda t: t))
        fake_srv = SimpleNamespace(ApplyBodyWrench=object, ApplyBodyWrenchRequest=request,
                                   BodyRequest=object, GetModelProperties=object)
        with patch.dict(sys.modules, {'rospy': fake_ros, 'gazebo_msgs.srv': fake_srv,
                                      'ros_io': SimpleNamespace(Gazebo=object)}):
            spec = importlib.util.spec_from_file_location('diagnostic_ros_test', HERE/'diagnostic_ros.py')
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            env = object.__new__(mod.DiagnosticGazebo)
            env.body = 'robot::base_link'
            env.apply_wrench = lambda req: (calls.append(req) or SimpleNamespace(success=True))
            receipt = env.pulse(self.profile, -1.)
        self.assertEqual(calls[0].reference_frame, 'world')
        self.assertEqual(calls[0].wrench.force.y, -1.5)
        self.assertEqual(calls[0].wrench.torque.x, -.06)
        self.assertEqual(receipt['start_upper'], 1.01)
        self.assertEqual(receipt['end_lower'], 1.8)


if __name__ == '__main__':
    unittest.main()
