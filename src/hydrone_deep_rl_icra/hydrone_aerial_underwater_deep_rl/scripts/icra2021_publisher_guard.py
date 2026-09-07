#!/usr/bin/env python3
"""Fail-fast guard against competing high-level ``cmd_vel`` publishers."""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import rospy

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from hydrone_icra2021.publisher_guard import (  # noqa: E402
    canonical_namespace,
    control_topic,
    master_conflicting_publishers,
)


_SAFE_NAMESPACE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _validate_private_params():
    algorithm = str(rospy.get_param("~algorithm", "ddpg")).lower()
    stage = int(rospy.get_param("~stage", 1))
    mode = str(rospy.get_param("~mode", "train")).lower()
    namespace_raw = str(rospy.get_param("~namespace", "hydrone_aerial_underwater0"))
    namespace = canonical_namespace(namespace_raw)
    config_path = str(rospy.get_param("~config", ""))
    checkpoint_path = str(rospy.get_param("~checkpoint", ""))
    run_agent = bool(rospy.get_param("~run_agent", False))

    if algorithm not in ("ddpg", "sac"):
        raise ValueError("algorithm must be ddpg or sac")
    if stage not in (1, 2):
        raise ValueError("stage must be 1 or 2")
    if mode not in ("train", "evaluate", "resume"):
        raise ValueError("mode must be train, evaluate, or resume")
    if not _SAFE_NAMESPACE.fullmatch(namespace.lstrip("/")):
        raise ValueError(
            "namespace must be a simple ROS name without slashes; "
            "the default is hydrone_aerial_underwater0"
        )
    if config_path and not Path(config_path).expanduser().is_file():
        raise ValueError("config file does not exist: %s" % config_path)
    if run_agent and mode in ("evaluate", "resume"):
        if not checkpoint_path:
            raise ValueError("checkpoint is required for evaluate/resume when run_agent=true")
        if not Path(checkpoint_path).expanduser().is_file():
            raise ValueError("checkpoint file does not exist: %s" % checkpoint_path)
    return {
        "algorithm": algorithm,
        "stage": stage,
        "mode": mode,
        "namespace": namespace,
        "config": config_path,
        "checkpoint": checkpoint_path,
        "run_agent": run_agent,
        "topic": control_topic(namespace),
    }


def main() -> int:
    rospy.init_node("icra2021_publisher_guard", anonymous=False)
    try:
        params = _validate_private_params()
    except (OSError, TypeError, ValueError) as exc:
        rospy.logfatal("ICRA2021 launch contract invalid: %s", exc)
        return 2

    topic = params["topic"]
    node_name = rospy.get_name()
    allowed_nodes = [node_name]
    if params["run_agent"]:
        # The optional agent is the sole intended high-level publisher during
        # train/evaluate/resume; the bridge only subscribes to this topic.
        allowed_nodes.append("/icra2021_agent")

    def check(event=None):
        del event
        try:
            conflicts = master_conflicting_publishers(topic, allowed_nodes=allowed_nodes)
        except Exception as exc:  # master may still be starting during launch
            rospy.logwarn_throttle(5.0, "publisher guard waiting for ROS master: %s", exc)
            return
        if conflicts:
            rospy.logfatal(
                "publisher conflict on %s; refusing to run paper controller: %s",
                topic,
                ", ".join(conflicts),
            )
            rospy.signal_shutdown("conflicting cmd_vel publisher")
            return
        rospy.loginfo_once(
            "ICRA2021 publisher guard passed: %s has no competing publishers "
            "(algorithm=%s stage=%s mode=%s)",
            topic,
            params["algorithm"],
            params["stage"],
            params["mode"],
        )

    # Delay the first check until launch-time publishers have registered, then
    # keep checking so a late-starting legacy policy is also rejected.  A
    # regular Python thread is used because the wrapper intentionally defaults
    # to paused physics and ROS timers would otherwise wait on frozen /clock.
    def poll():
        time.sleep(0.5)
        while not rospy.is_shutdown():
            check()
            time.sleep(1.0)

    threading.Thread(target=poll, name="icra2021_publisher_guard", daemon=True).start()
    rospy.spin()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
