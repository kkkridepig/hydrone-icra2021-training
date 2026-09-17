"""Real ROS experiment worker. No synthetic success data or fallback mode."""
import argparse
import json
import signal
import time
import traceback
from pathlib import Path

import numpy as np

from core import (ChunkExecutor, HeightFilter, corrupt_image, decision_probs, expert_features,
                  history_at, history_row, interventions, json_write, load_config,
                  resize_rgb, scenario, sense, success_sample, teacher, terminal_reason, write_png)


def episode(env, c, spec, condition, method, output, predictor=None):
    model_seed = -1 if predictor is None else predictor.seed
    stem = "%s_%s_s%d_%s_m%d" % (spec["direction"], condition, spec["seed"], method, model_seed)
    root = Path(output)/"episodes"
    root.mkdir(exist_ok=True)
    if (root/(stem+".json")).exists():
        raise RuntimeError("Refusing to overwrite episode " + stem)
    rows = []
    data = {key: [] for key in ("images", "history_rows", "heights", "labels",
                                "sensors", "actions", "input_dt")}
    state, rgb = env.reset(spec)
    initial = state
    start = state["time"]
    previous_time = start-c["dt"]
    previous_action = np.zeros(3, dtype=np.float32)
    previous_dyn = False
    filt = HeightFilter(c["filter_gain"])
    executor = ChunkExecutor(method, c)
    noise = np.random.RandomState(spec["seed"]*97 + (11 if spec["direction"] == "air_to_water" else 29))
    image_noise = np.random.RandomState(spec["seed"]*101+37)
    success_time = 0.0
    crossed = False
    first_crossing = None
    reason, error = None, None
    started_wall = time.monotonic()
    log_path = root/(stem+".jsonl")
    try:
        with log_path.open("w") as log:
            while reason is None:
                tick_wall = time.monotonic()
                elapsed = state["time"]-start
                reason = terminal_reason(state, spec["goal"], elapsed, c)
                if reason is not None:
                    break
                z = state["position"][2]
                visual_active, dyn_active, damping = interventions(z, spec, condition, c)
                image = resize_rgb(rgb, c["image_size"])
                candidate = corrupt_image(image, spec["visual_strength"], image_noise)
                observed = candidate if visual_active else image
                sensor = sense(state, noise, c)
                input_dt = state["time"]-previous_time
                row = history_row(sensor, previous_action, input_dt)
                data["history_rows"].append(row)
                history = history_at(data["history_rows"], len(data["history_rows"])-1, c["history"])
                event_ms = expert_ms = 0.0
                replan = True
                if method == "teacher":
                    visual_height, pv, pd = float(z), None, None
                    estimated_height = z
                    action = teacher(state, spec["goal"])
                else:
                    t = time.monotonic()
                    visual_height, pv_raw, pd_raw = predictor.events(observed, history)
                    pv, pd = decision_probs(pv_raw, pd_raw, method)
                    estimated_height = filt.update(visual_height, sensor[7], input_dt, pv, method)
                    event_ms = (time.monotonic()-t)*1000
                    replan = executor.needs_plan(pv, pd, estimated_height, sensor[7])
                    if replan:
                        t = time.monotonic()
                        features = expert_features(sensor, estimated_height, spec["goal"], previous_action, pd)
                        executor.replace(predictor.actions(features))
                        expert_ms = (time.monotonic()-t)*1000
                    action = executor.next()
                # Labels describe the current corrupted image and the completed
                # previous action interval. Current injection is not observable
                # in current motion history and is never used as its label.
                data["images"].append(observed)
                data["heights"].append(z)
                data["labels"].append([float(visual_active), float(previous_dyn)])
                data["sensors"].append(sensor)
                data["actions"].append(action)
                data["input_dt"].append(input_dt)
                env.damping(damping)
                command_time = env.command(action)
                decision_ms = (time.monotonic()-tick_wall)*1000
                before = state
                state, rgb = env.advance(before["time"], c["dt"])
                dt_actual = state["time"]-before["time"]
                in_destination = (state["position"][2] < -0.1 if spec["direction"] == "air_to_water"
                                  else state["position"][2] > 0.1)
                if in_destination and not crossed:
                    first_crossing = state["time"]-start
                crossed = crossed or in_destination
                # Failure takes precedence over goal success.
                reason = terminal_reason(state, spec["goal"], state["time"]-start, c)
                if success_sample(state, spec["goal"], c):
                    success_time += dt_actual
                else:
                    success_time = 0.0
                if reason is None and crossed and success_time >= c["success_hold_seconds"]:
                    reason = "success"
                record = dict(step=len(rows), simulation_time=before["time"],
                              elapsed=before["time"]-start, dt=dt_actual,
                              command_enqueued_simulation_time=command_time,
                              pre=before, post=state, physical_action=action.tolist(),
                              visual_active=visual_active, dynamics_active=dyn_active,
                              motion_label_previous_interval=previous_dyn,
                              damping_requested_and_readback=damping,
                              image_std=float(image.astype(float).std(axis=(0, 1)).mean()/255),
                              visual_height=float(visual_height), estimated_height=float(estimated_height),
                              visual_bad_probability=None if pv is None else float(pv),
                              dynamics_bad_probability=None if pd is None else float(pd),
                              event_ms=event_ms, expert_ms=expert_ms, decision_ms=decision_ms,
                              deadline_ms=c["dt"]*1000,
                              expert_replanned=bool(replan), terminal_reason=reason)
                if method != "teacher":
                    record["raw_probabilities"] = [float(pv_raw), float(pd_raw)]
                log.write(json.dumps(record, allow_nan=False)+"\n")
                log.flush()
                rows.append(record)
                previous_time = before["time"]
                previous_action = action
                previous_dyn = dyn_active
    except BaseException as exc:
        reason = "infrastructure_error"
        error = repr(exc)
        raise
    finally:
        env.command([0, 0, 0])
        meta = dict(scenario=spec, condition=condition, method=method, model_seed=model_seed,
                    reason=reason or "interrupted", error=error, crossed=bool(crossed),
                    steps=len(rows), initial=initial, final=state,
                    wall_seconds=time.monotonic()-started_wall,
                    simulation_seconds=state["time"]-start,
                    first_crossing_seconds=first_crossing,
                    watchdog_ticks=state["counts"].get("watchdog", 0)-initial["counts"].get("watchdog", 0),
                    complete=reason not in (None, "infrastructure_error", "interrupted"),
                    observation_contract="sim_odom_proxy_no_absolute_z_plus_rgb",
                    bridge=c["bridge"])
        # Truncate to completed transitions only after interruption.
        arrays = {k: np.asarray(v[:len(rows)]) for k, v in data.items()}
        np.savez_compressed(root/(stem+".npz"), **arrays)
        json_write(root/(stem+".json"), meta)
        if rows:
            # Five ordered camera samples; corresponding labels remain metadata.
            ids = np.linspace(0, len(rows)-1, min(5, len(rows))).astype(int)
            write_png(root/(stem+".png"), np.concatenate([arrays["images"][i] for i in ids], axis=1))
            json_write(root/(stem+".preview.json"), [
                dict(index=int(i), height=float(arrays["heights"][i]),
                     visual_injection=bool(arrays["labels"][i, 0])) for i in ids])
        print(json.dumps(dict(episode=stem, reason=meta["reason"], steps=len(rows),
                              crossed=crossed)), flush=True)
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["collect", "evaluate"], required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint", action="append", default=[])
    args = parser.parse_args()
    c = load_config(args.config)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    result = dict(status="running", mode=args.mode, episodes=0)
    json_write(output/"worker_result.json", result)
    env = None
    try:
        from ros_io import Gazebo
        env = Gazebo(c)
        # Refuse competing publishers on the actual trajectory topic.
        import rosgraph
        publishers, _, _ = rosgraph.Master("/interface_guard").getSystemState()
        owners = dict(publishers).get("/"+c["namespace"]+"/command/trajectory", [])
        if len(owners) != 1 or owners[0] != "/hydrone_interface_pilot":
            raise RuntimeError("Trajectory publisher ownership invalid: " + str(owners))
        json_write(output/"sensor_preflight.json", dict(counts=env.counts,
                   trajectory_publishers=owners, damping_readback=env.get_damping().data))
        directions = ["air_to_water", "water_to_air"]
        if args.mode == "collect":
            screened = set()
            for direction in directions:
                seed = c["train_seeds"][0]
                spec = scenario(seed, direction, c, "train")
                check = episode(env, c, spec, "clean", "teacher", output)
                result["episodes"] += 1
                screened.add((seed, direction, "clean"))
                if check["reason"] != "success":
                    raise RuntimeError("Initial clean crossing failed (%s: %s). Inspect control/model before collecting."
                                       % (direction, check["reason"]))
            for split, seeds in [("train", c["train_seeds"]), ("validation", c["validation_seeds"])]:
                for seed in seeds:
                    for direction in directions:
                        spec = scenario(seed, direction, c, split)
                        # Rotate condition ordering to limit reset/order confounding.
                        conditions = list(np.roll(c["conditions"], seed % 4))
                        for condition in conditions:
                            if (seed, direction, condition) in screened:
                                continue
                            episode(env, c, spec, condition, "teacher", output)
                            result["episodes"] += 1
        else:
            from learning import Predictor
            predictors = [Predictor(p, c) for p in args.checkpoint]
            if not predictors:
                raise ValueError("Evaluation requires trained checkpoints")
            for seed in c["test_seeds"]:
                for direction in directions:
                    spec = scenario(seed, direction, c, "test")
                    for condition in list(np.roll(c["conditions"], seed % 4)):
                        episode(env, c, spec, condition, "teacher", output)
                        result["episodes"] += 1
                        for predictor in predictors:
                            methods = [m for m in c["methods"] if m != "teacher"]
                            methods = list(np.roll(methods, (seed+predictor.seed) % len(methods)))
                            for method in methods:
                                episode(env, c, spec, condition, method, output, predictor)
                                result["episodes"] += 1
        result["status"] = "completed"
    except BaseException as exc:
        result.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        raise
    finally:
        json_write(output/"worker_result.json", result)
        if env is not None:
            env.close()


if __name__ == "__main__":
    main()
