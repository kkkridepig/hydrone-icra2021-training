#!/usr/bin/env python3
"""Opt-in paper agent runner used by the Phase 7/8 launch wrapper.

The wrapper never starts this node unless ``run_agent:=true``.  Formal
training therefore remains explicit, while train/evaluate/resume semantics,
bounded stability gates, and reproducible reporting share one entry point for
both algorithms.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict

import numpy as np
import rospy
import torch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[0]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from hydrone_icra2021.ddpg import DDPGAgent, DDPGConfig  # noqa: E402
from hydrone_icra2021.environment import EnvironmentConfig, PaperEnvironment  # noqa: E402
from hydrone_icra2021.runner_utils import (  # noqa: E402
    load_yaml_with_base,
    moving_average,
    real_time_factor,
)
from hydrone_icra2021.sac import SACAgent, SACConfig  # noqa: E402


def _require_file(path: str, label: str) -> Path:
    value = Path(path).expanduser().resolve()
    if not value.is_file():
        raise ValueError("%s does not exist: %s" % (label, value))
    return value


def _load_config(path: str) -> Dict[str, Any]:
    if not path:
        raise ValueError("config is required when run_agent=true")
    return load_yaml_with_base(str(_require_file(path, "config")))


def _training_config(algorithm: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Return one agent-specific mapping from the shared paper YAML."""

    training = dict(data.get("training", {}))
    if not isinstance(training, dict):
        raise ValueError("training must contain a YAML mapping")
    if "seed" not in training:
        if data.get("seed") is not None:
            training["seed"] = int(data["seed"])
        else:
            seeds = training.get("seeds", {}).get("values", [0])
            training["seed"] = int(seeds[0])

    if algorithm == "ddpg":
        aliases = {"ddpg_tau": "tau"}
        config_fields = DDPGConfig.__dataclass_fields__
    else:
        aliases = {
            "sac_tau": "tau",
            "sac_alpha": "alpha",
            "sac_automatic_entropy_tuning": "automatic_entropy_tuning",
            "sac_target_entropy": "target_entropy",
        }
        config_fields = SACConfig.__dataclass_fields__
    for source, target in aliases.items():
        if target not in training and source in training:
            training[target] = training[source]
    return {key: training[key] for key in config_fields if key in training}


def _make_agent(algorithm: str, data: Dict[str, Any]):
    training = _training_config(algorithm, data)
    if algorithm == "ddpg":
        return DDPGAgent(DDPGConfig(**training)), training
    return SACAgent(SACConfig(**training)), training


def _environment_config(
    stage: int, namespace: str, data: Dict[str, Any], seed: int
) -> EnvironmentConfig:
    episode = data.get("episode", {})
    goal_sampling = data.get("goal_sampling", {})
    start = tuple(float(value) for value in episode.get("start", (0.0, 0.0, 2.5)))
    return EnvironmentConfig(
        namespace=namespace,
        stage=stage,
        start=start,
        goal_tolerance=float(data.get("reward", {}).get("goal_distance_m", 0.25)),
        collision_threshold=float(data.get("reward", {}).get("collision_threshold_m", 0.5)),
        max_steps=int(data.get("max_steps", episode.get("max_steps", 500))),
        goal_mode=str(goal_sampling.get("mode", "upstream_public")),
        goal_seed=int(data.get("goal_seed", seed)),
    )


def _git_metadata() -> Dict[str, Any]:
    """Capture the exact repository revision and dirty-state evidence."""

    def run(*args: str) -> str:
        try:
            return subprocess.check_output(
                args, cwd=str(REPOSITORY_ROOT), stderr=subprocess.DEVNULL, text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return "NOT_AVAILABLE"

    return {
        "repository": str(REPOSITORY_ROOT),
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "head": run("git", "rev-parse", "HEAD"),
        "status_porcelain": run("git", "status", "--porcelain"),
    }


def _manifest(
    algorithm: str,
    stage: int,
    mode: str,
    namespace: str,
    config_path: str,
    seed: int,
    data: Dict[str, Any],
    output_dir: Path,
) -> Dict[str, Any]:
    config_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "profile": "icra2021_paper_wrapper",
        "phase": 8,
        "algorithm": algorithm,
        "stage": stage,
        "mode": mode,
        "seed": seed,
        "config_path": str(Path(config_path).expanduser().resolve()),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "output_dir": str(output_dir),
        "git": _git_metadata(),
        "ros": {
            "namespace": namespace,
            "scan_topic": namespace.rstrip("/") + "/scan",
            "ground_truth_odom_topic": namespace.rstrip("/") + "/ground_truth/odometry",
            "controller_odom_topic": namespace.rstrip("/") + "/odometry_sensor1/odometry",
            "cmd_vel_topic": namespace.rstrip("/") + "/cmd_vel",
            "trajectory_topic": namespace.rstrip("/") + "/command/trajectory",
            "motor_speed_topic": namespace.rstrip("/") + "/command/motor_speed",
            "submerged_topic": namespace.rstrip("/") + "/is_submerged",
            "wind_enabled": bool(data.get("disturbances", {}).get("wind_enabled", False)),
        },
        "observation_contract": copy.deepcopy(data.get("observation", {})),
        "action_contract": copy.deepcopy(data.get("action", {})),
        "goal_sampling": copy.deepcopy(data.get("goal_sampling", {})),
        "training": copy.deepcopy(data.get("training", {})),
    }


def _sim_time() -> float:
    value = float(rospy.get_time())
    return value if math.isfinite(value) and value >= 0.0 else 0.0


def _run_episode(
    env: PaperEnvironment,
    agent: Any,
    max_steps: int,
    evaluate: bool,
    on_step: Any = None,
) -> Dict[str, Any]:
    started_wall = time.monotonic()
    started_sim = _sim_time()
    state = env.reset()
    total_reward = 0.0
    terminated = False
    truncated = False
    steps = 0
    last_update: Dict[str, float] = {}
    for _ in range(max_steps):
        normalized = (
            agent.deterministic_normalized_action(state)
            if evaluate
            else agent.select_normalized_action(state, explore=True)
        )
        physical = agent.to_physical_action(normalized)
        next_state, reward, terminated, truncated, _info = env.step(physical)
        if not evaluate:
            update = agent.observe(
                state, normalized, reward, next_state, terminated, truncated
            )
            if update is not None:
                last_update = {key: float(value) for key, value in update.items()}
        state = next_state
        total_reward += float(reward)
        steps += 1
        if on_step is not None:
            on_step(
                steps=steps,
                reward=float(reward),
                total_reward=float(total_reward),
                terminated=bool(terminated),
                truncated=bool(truncated),
            )
        if terminated or truncated:
            break
    wall_elapsed = max(1.0e-9, time.monotonic() - started_wall)
    sim_elapsed = max(0.0, _sim_time() - started_sim)
    return {
        "steps": steps,
        "reward": float(total_reward),
        "terminated": bool(terminated),
        "truncated": bool(truncated),
        "wall_elapsed_s": wall_elapsed,
        "sim_elapsed_s": sim_elapsed,
        "rtf": real_time_factor(sim_elapsed, wall_elapsed),
        "update_metrics": last_update,
    }


def _checkpoint_path(
    algorithm: str,
    stage: int,
    seed: int,
    checkpoint_arg: str,
    data: Dict[str, Any],
    run_suffix: str = "",
) -> Path:
    if checkpoint_arg:
        return Path(checkpoint_arg).expanduser().resolve()
    checkpoint = data.get("checkpoint", {})
    configured = checkpoint.get("path")
    template = checkpoint.get("path_template")
    value = configured or template
    if value:
        path = Path(str(value).format(algorithm=algorithm, stage=stage, seed=seed)).expanduser().resolve()
    else:
        path = Path(
        "/home/rosnoetic/hydrone_repro/icra2021/checkpoints"
        ) / ("phase8_%s_stage%d_seed%d.pt" % (algorithm, stage, seed))
    if run_suffix:
        path = path.with_name(path.stem + "_" + run_suffix + path.suffix)
    return path


def _output_dir(
    algorithm: str,
    stage: int,
    seed: int,
    data: Dict[str, Any],
    run_suffix: str = "",
) -> Path:
    logging = data.get("logging", {})
    value = logging.get(
        "output_dir",
        "/home/rosnoetic/hydrone_repro/icra2021/runs/{algorithm}/stage_{stage}/seed_{seed}",
    )
    path = Path(str(value).format(algorithm=algorithm, stage=stage, seed=seed)).expanduser().resolve()
    return path / "gates" / run_suffix if run_suffix else path


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _save_checkpoint(
    agent: Any,
    algorithm: str,
    path: Path,
    stage: int,
    episode: int,
    manifest: Dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = copy.deepcopy(getattr(agent, "last_metrics", {}))
    if algorithm == "sac":
        agent.save_checkpoint(
            str(path), stage=stage, episode=episode, metrics=metrics, manifest=manifest
        )
    else:
        agent.save_checkpoint(
            str(path), manifest=manifest, stage=stage, episode=episode, metrics=metrics
        )


def _run() -> int:
    algorithm = str(rospy.get_param("~algorithm", "ddpg")).lower()
    stage = int(rospy.get_param("~stage", 1))
    mode = str(rospy.get_param("~mode", "train")).lower()
    namespace = "/" + str(rospy.get_param("~namespace", "hydrone_aerial_underwater0")).strip("/")
    config_path = str(rospy.get_param("~config", ""))
    checkpoint_arg = str(rospy.get_param("~checkpoint", ""))
    episode_limit = int(rospy.get_param("~episode_limit", 0))
    step_limit = int(rospy.get_param("~step_limit", 0))
    if algorithm not in ("ddpg", "sac"):
        raise ValueError("algorithm must be ddpg or sac")
    if stage not in (1, 2):
        raise ValueError("stage must be 1 or 2")
    if mode not in ("train", "evaluate", "resume"):
        raise ValueError("mode must be train, evaluate, or resume")
    if episode_limit < 0 or step_limit < 0:
        raise ValueError("episode_limit and step_limit must be non-negative")

    data = _load_config(config_path)
    agent, training = _make_agent(algorithm, data)
    seed = int(training["seed"])
    run_suffix = ""
    if step_limit:
        run_suffix = "step_%d" % step_limit
    elif episode_limit:
        run_suffix = "episode_%d" % episode_limit
    checkpoint_path = _checkpoint_path(
        algorithm, stage, seed, checkpoint_arg, data, run_suffix=run_suffix
    )
    output_dir = _output_dir(
        algorithm, stage, seed, data, run_suffix=run_suffix
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(
        algorithm, stage, mode, namespace, config_path, seed, data, output_dir
    )
    manifest["started_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_json(output_dir / "manifest.json", manifest)

    resume_episode = 0
    if mode in ("evaluate", "resume"):
        _require_file(str(checkpoint_path), "checkpoint")
        try:
            payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        except TypeError:  # pragma: no cover - older torch compatibility
            payload = torch.load(checkpoint_path, map_location="cpu")
        if not isinstance(payload, dict):
            raise ValueError("checkpoint must contain a dictionary")
        resume_episode = int(payload.get("episode", 0)) if mode == "resume" else 0
        agent.load_checkpoint(str(checkpoint_path), map_location="cpu")

    env = PaperEnvironment(config=_environment_config(stage, namespace, data, seed))
    target_episodes = int(data.get("episodes", data.get("max_episodes", 1)))
    max_steps = int(data.get("max_steps", data.get("episode", {}).get("max_steps", 500)))
    if target_episodes < 1 or max_steps < 1:
        raise ValueError("episodes and max_steps must be positive")
    if mode == "resume" and resume_episode >= target_episodes:
        rospy.loginfo("checkpoint already reached target episodes: %d", target_episodes)
        env.close()
        return 0

    if mode == "resume":
        first_episode = resume_episode
        episodes_to_run = target_episodes - resume_episode
    else:
        first_episode = 0
        episodes_to_run = target_episodes
    if episode_limit:
        episodes_to_run = min(episodes_to_run, episode_limit)

    logging = data.get("logging", {})
    moving_window = int(logging.get("moving_average_window", 300))
    checkpoint_interval = int(logging.get("checkpoint_interval_episodes", 10))
    progress_interval_steps = int(logging.get("progress_interval_steps", 100))
    if moving_window < 1 or checkpoint_interval < 1 or progress_interval_steps < 1:
        raise ValueError("logging windows and checkpoint interval must be positive")
    evaluation = data.get("evaluation", {})
    eval_interval = int(evaluation.get("interval_episodes", 0))
    eval_episodes = int(evaluation.get("episodes", 0))
    eval_max_steps = int(evaluation.get("max_steps", max_steps))
    # A stability gate is deliberately training-only; this prevents a periodic
    # evaluation from making the requested environment-step bound ambiguous.
    evaluation_enabled = (
        mode in ("train", "resume")
        and bool(evaluation.get("enabled", True))
        and step_limit == 0
        and eval_interval > 0
        and eval_episodes > 0
    )

    episode_log_path = output_dir / "episodes.jsonl"
    progress_log_path = output_dir / "progress.jsonl"
    reward_history = []
    if mode == "resume" and episode_log_path.is_file():
        with episode_log_path.open("r") as previous_log:
            for line in previous_log:
                try:
                    previous = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if previous.get("kind") == "training":
                    try:
                        reward_history.append(float(previous["reward"]))
                    except (KeyError, TypeError, ValueError):
                        continue
    episode_log = episode_log_path.open("a")
    progress_log = progress_log_path.open("a")
    completed_training_episodes = first_episode
    total_env_steps = 0
    run_wall_start = time.monotonic()
    run_sim_start = _sim_time()
    records = []
    interrupted = False
    failure = None
    summary = None
    next_progress_step = ((agent.global_step // progress_interval_steps) + 1) * progress_interval_steps

    def write_progress(**event: Any) -> None:
        """Emit a bounded heartbeat to ROS logs and a tail-able JSONL file."""

        nonlocal next_progress_step
        if mode == "evaluate" or agent.global_step < next_progress_step:
            return
        now = time.monotonic()
        payload: Dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "algorithm": algorithm,
            "stage": stage,
            "mode": mode,
            "episode": int(episode),
            "completed_training_episodes": int(completed_training_episodes),
            "episode_step": int(event["steps"]),
            "episode_reward": float(event["total_reward"]),
            "last_reward": float(event["reward"]),
            "terminated": bool(event["terminated"]),
            "truncated": bool(event["truncated"]),
            # Keep both counters explicit: ``run_env_steps`` starts at zero
            # for each invocation, while ``global_env_steps`` includes the
            # replay/checkpoint step restored by resume mode.
            "run_env_steps": int(total_env_steps + event["steps"]),
            "global_env_steps": int(agent.global_step),
            "agent_global_step": int(agent.global_step),
            "gradient_updates": int(agent.update_count),
            "wall_elapsed_s": float(now - run_wall_start),
            "steps_per_wall_second": float((total_env_steps + event["steps"]) / max(1.0e-9, now - run_wall_start)),
            "last_update_metrics": copy.deepcopy(getattr(agent, "last_metrics", {})),
        }
        if torch.cuda.is_available():
            payload["gpu_allocated_mib"] = float(torch.cuda.memory_allocated() / 1024.0 ** 2)
            payload["gpu_reserved_mib"] = float(torch.cuda.memory_reserved() / 1024.0 ** 2)
        progress_log.write(json.dumps(payload, sort_keys=True) + "\n")
        progress_log.flush()
        _write_json(output_dir / "progress.json", payload)
        rospy.loginfo(
            "ICRA2021 progress: episode=%d episode_step=%d env_steps=%d/%s updates=%d reward=%.3f last_reward=%.3f rate=%.2f step/s gpu=%s",
            payload["episode"], payload["episode_step"], payload["run_env_steps"],
            target_episodes * max_steps if step_limit == 0 else step_limit,
            payload["gradient_updates"], payload["episode_reward"], payload["last_reward"],
            payload["steps_per_wall_second"],
            "%.1f MiB" % payload["gpu_allocated_mib"] if "gpu_allocated_mib" in payload else "n/a",
        )
        next_progress_step = ((agent.global_step // progress_interval_steps) + 1) * progress_interval_steps

    try:
        for episode in range(first_episode, first_episode + episodes_to_run):
            if rospy.is_shutdown():
                interrupted = True
                break
            if step_limit and total_env_steps >= step_limit:
                break
            remaining_steps = max_steps
            if step_limit:
                remaining_steps = min(max_steps, step_limit - total_env_steps)
            record = _run_episode(
                env, agent, max_steps=remaining_steps, evaluate=(mode == "evaluate"),
                on_step=write_progress,
            )
            record["kind"] = "evaluation" if mode == "evaluate" else "training"
            record["episode"] = episode
            total_env_steps += int(record["steps"])
            if mode != "evaluate":
                reward_history.append(float(record["reward"]))
                record["moving_average_reward"] = moving_average(reward_history, moving_window)
                completed_training_episodes = episode + 1
            records.append(record)
            episode_log.write(json.dumps(record, sort_keys=True) + "\n")
            episode_log.flush()
            rospy.loginfo(
                "ICRA2021 episode complete: episode=%d/%d steps=%d reward=%.3f terminated=%s truncated=%s total_env_steps=%d updates=%d",
                completed_training_episodes if mode != "evaluate" else episode + 1,
                target_episodes, record["steps"], record["reward"],
                record["terminated"], record["truncated"], total_env_steps,
                agent.update_count,
            )

            if mode != "evaluate" and (
                completed_training_episodes % checkpoint_interval == 0
                or (step_limit and total_env_steps >= step_limit)
            ):
                _save_checkpoint(
                    agent, algorithm, checkpoint_path, stage,
                    completed_training_episodes, manifest,
                )

            if evaluation_enabled and completed_training_episodes % eval_interval == 0:
                for evaluation_index in range(eval_episodes):
                    eval_record = _run_episode(
                        env, agent, max_steps=eval_max_steps, evaluate=True
                    )
                    eval_record.update(
                        {
                            "kind": "deterministic_evaluation",
                            "episode": completed_training_episodes,
                            "evaluation_index": evaluation_index,
                        }
                    )
                    records.append(eval_record)
                    episode_log.write(json.dumps(eval_record, sort_keys=True) + "\n")
                    episode_log.flush()
    except KeyboardInterrupt:
        interrupted = True
        rospy.logwarn("ICRA2021 runner interrupted; saving the last completed checkpoint")
    except RuntimeError as exc:
        if not rospy.is_shutdown():
            failure = "%s: %s" % (type(exc).__name__, exc)
            raise
        interrupted = True
        rospy.logwarn(
            "ICRA2021 ROS shutdown interrupted the current episode (%s); "
            "saving the last completed checkpoint",
            exc,
        )
    except Exception as exc:
        failure = "%s: %s" % (type(exc).__name__, exc)
        raise
    finally:
        episode_log.close()
        progress_log.close()
        if mode != "evaluate" and completed_training_episodes > 0:
            try:
                _save_checkpoint(
                    agent, algorithm, checkpoint_path, stage,
                    completed_training_episodes, manifest,
                )
            except Exception as exc:  # preserve the original failure/shutdown path
                rospy.logwarn("unable to save final checkpoint: %s", exc)
        wall_elapsed = max(1.0e-9, time.monotonic() - run_wall_start)
        sim_elapsed = max(0.0, _sim_time() - run_sim_start)
        summary = {
            "algorithm": algorithm,
            "stage": stage,
            "mode": mode,
            "seed": seed,
            "target_episodes": target_episodes,
            "completed_training_episodes": completed_training_episodes,
            "records": len(records),
            "total_env_steps": total_env_steps,
            "wall_elapsed_s": wall_elapsed,
            "sim_elapsed_s": sim_elapsed,
            "rtf": real_time_factor(sim_elapsed, wall_elapsed),
            "checkpoint": str(checkpoint_path) if mode != "evaluate" else None,
            "interrupted": interrupted,
            "status": "failed" if failure else "interrupted" if interrupted else "completed",
            "error": failure,
        }
        _write_json(output_dir / "summary.json", summary)
        env.close()

    rospy.loginfo(
        "ICRA2021 %s/%s completed: training_episodes=%d env_steps=%d checkpoint=%s",
        algorithm, mode, completed_training_episodes, total_env_steps,
        checkpoint_path if mode != "evaluate" else "not written",
    )
    rospy.loginfo("ICRA2021 summary: %s", json.dumps(summary, sort_keys=True))
    return 0


def main() -> int:
    rospy.init_node("icra2021_agent", anonymous=False)
    try:
        return _run()
    except (OSError, TypeError, ValueError, RuntimeError, FloatingPointError) as exc:
        rospy.logfatal("ICRA2021 agent failed: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
