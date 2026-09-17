"""Real ROS episodes with a frozen common expert and causal event labels."""
import argparse
import json
from pathlib import Path
import signal
import time
import traceback
import numpy as np
from diagnostic_core import (EventWindow, Execution, choose_profile, cross_track,
                             load_protocol, make_spec, routing)
from core import (HeightFilter, expert_features, history_at, history_row, json_write,
                  load_config, resize_rgb, sense, success_sample, teacher,
                  terminal_reason, write_png)


def episode(env, predictor, c, p, spec, profile, condition, method, output):
    stem = '%s_%s_s%d_%s_%s_%s' % (spec['diagnostic_split'], spec['direction'],
             spec['seed'], profile['name'], condition, method)
    root = Path(output)/'episodes'
    root.mkdir(exist_ok=True)
    if (root/(stem+'.json')).exists():
        raise RuntimeError('Refusing to overwrite '+stem)
    meta = dict(split=spec['diagnostic_split'], seed=spec['seed'], direction=spec['direction'],
                profile=profile['name'], condition=condition, method=method, scenario=spec,
                model_seed=predictor.seed, reason='infrastructure_error', complete=False)
    rows, images, history_rows = [], [], []
    state = initial = None
    window = EventWindow(profile, condition, p['trigger_height'])
    started_wall = heartbeat = time.monotonic()
    error = None
    try:
        state, rgb = env.reset(spec)
        initial = state
        start = state['time']
        previous_time = start-c['dt']
        previous_action = np.zeros(3, dtype=np.float32)
        previous_motion = False
        filt, executor = HeightFilter(c['filter_gain']), Execution(method, c)
        noise = np.random.RandomState(spec['seed']*97+(11 if spec['direction']=='air_to_water' else 29))
        image_noise = np.random.RandomState(spec['seed']*101+37)
        success_time = recovery_hold = 0.
        crossed = False
        recovered = None
        reason = None
        with (root/(stem+'.jsonl')).open('w') as log:
            while reason is None:
                tick = time.monotonic()
                reason = terminal_reason(state, spec['goal'], state['time']-start, c)
                if reason is not None:
                    break
                z = state['position'][2]
                image = resize_rgb(rgb, c['image_size'])
                observed, visual_label = window.observe(state['time'], z, image, image_noise)
                sensor = sense(state, noise, c)
                input_dt = state['time']-previous_time
                history_rows.append(history_row(sensor, previous_action, input_dt))
                history = history_at(history_rows, len(history_rows)-1, c['history'])
                raw_visual = raw_motion = visual_used = motion_used = None
                event_ms = expert_ms = 0.
                trigger = None
                if method == 'teacher':
                    visual_height = estimated_height = float(z)
                    action = teacher(state, spec['goal'])
                else:
                    t = time.monotonic()
                    visual_height, raw_visual, raw_motion = predictor.events(observed, history)
                    visual_used, motion_used, filter_mode = routing(
                        method, raw_visual, raw_motion, visual_label, previous_motion)
                    estimated_height = filt.update(visual_height, sensor[7], input_dt,
                                                   visual_used, filter_mode)
                    event_ms = (time.monotonic()-t)*1000
                    trigger = executor.reason(motion_used, estimated_height, sensor[7])
                    if trigger:
                        t = time.monotonic()
                        # Expert conditioning is identical across all student strategies.
                        features = expert_features(sensor, estimated_height, spec['goal'],
                                                   previous_action, raw_motion)
                        executor.replace(predictor.actions(features))
                        expert_ms = (time.monotonic()-t)*1000
                    action = executor.next()
                command_time = env.command(action)
                new_receipt = None
                if window.needs_pulse():
                    new_receipt = window.receipt = env.pulse(profile, spec['pulse_sign'])
                decision_ms = (time.monotonic()-tick)*1000
                before = state
                state, rgb = env.advance(before['time'], c['dt'])
                dt = state['time']-before['time']
                next_motion = window.completed_interval_label(before['time'], state['time'])
                crossed = crossed or (state['position'][2] < -.1 if spec['direction']=='air_to_water'
                                       else state['position'][2] > .1)
                reason = terminal_reason(state, spec['goal'], state['time']-start, c)
                success_time = success_time+dt if success_sample(state, spec['goal'], c) else 0.
                if reason is None and crossed and success_time >= c['success_hold_seconds']:
                    reason = 'success'
                end = window.ended()
                if end is not None and state['time'] >= end and recovered is None:
                    calm = (max(abs(x) for x in state['rpy'][:2]) < p['recovery_roll_pitch_rad']
                            and abs(state['velocity'][1]) < p['recovery_lateral_speed'])
                    recovery_hold = recovery_hold+max(0., state['time']-max(before['time'], end)) if calm else 0.
                    if recovery_hold >= p['recovery_hold_seconds']:
                        recovered = state['time']-end
                row = dict(step=len(rows), pre=before, post=state, dt=dt, input_dt=input_dt,
                           physical_action=action.tolist(), command_time=command_time,
                           visual_label=visual_label, motion_label_previous_interval=previous_motion,
                           motion_label_completed_now=next_motion, anchor=window.anchor,
                           new_pulse_receipt=new_receipt, visual_height=float(visual_height),
                           estimated_height=float(estimated_height), raw_visual=raw_visual,
                           raw_motion=raw_motion, visual_used=visual_used, motion_used=motion_used,
                           expert_conditioning_motion_score=raw_motion, replan_reason=trigger,
                           expert_replanned=bool(trigger), event_ms=event_ms, expert_ms=expert_ms,
                           decision_ms=decision_ms, cross_track_m=cross_track(state['position'], spec),
                           terminal_reason=reason)
                log.write(json.dumps(row, allow_nan=False)+'\n')
                log.flush()
                rows.append(row)
                images.append(observed)
                previous_time, previous_action, previous_motion = before['time'], action, next_motion
                if time.monotonic()-heartbeat > 30:
                    print(json.dumps(dict(heartbeat=stem, steps=len(rows))), flush=True)
                    heartbeat = time.monotonic()
        end = window.ended()
        meta.update(reason=reason, complete=True, crossed=bool(crossed), recovery_seconds=recovered,
                    recovery_censored=end is not None and recovered is None,
                    post_event_observation_seconds=max(0., state['time']-end) if end is not None else None)
    except BaseException as exc:
        error = repr(exc)
        meta['error'] = error
        raise
    finally:
        cleanup_error = None
        try:
            env.command([0, 0, 0])
            env.clear_pulse()
        except BaseException as exc:
            cleanup_error = exc
            meta.update(cleanup_error=repr(exc), complete=False, reason='infrastructure_error')
        meta.update(steps=len(rows), initial=initial, final=state,
                    wall_seconds=time.monotonic()-started_wall, anchor=window.anchor,
                    pulse_receipt=window.receipt, visual_frames=sum(r['visual_label'] for r in rows),
                    watchdog_ticks=(state['counts'].get('watchdog', 0)-initial['counts'].get('watchdog', 0))
                    if state is not None and initial is not None else 0,
                    simulation_seconds=state['time']-initial['time'] if initial is not None else 0.,
                    peak_roll_pitch_deg=float(np.rad2deg(max(
                        [abs(x) for r in rows for x in r['post']['rpy'][:2]] or [0.]))),
                    max_cross_track_m=max([r['cross_track_m'] for r in rows] or [0.]))
        json_write(root/(stem+'.json'), meta)
        if images:
            np.savez_compressed(root/(stem+'.npz'), images=np.asarray(images))
            ids = np.linspace(0, len(images)-1, min(5, len(images))).astype(int)
            write_png(root/(stem+'.png'), np.concatenate([images[i] for i in ids], axis=1))
        print(json.dumps(dict(episode=stem, reason=meta['reason'], steps=len(rows))), flush=True)
        if cleanup_error is not None and error is None:
            raise cleanup_error
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    out = parser.parse_args().output
    c = load_config(out/'config.json')
    p = load_protocol(out/'protocol.json', c)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    result = dict(status='running', episodes=0)
    json_write(out/'worker_result.json', result)
    env = None
    try:
        from learning import Predictor
        from diagnostic_ros import DiagnosticGazebo
        predictor = Predictor(out/'frozen/model.pt', c)
        env = DiagnosticGazebo(c)
        import rosgraph
        publishers, _, _ = rosgraph.Master('/interface_diagnostic_guard').getSystemState()
        owners = dict(publishers).get('/'+c['namespace']+'/command/trajectory', [])
        if owners != ['/hydrone_interface_pilot']:
            raise RuntimeError('Trajectory publisher ownership invalid: '+str(owners))
        json_write(out/'sensor_preflight.json', dict(trajectory_publishers=owners,
                   wrench_body=env.body, zero_force_probe=env.probe_receipt))
        metas = []
        control = dict(p['profiles'][0], name='control')
        for profile, condition in [(control, 'clean')]+[(q, 'both') for q in p['profiles']]:
            for seed in p['calibration_seeds']:
                for direction in ('air_to_water', 'water_to_air'):
                    spec = make_spec(seed, direction, c, 'calibration')
                    for method in np.roll(['teacher', 'short', 'long'], seed % 3):
                        metas.append(episode(env, predictor, c, p, spec, profile, condition, str(method), out))
                        result['episodes'] += 1
        selection = choose_profile(metas, p)
        json_write(out/'selection.json', selection)
        if selection['selected'] is None:
            result['status'] = 'no_eligible_profile'
            return
        profile = next(q for q in p['profiles'] if q['name']==selection['selected'])
        print('Selected validation profile: '+profile['name'], flush=True)
        for seed in p['test_seeds']:
            for direction in ('air_to_water', 'water_to_air'):
                spec = make_spec(seed, direction, c, 'test')
                for condition in np.roll(p['conditions'], seed % len(p['conditions'])):
                    for method in np.roll(p['methods'], seed % len(p['methods'])):
                        episode(env, predictor, c, p, spec, profile, str(condition), str(method), out)
                        result['episodes'] += 1
        result['status'] = 'completed'
    except BaseException as exc:
        result.update(status='failed', error=repr(exc), traceback=traceback.format_exc())
        raise
    finally:
        try:
            if env is not None:
                env.close()
        except BaseException as exc:
            result.update(status='failed', cleanup_error=repr(exc))
            raise
        finally:
            json_write(out/'worker_result.json', result)


if __name__ == '__main__':
    main()
