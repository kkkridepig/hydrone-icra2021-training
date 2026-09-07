"""Single-controller publisher guard for the paper launch wrappers.

The guard inspects the ROS master publisher registry rather than relying on
topic names alone.  It is intentionally ROS-free at import time so the topic
matching logic can be unit tested without a running master.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple


def canonical_topic(topic: str) -> str:
    """Return a ROS topic with one leading slash and no trailing slash."""

    value = "/" + str(topic).strip("/")
    return value if value != "/" else value


def canonical_namespace(namespace: str) -> str:
    """Return a namespace suitable for topic construction."""

    value = "/" + str(namespace).strip("/")
    if value == "/":
        raise ValueError("namespace must not be empty")
    return value


def control_topic(namespace: str, suffix: str = "cmd_vel") -> str:
    """Build the high-level control topic used by the paper environment."""

    suffix_value = str(suffix).strip("/")
    if not suffix_value:
        raise ValueError("control topic suffix must not be empty")
    return canonical_topic(canonical_namespace(namespace) + "/" + suffix_value)


def conflicting_publishers(
    publisher_state: Sequence[Tuple[str, Iterable[str]]],
    topic: str,
    allowed_nodes: Iterable[str] = (),
) -> List[str]:
    """Return publisher node names on ``topic`` not present in allow-list.

    ``publisher_state`` is the first item returned by ROS master's
    ``getSystemState``: ``[(topic, [node, ...]), ...]``.
    """

    target = canonical_topic(topic)
    allowed = {str(node) for node in allowed_nodes}
    conflicts = []
    for registered_topic, nodes in publisher_state:
        if canonical_topic(registered_topic) != target:
            continue
        for node in nodes:
            node_name = str(node)
            if node_name not in allowed and node_name not in conflicts:
                conflicts.append(node_name)
    return conflicts


def master_conflicting_publishers(topic: str, allowed_nodes: Iterable[str] = ()) -> List[str]:
    """Query the ROS master and return conflicting publisher node names."""

    import rospy  # imported only in a sourced ROS runtime

    result = rospy.get_master().getSystemState()
    # rosgraph's XML-RPC wrapper returns ``(code, message, value)``; the
    # publisher registry is the third item and its first component is the
    # publisher list.
    if len(result) != 3 or result[0] != 1:
        raise RuntimeError("ROS master getSystemState failed: %r" % (result,))
    return conflicting_publishers(result[2][0], topic, allowed_nodes=allowed_nodes)
