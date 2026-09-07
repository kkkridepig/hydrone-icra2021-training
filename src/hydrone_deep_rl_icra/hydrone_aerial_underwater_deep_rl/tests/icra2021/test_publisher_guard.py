import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.publisher_guard import (  # noqa: E402
    canonical_namespace,
    canonical_topic,
    conflicting_publishers,
    control_topic,
)


class PublisherGuardTests(unittest.TestCase):
    def test_topic_canonicalization(self):
        self.assertEqual(canonical_namespace("/hydrone_aerial_underwater0/"), "/hydrone_aerial_underwater0")
        self.assertEqual(canonical_topic("cmd_vel"), "/cmd_vel")
        self.assertEqual(
            control_topic("/hydrone_aerial_underwater0/"),
            "/hydrone_aerial_underwater0/cmd_vel",
        )

    def test_conflict_filter_allows_guard_and_reports_other_nodes(self):
        state = [
            ("/hydrone_aerial_underwater0/cmd_vel", ["/icra2021_publisher_guard", "/legacy_nav"]),
            ("/other/cmd_vel", ["/unrelated"]),
        ]
        conflicts = conflicting_publishers(
            state,
            "/hydrone_aerial_underwater0/cmd_vel",
            allowed_nodes=("/icra2021_publisher_guard",),
        )
        self.assertEqual(conflicts, ["/legacy_nav"])

    def test_nonmatching_topic_has_no_conflict(self):
        state = [("/hydrone_aerial_underwater0/cmd_vel", ["/legacy_nav"])]
        self.assertEqual(
            conflicting_publishers(state, "/hydrone_aerial_underwater0/scan"), []
        )


if __name__ == "__main__":
    unittest.main()

