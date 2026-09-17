"""Archive integrity, path safety, and no-overwrite contracts; no ROS or torch."""
import hashlib
import importlib.util
import io
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("archive_restore", Path(__file__).resolve().parents[1] / "restore.py")
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)


def make_zip(path, name="root/model.pt", data=b"opaque checkpoint bytes"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, data)
    return {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}}


class RestoreTests(unittest.TestCase):
    def test_original_bytes_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "backup.zip"
            members = make_zip(archive)
            self.assertEqual(restore.read_members(archive, members, root / "new", "root/"), 1)
            self.assertEqual((root / "new/model.pt").read_bytes(), b"opaque checkpoint bytes")

    def test_existing_directory_and_file_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for destination in (root, root / "existing.txt"):
                if destination != root:
                    destination.write_bytes(b"keep")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    restore.restore(root, destination)
            self.assertEqual((root / "existing.txt").read_bytes(), b"keep")

    def test_paths_with_traversal_or_windows_aliases_are_rejected(self):
        for name in ("../x", "/x", "a/../x", "a/./x", "a//x", "C:/x", "a\\x", "NUL.txt", "a/x.", "a/x "):
            with self.subTest(name=name), self.assertRaises(ValueError):
                restore.safe_parts(name)

    def test_case_collision_and_file_ancestor_are_rejected(self):
        for names in (("a.pt", "A.pt"), ("x", "x/y")):
            with self.subTest(names=names):
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    for name in names:
                        archive.writestr(name, b"x")
                with zipfile.ZipFile(data) as archive, self.assertRaises(ValueError):
                    restore.member_plan(archive, {})

    def test_symlink_is_rejected_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            member = zipfile.ZipInfo("root/link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(root / "bad.zip", "w") as archive:
                archive.writestr(member, "../../outside")
            with self.assertRaisesRegex(ValueError, "Link/special"):
                restore.read_members(root / "bad.zip", {}, root / "new", "root/")
            self.assertFalse((root / "new").exists())

    def test_missing_or_unexpected_members_are_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_zip(root / "backup.zip")
            with self.assertRaisesRegex(ValueError, "file set"):
                restore.read_members(root / "backup.zip", {}, root / "new", "root/")
            self.assertFalse((root / "new").exists())

    def test_hash_mismatch_fails_and_preserves_partial_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            members = make_zip(root / "backup.zip")
            members["root/model.pt"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                restore.read_members(root / "backup.zip", members, root / "new", "root/")
            self.assertTrue((root / "new/model.pt").is_file())

    def test_lfs_pointer_and_corrupt_container_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "backup.zip"
            path.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
            with self.assertRaisesRegex(ValueError, "git lfs pull"):
                restore.check_container(path, {})
            path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                restore.check_container(path, {"bytes": 7, "sha256": "0" * 64})


if __name__ == "__main__":
    unittest.main()
