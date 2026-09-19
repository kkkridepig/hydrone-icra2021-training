"""Test failure reporting and bounded execution, not experiment performance."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("local_validation_checks", Path(__file__).resolve().parents[1] / "checks.py")
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class HarnessTests(unittest.TestCase):
    def test_audit_comparison_detects_changed_counts(self):
        self.assertEqual(checks.compare_json({"episodes": 168}, {"episodes": 167}), ["$.episodes"])
        self.assertEqual(checks.compare_json({"x": .1}, {"x": .1 + 1e-17}), [])
        self.assertEqual(checks.compare_json({"x": .1}, {"x": .10001}), ["$.x"])

    def test_manifest_paths_cannot_escape(self):
        for name in ("../source.py", "/source.py", "C:/source.py", "tools\\source.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checks.safe_relative(name)

    def test_empty_suite_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.json"
            self.assertEqual(checks.unit_child(root, "test_*.py", result), 1)
            self.assertEqual(json.loads(result.read_text())["tests_run"], 0)

    def test_test_failures_and_skips_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_harness_sample.py").write_text(
                "import unittest\nclass Example(unittest.TestCase):\n"
                " def test_ok(self): pass\n"
                " @unittest.skip('no optional runtime')\n def test_skip(self): pass\n"
                " def test_failure(self): self.fail('intentional harness failure')\n", encoding="utf-8")
            target = root / "result.json"
            result, _ = checks.run_command(
                [sys.executable, str(Path(checks.__file__)), "--_suite", str(root),
                 "--_pattern", "test_harness_sample.py", "--_result", str(target)],
                root, 10, root / "subprocess.log")
            data = json.loads(target.read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual((data["tests_run"], data["passed"], len(data["skipped"]), len(data["failures"])), (3, 1, 1, 1))

    def test_timeout_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, _ = checks.run_command([sys.executable, "-c", "import time; time.sleep(20)"],
                                            root, 1, root / "timeout.log")
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["status"], "failed")

    def test_existing_output_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "logs"
            output.mkdir()
            (output / "report.json").write_text("original", encoding="utf-8")
            with self.assertRaises(SystemExit) as result:
                checks.main(["--root", str(root), "--output", str(output), "--level", "unit"])
            self.assertEqual(result.exception.code, 2)
            self.assertEqual((output / "report.json").read_text(), "original")

    def test_file_discovery_excludes_ignored_generated_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "logs"
            output.mkdir()
            harness = root / "dev/local_validation"
            generated = harness / "_runs/previous"
            generated.mkdir(parents=True)
            (root / ".gitignore").write_text("dev/local_validation/_runs/\n", encoding="utf-8")
            (harness / "checks.py").write_text("# new source\n", encoding="utf-8")
            (generated / "invalid.json").write_text("not source", encoding="utf-8")
            result, _ = checks.run_command(["git", "init", "--quiet"], root, 10, output / "init.log")
            self.assertEqual(result["status"], "passed")
            names = [name for name, _ in checks.tracked_files(root, output, 10)]
            self.assertIn("dev/local_validation/checks.py", names)
            self.assertNotIn("dev/local_validation/_runs/previous/invalid.json", names)


if __name__ == "__main__":
    unittest.main()
