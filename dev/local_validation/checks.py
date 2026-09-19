#!/usr/bin/env python3
"""Bounded, read-only L0/L1 checks. Never starts ROS, Gazebo or training.

Only this harness's output directory and unit-test temporary directories are
written. Synthetic fixtures remain software tests, never experiment evidence.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import time
import tokenize
import unittest
import xml.etree.ElementTree as ET


ICRA_TESTS = "src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/tests/icra2021"
PLAIN_ICRA = (
    "test_action_scaling.py", "test_contracts.py", "test_environment_logic.py",
    "test_goal_sampling.py", "test_publisher_guard.py", "test_runner_utils.py",
)
TORCH_ICRA = (
    "test_checkpoint_roundtrip.py", "test_ddpg.py", "test_network_shapes.py",
    "test_sac.py",
)
LEGACY_PYTHON2 = (
    "src/rotors_simulator/rotors_evaluation/src/rosbag_tools/analyze_bag.py",
    "src/uuv_simulator/uuv_tutorials/uuv_tutorial_dp_controller/scripts/tutorial_dp_controller.py",
)
MANIFEST_DIR = "docs/experiments/2026-09-17/package-manifests"


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def safe_relative(path):
    value = PurePosixPath(path)
    if value.is_absolute() or not value.parts or ".." in value.parts or "\\" in path or ":" in path:
        raise ValueError("Unsafe manifest path: " + path)
    return value


def expected_source_hashes(root):
    """Apply documented delivery precedence; never apply package patches."""
    expected, overlays = {}, []
    for filename, field in (
        ("hydrone-interface-pilot-v1.json", "files_sha256"),
        ("hydrone-phase2-clockfix-v1.json", "files_sha256"),
        ("hydrone-interface-diagnostic-v1.json", "sha256"),
    ):
        data = json.loads((root / MANIFEST_DIR / filename).read_text(encoding="utf-8"))
        for name, digest in data[field].items():
            safe_relative(name)
            if name.endswith(".patch"):
                continue  # Original patches are archival, not deployed files.
            if not name.startswith("tools/interface_") or len(digest) != 64:
                raise ValueError("Invalid package hash entry: " + name)
            int(digest, 16)
            if name in expected:
                overlays.append({"path": name, "before": expected[name], "after": digest,
                                 "package": filename})
            expected[name] = digest
    inventory = json.loads((root / "docs/experiments/2026-09-17/original-zip-inventory.json").read_text(encoding="utf-8"))
    expected["tools/interface_analysis/audit.py"] = inventory["source_deliveries"][
        "hydrone-diagnostic-audit-20260917"]["files"]["audit.py"]["sha256"]
    return expected, overlays


def compare_json(left, right, path="$", mismatches=None):
    """Compare stored audit values; tolerate only floating-point roundoff."""
    if mismatches is None:
        mismatches = []
    if isinstance(left, float) and isinstance(right, (float, int)):
        if not math.isfinite(left) or not math.isfinite(right) or not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12):
            mismatches.append(path)
    elif type(left) is not type(right):
        mismatches.append(path)
    elif isinstance(left, dict):
        if set(left) != set(right):
            mismatches.append(path + ".keys")
        for key in sorted(set(left) & set(right)):
            compare_json(left[key], right[key], path + "." + key, mismatches)
    elif isinstance(left, list):
        if len(left) != len(right):
            mismatches.append(path + ".length")
        for index, (a, b) in enumerate(zip(left, right)):
            compare_json(a, b, "%s[%d]" % (path, index), mismatches)
    elif left != right:
        mismatches.append(path)
    return mismatches


def run_command(command, cwd, timeout, log):
    """Run a bounded child; on Linux terminate its whole process group."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    started = time.monotonic()
    result = {"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout,
              "log": str(log.name), "timed_out": False}
    try:
        process = subprocess.Popen(command, cwd=str(cwd), env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=(os.name == "posix"))
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                output, _ = process.communicate(timeout=5)
        result["returncode"] = process.returncode
        result["status"] = "passed" if process.returncode == 0 and not result["timed_out"] else "failed"
    except OSError as error:
        output = (type(error).__name__ + ": " + str(error) + "\n").encode("utf-8")
        result.update(status="failed", returncode=None, error=str(error))
    log.write_bytes(output)
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result, output.decode("utf-8", errors="replace")


def unit_child(directory, pattern, result_path):
    """Fresh interpreter for every suite avoids duplicate module-name imports."""
    suite = unittest.TestLoader().discover(str(directory), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    data = {"tests_run": result.testsRun,
            "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
                      - len(result.expectedFailures) - len(result.unexpectedSuccesses),
            "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
            "failures": [{"test": test.id(), "traceback": value} for test, value in result.failures],
            "errors": [{"test": test.id(), "traceback": value} for test, value in result.errors],
            "expected_failures": [test.id() for test, _ in result.expectedFailures],
            "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
            "successful": result.wasSuccessful() and result.testsRun > 0}
    write_json(result_path, data)
    return 0 if data["successful"] else 1


def tracked_files(root, output, timeout):
    git = ["git", "-c", "safe.directory=" + str(root)]
    result, _ = run_command(git + ["ls-files", "-z"], root, timeout, output / "git-files.log")
    if result["status"] != "passed":
        raise RuntimeError("git ls-files failed; see git-files.log")
    names = set((output / "git-files.log").read_bytes().decode("utf-8").split("\0")) - {""}
    # Include new harness files before commit, while honoring ignored _runs and
    # caches. Walking this directory would accidentally parse previous reports.
    result, _ = run_command(
        git + ["ls-files", "--others", "--exclude-standard", "-z", "--",
         "dev/local_validation", "docs/experiments/LOCAL_VALIDATION.md"],
        root, timeout, output / "git-new-files.log")
    if result["status"] != "passed":
        raise RuntimeError("git ls-files for new harness files failed; see git-new-files.log")
    names.update(set((output / "git-new-files.log").read_bytes().decode("utf-8").split("\0")) - {""})
    files = []
    for name in sorted(names):
        safe_relative(name)
        path = root / name
        if not path.is_file():
            raise RuntimeError("Tracked file missing: " + name)
        files.append((name, path))
    return files


def core_python(name):
    return (name.startswith(("tools/", "dev/local_validation/")) or
            "/src/hydrone_icra2021/" in name or "/tests/icra2021/" in name or
            "/scripts/icra2021_" in name)


def static_checks(root, output, timeout, bash):
    checks = []
    runtime38 = sys.version_info[:2] == (3, 8)
    checks.append({"name": "python_runtime", "status": "passed" if runtime38 else "failed",
                   "actual": sys.version, "required": "Python 3.8",
                   "note": "Other runtimes provide grammar-only evidence, not Python 3.8 execution."})
    files = tracked_files(root, output, timeout)
    errors, count, core_count = [], 0, 0
    for name, path in files:
        if path.suffix != ".py":
            continue
        count += 1
        core_count += int(core_python(name))
        try:
            with tokenize.open(str(path)) as stream:
                source = stream.read()
            if runtime38:
                compile(source, name, "exec", ast.PyCF_ONLY_AST)
            else:
                ast.parse(source, filename=name, feature_version=(3, 8))
        except (SyntaxError, UnicodeError, ValueError) as error:
            errors.append({"path": name, "error": str(error), "core": core_python(name),
                           "known_legacy_python2": name in LEGACY_PYTHON2})
    checks.append({"name": "python_all", "status": "failed" if errors else "passed",
                   "checked": count, "errors": errors,
                   "mode": "Python 3.8 AST compile" if runtime38 else "Python 3.8 grammar on another runtime"})
    core_errors = [item for item in errors if item["core"]]
    checks.append({"name": "python_core", "status": "failed" if core_errors else "passed",
                   "checked": core_count, "errors": core_errors})
    for index, (name, _) in enumerate(item for item in files if item[1].suffix in (".bash", ".sh")):
        result, _ = run_command([bash, "-n", name], root, timeout, output / ("bash-%03d.log" % index))
        checks.append(dict(result, name="bash_syntax", path=name))
    result, _ = run_command(["git", "-c", "safe.directory=" + str(root), "diff", "--check", "HEAD"],
                            root, timeout, output / "git-diff.log")
    checks.append(dict(result, name="git_diff_check"))

    parsers = {"json": (".json",), "xml": (".xml", ".launch", ".xacro", ".urdf", ".world", ".sdf", ".test"),
               "yaml": (".yaml", ".yml")}
    for kind, suffixes in parsers.items():
        checked, failures, unavailable = 0, [], None
        if kind == "yaml":
            try:
                import yaml
            except ImportError:
                unavailable = "PyYAML unavailable; configuration parsing not executed"
        if not unavailable:
            for name, path in files:
                if path.suffix not in suffixes:
                    continue
                checked += 1
                try:
                    if kind == "xml":
                        ET.parse(str(path))
                    elif kind == "json":
                        json.loads(path.read_text(encoding="utf-8"))
                    else:
                        list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
                except Exception as error:
                    failures.append({"path": name, "error": str(error)})
        checks.append({"name": kind + "_syntax", "status": "failed" if failures or unavailable else "passed",
                       "checked": checked, "errors": failures, "unavailable": unavailable})

    package_names, package_errors = {}, []
    for name, path in files:
        if path.name != "package.xml":
            continue
        try:
            element = ET.parse(str(path)).getroot()
            package = element.findtext("name")
            if element.tag != "package" or not package or not element.findtext("version"):
                raise ValueError("Missing package root/name/version")
            if package in package_names:
                raise ValueError("Duplicate package name: " + package)
            package_names[package] = name
        except Exception as error:
            package_errors.append({"path": name, "error": str(error)})
    checks.append({"name": "ros_package_manifests", "status": "failed" if package_errors else "passed",
                   "checked": len(package_names), "errors": package_errors})
    try:
        expected, overlays = expected_source_hashes(root)
        mismatches = []
        for name, wanted in expected.items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            if actual != wanted:
                mismatches.append({"path": name, "expected": wanted, "actual": actual})
        checks.append({"name": "original_delivery_source_hashes", "status": "failed" if mismatches else "passed",
                       "checked": len(expected), "overlays": overlays, "errors": mismatches,
                       "note": "Pilot then clockfix then diagnostic; hashes monitor final raw bytes, not historical build binaries."})
    except Exception as error:
        checks.append({"name": "original_delivery_source_hashes", "status": "failed", "error": str(error)})
    return checks


def unit_checks(root, output, timeout, cpu_torch):
    checks = []
    torch_available = importlib.util.find_spec("torch") is not None
    if not cpu_torch and torch_available:
        return [{"name": "torch_isolation", "status": "failed",
                 "error": "torch is installed but --cpu-torch was not selected; use the default no-torch image or explicit CPU-only image."}]
    if cpu_torch:
        probe = ("import json,torch; print(json.dumps({'version':torch.__version__,'path':torch.__file__,"
                 "'cuda':torch.version.cuda,'hip':getattr(torch.version,'hip',None)})); "
                 "assert '+cpu' in torch.__version__ and torch.version.cuda is None "
                 "and getattr(torch.version,'hip',None) is None and not torch.cuda.is_available(), 'Not a CPU-only torch build'")
        result, _ = run_command([sys.executable, "-c", probe], root, timeout, output / "cpu-torch-probe.log")
        checks.append(dict(result, name="cpu_torch_isolation"))
        if result["status"] != "passed":
            return checks
    suites = [("interface_experiment", "tools/interface_experiment/tests", "test_*.py"),
              ("interface_diagnostic", "tools/interface_diagnostic/tests", "test_*.py"),
              ("interface_archive", "tools/interface_archive/tests", "test_*.py")]
    suites.extend(("icra_" + name[:-3], ICRA_TESTS, name) for name in PLAIN_ICRA)
    if cpu_torch:
        suites.extend(("icra_" + name[:-3], ICRA_TESTS, name) for name in TORCH_ICRA)
    if (root / "dev/local_validation/tests").is_dir():
        suites.append(("validation_harness", "dev/local_validation/tests", "test_*.py"))
    for name, directory, pattern in suites:
        data_path = output / (name + ".json")
        command = [sys.executable, str(Path(__file__).resolve()), "--_suite", str(root / directory),
                   "--_pattern", pattern, "--_result", str(data_path)]
        result, _ = run_command(command, root, timeout, output / (name + ".log"))
        result["name"] = name
        if data_path.exists():
            result["unittest"] = json.loads(data_path.read_text(encoding="utf-8"))
        else:
            result["status"] = "failed"
            result["error"] = "No unittest result; see subprocess log (startup failure or timeout)."
        checks.append(result)
    checks.append({"name": "not_selected", "status": "skipped",
                   "items": ["ICRA test_agent_runner.py: requires ROS imports and CPU torch; reserved for L2"],
                   "cpu_torch_files_not_selected": [] if cpu_torch else list(TORCH_ICRA)})
    try:
        old = root / "docs/experiments/2026-09-17/audit-original/audit.json"
        new = root / "docs/experiments/2026-09-17/audit-original-zip-recomputed.json"
        mismatches = compare_json(json.loads(old.read_text(encoding="utf-8")),
                                  json.loads(new.read_text(encoding="utf-8")))
        checks.append({"name": "stored_audit_consistency", "status": "failed" if mismatches else "passed",
                       "mismatches": mismatches,
                       "note": "Read-only comparison of two historical audit summaries, not a new evidence ZIP audit or Gazebo run."})
    except Exception as error:
        checks.append({"name": "stored_audit_consistency", "status": "failed", "error": str(error)})
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--level", choices=("static", "unit"))
    parser.add_argument("--cpu-torch", action="store_true")
    parser.add_argument("--timeout", type=int, default=120, help="Per-child timeout in seconds (1..600)")
    parser.add_argument("--bash", default="bash")
    parser.add_argument("--_suite", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_pattern", help=argparse.SUPPRESS)
    parser.add_argument("--_result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._suite:
        return unit_child(args._suite, args._pattern, args._result)
    if not args.root or not args.output or not args.level:
        parser.error("--root, --output and --level are required")
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    root, output = args.root.resolve(), args.output.resolve()
    if output == root or any((root / folder) == output or (root / folder) in output.parents
                             for folder in ("src", "tools", "provenance", "archives", "docs", "dev")):
        parser.error("Output must not overwrite source or archived evidence directories; use logs/local_validation/<new-run>")
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be empty; existing results are preserved")
    output.mkdir(parents=True, exist_ok=True)
    try:
        checks = (static_checks(root, output, args.timeout, args.bash) if args.level == "static" else
                  unit_checks(root, output, args.timeout, args.cpu_torch))
    except Exception as error:
        checks = [{"name": "harness_error", "status": "failed", "error": type(error).__name__ + ": " + str(error)}]
    tests = [item["unittest"] for item in checks if "unittest" in item]
    totals = {"tests_run": sum(item["tests_run"] for item in tests),
              "passed": sum(item["passed"] for item in tests),
              "skipped": sum(len(item["skipped"]) for item in tests),
              "failures": sum(len(item["failures"]) for item in tests),
              "errors": sum(len(item["errors"]) for item in tests),
              "expected_failures": sum(len(item["expected_failures"]) for item in tests),
              "unexpected_successes": sum(len(item["unexpected_successes"]) for item in tests)}
    passed = not any(item["status"] == "failed" for item in checks)
    report = {"schema_version": 1, "level": "L0" if args.level == "static" else "L1",
              "root": str(root), "python": sys.version, "executable": sys.executable,
              "cpu_torch_opt_in": args.cpu_torch, "status": "passed" if passed else "failed",
              "unittest_totals": totals, "checks": checks,
              "server_validation": "NOT EXECUTED: PPU runtime, vendor drivers and formal experiments remain L3 server-only."}
    write_json(output / "report.json", report)
    print(json.dumps({"status": report["status"], "report": str(output / "report.json"),
                      "unittest_totals": totals}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
