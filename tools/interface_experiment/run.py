#!/usr/bin/env python3
"""Two isolated stages; preserve PPU runtime and every failed experiment."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile

from core import BASE_SHA, canonical_hash, json_write, load_config
from report import make_report

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def collection_source_compatibility(recorded, current):
    """Allow only the reviewed v1 clock-startup repair when reusing v1 data.

    Configuration and every episode hash are checked separately in main.
    Physics, controller, action/observation contracts and learning code must
    still match exactly. The executing runner records its own hash; it is
    the migration verifier, not an additional collection implementation.
    """
    changes = {p: dict(before=recorded.get(p), after=current.get(p))
               for p in sorted(set(recorded) | set(current))
               if recorded.get(p) != current.get(p)}
    if not changes:
        return dict(mode="exact", changes={})
    prefix = "tools/interface_experiment/"
    approved = {
        prefix+"ros_io.py": (
            "20e22059ebadc2b3e041c82b0d8f079ffe14bcafcbbe755acbd32d560175ad20",
            "8efb3c230897e495544dfde5fd06af47b4f816a7386b087a5ab1bdae67ec9725"),
        prefix+"run.py": (
            "35c1a287a9ade18488a6f208eac8e37a2c636a2278aac54c179ad5d77511bd4b",
            sha(Path(__file__))),
        prefix+"tests/test_startup.py": (None, "93c30d42ca7883124dc88e2320dfed8173ffbe6a01cabe27106667a0bea71152"),
    }
    invalid = [p for p, change in changes.items()
               if p not in approved or (change["before"], change["after"]) != approved[p]]
    if invalid:
        raise RuntimeError("Sources/build changed outside the reviewed clock hotfix: "
                           +", ".join(invalid)+". Inspect changes; do not rewrite phase1 provenance.")
    return dict(mode="clock_startup_hotfix_v1", changes=changes,
                scope="Startup clock synchronization only; collection semantics unchanged")


def provenance(c):
    result = dict(base_sha=BASE_SHA, config_sha256=canonical_hash(c),
                  python=sys.version, executable=sys.executable, source_sha256={},
                  created_utc=datetime.now(timezone.utc).isoformat())
    result["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result["git_status"] = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    watched = [HERE, ROOT/"src/hydrone_deep_rl_icra",
               ROOT/"src/rotors_simulator/rotors_control",
               ROOT/"src/rotors_simulator/rotors_gazebo_plugins",
               ROOT/"src/uuv_simulator/uuv_gazebo_plugins",
               ROOT/"src/uuv_simulator/uuv_gazebo_worlds/models/ocean",
               ROOT/"src/uuv_simulator/uuv_gazebo_worlds/Media"]
    for folder in watched:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.suffix in (".py", ".json", ".xacro", ".launch", ".world", ".sdf",
                                            ".yaml", ".cpp", ".cc", ".h", ".hh", ".bash", ".so",
                                            ".dae", ".stl", ".material", ".png", ".jpg", ".vert", ".frag"):
                result["source_sha256"][str(p.relative_to(ROOT))] = sha(p)
    lib = ROOT/"devel/lib"
    for pattern in ("libuuv*so", "librotors*so", "rotors_control/lee_position_controller_node"):
        for p in lib.glob(pattern):
            if p.is_file():
                result["source_sha256"][str(p.relative_to(ROOT))] = sha(p)
    return result


def port_free(port):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise RuntimeError("Port %d is in use. Choose another --port; no process was killed." % port)
    finally:
        sock.close()


def stop_group(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        return
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def preflight(out, c, env):
    for command in ("roslaunch", "rospack", "xacro"):
        if shutil.which(command) is None:
            raise RuntimeError(command+" missing; source the existing PPU/ROS workspace")
    if not (ROOT/"devel/setup.bash").exists():
        raise RuntimeError("Existing workspace has not been built")
    if not Path("/opt/ros/noetic/lib/libgazebo_ros_camera.so").exists():
        raise RuntimeError("libgazebo_ros_camera.so missing (ROS gazebo_plugins); do not reinstall torch")
    expanded = subprocess.run(
        ["xacro", str(HERE/"assets/robot.xacro"), "--inorder",
         "namespace:="+c["namespace"], "debug:=0", "use_simplified_mesh:=false",
         "inertial_reference_frame:=world"], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    (out/"xacro.log").write_text(expanded.stderr)
    if expanded.returncode:
        raise RuntimeError("Xacro expansion failed: " + expanded.stderr[-2000:])
    robot = expanded.stdout
    (out/"expanded_robot.urdf").write_text(robot)
    xml = ET.fromstring(robot)
    plugins = [dict(name=p.get("name"), library=p.get("filename")) for p in xml.iter("plugin")]
    cameras = [s for s in xml.iter("sensor") if s.get("type") == "camera"]
    if len(cameras) != 1:
        raise RuntimeError("Expected exactly one experimental RGB camera")
    mass = sum(float(m.get("value")) for m in xml.findall("./link/inertial/mass"))
    volumes = [float(v.text) for p in xml.iter("plugin")
               if p.get("name") == "uuv_plugin" for v in p.iter("volume")]
    inventory = dict(plugins=plugins, robot_urdf_mass_kg=mass, buoyancy_volumes_m3=volumes,
                     fully_submerged_buoyancy_N=sum(volumes)*1028*9.8,
                     gravity_weight_N=mass*9.8, expanded_robot_sha256=sha(out/"expanded_robot.urdf"),
                     model_profile="unmodified_legacy_hydrone",
                     limitations=["Fossen damping/added mass not automatically gated to air/water",
                                  "Original partial buoyancy discontinuities retained",
                                  "Fixed motor coefficients retained; no validated rotor wetting model",
                                  "Camera rendering has no validated refraction or splashing",
                                  "Operational crossing gate is not physical validation"])
    json_write(out/"physics_inventory.json", inventory)
    return inventory


def simulate(out, c, mode, port, checkpoints=()):
    out.mkdir(parents=True, exist_ok=True)
    port_free(port)
    port_free(port+30)
    env = os.environ.copy()
    env.update(ROS_MASTER_URI="http://127.0.0.1:%d" % port, ROS_IP="127.0.0.1",
               GAZEBO_MASTER_URI="http://127.0.0.1:%d" % (port+30),
               ROS_LOG_DIR=str(out/"ros_logs"), ROS_HOME=str(out/"ros_home"),
               LIBGL_ALWAYS_SOFTWARE="1", PYTHONUNBUFFERED="1")
    (out/"ros_logs").mkdir(exist_ok=True)
    (out/"ros_home").mkdir(exist_ok=True)
    json_write(out/"config.json", c)
    preflight(out, c, env)
    launch = ["roslaunch", "--port", str(port), str(HERE/"assets/simulation.launch"),
              "assets:="+str(HERE/"assets"), "namespace:="+c["namespace"]]
    if not env.get("DISPLAY"):
        if shutil.which("xvfb-run") is None or shutil.which("xauth") is None:
            raise RuntimeError("RGB camera requires xvfb-run and xauth; use the documented Xvfb/Mesa packages")
        launch = ["xvfb-run", "-a", "-s", "-screen 0 640x480x24"]+launch
    worker = [sys.executable, str(HERE/"worker.py"), "--mode", mode,
              "--config", str(out/"config.json"), "--output", str(out)]
    for p in checkpoints:
        worker += ["--checkpoint", str(p)]
    sim = agent = None
    deadline = time.monotonic()+c["max_wall_hours"]*3600
    try:
        with (out/"gazebo.log").open("w") as simlog, (out/"worker.log").open("w") as runlog:
            sim = subprocess.Popen(launch, env=env, stdout=simlog, stderr=subprocess.STDOUT,
                                   start_new_session=True)
            agent = subprocess.Popen(worker, env=env, stdout=runlog, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            while agent.poll() is None:
                if sim.poll() is not None:
                    raise RuntimeError("Simulation launcher exited; inspect gazebo.log")
                if time.monotonic() > deadline:
                    raise TimeoutError("Simulation exceeded configured wall-time bound")
                if time.time()-(out/"worker.log").stat().st_mtime > 300:
                    raise TimeoutError("No episode/startup progress for 300 wall seconds; inspect worker.log")
                time.sleep(0.5)
            if agent.returncode != 0:
                raise RuntimeError("Worker failed (%d); inspect worker.log and REPORT.md" % agent.returncode)
    finally:
        stop_group(agent)
        stop_group(sim)


def bundle(out):
    """Portable evidence; large training arrays and weights remain on server."""
    destination = out/"evidence.zip"
    with zipfile.ZipFile(str(destination), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob("*")):
            if not path.is_file() or "ros_logs" in path.parts or "ros_home" in path.parts:
                continue
            if path.suffix not in (".json", ".jsonl", ".md", ".txt", ".log", ".png", ".urdf"):
                continue
            archive.write(str(path), str(path.relative_to(out)))
    return destination


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["phase1", "phase2"])
    p.add_argument("--config", type=Path, default=HERE/"config.json")
    p.add_argument("--data", type=Path, help="Successful phase1 directory; mandatory for phase2")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--port", type=int, default=11331)
    p.add_argument("--device", default="cuda:0", choices=["cuda:0", "cpu"])
    a = p.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if a.phase == "phase2" and a.data is None:
        p.error("phase2 requires --data")
    if not 1024 <= a.port <= 65505:
        p.error("port must be 1024..65505")
    c = load_config(a.config)
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    json_write(out/"config.json", c)
    print("OUTPUT="+str(out), flush=True)
    print("Logs: "+str(out/("worker.log" if a.phase == "phase1" else "evaluation/worker.log")), flush=True)
    error = None
    try:
        manifest = provenance(c)
        manifest.update(phase=a.phase, port=a.port, requested_training_device=a.device,
                        collection=str(a.data.resolve()) if a.data else None)
        json_write(out/"provenance.json", manifest)
        if a.phase == "phase2":
            data = a.data.resolve()
            from report import collection_gate
            if canonical_hash(load_config(data/"config.json")) != canonical_hash(c):
                raise RuntimeError("Phase1/config mismatch; use the exact original config")
            if not collection_gate(data, c)["passed"]:
                raise RuntimeError("Phase1 feasibility gate failed; inspect its REPORT.md")
            source = json.loads((data/"provenance.json").read_text())
            manifest["collection_source_compatibility"] = collection_source_compatibility(
                source["source_sha256"], manifest["source_sha256"])
            print("COLLECTION_COMPATIBILITY="+manifest["collection_source_compatibility"]["mode"],
                  flush=True)
            manifest["collection_sha256"] = {
                str(path.relative_to(data)): sha(path) for path in sorted((data/"episodes").glob("*"))
                if path.is_file()}
            recorded = json.loads((data/"dataset_manifest.json").read_text())
            if recorded != manifest["collection_sha256"]:
                raise RuntimeError("Collected episode files changed after phase1; preserve original evidence")
            json_write(out/"provenance.json", manifest)
        if a.phase == "phase1":
            from learning import accelerator_check
            json_write(out/"accelerator.json", accelerator_check(c, a.device))
            simulate(out, c, "collect", a.port)
        else:
            from learning import fit
            model_paths = []
            for seed in c["model_seeds"]:
                model_dir = out/"models"/("seed_%d" % seed)
                fit(a.data.resolve(), model_dir, c, seed, a.device)
                model_paths.append(model_dir/"model.pt")
            simulate(out/"evaluation", c, "evaluate", a.port, model_paths)
    except BaseException as exc:
        error = repr(exc)
        (out/"ERROR.txt").write_text(traceback.format_exc())
        print("ERROR: "+error, file=sys.stderr, flush=True)
    finally:
        if a.phase == "phase1":
            json_write(out/"dataset_manifest.json", {
                str(path.relative_to(out)): sha(path) for path in sorted((out/"episodes").glob("*"))
                if path.is_file()})
        report = make_report(out, c, a.phase, error)
        evidence = bundle(out)
        print("OUTPUT="+str(out), flush=True)
        print("REPORT="+str(out/"REPORT.md"), flush=True)
        print("DIAGNOSIS="+report["diagnosis"], flush=True)
        print("EVIDENCE="+str(evidence), flush=True)
    if error or report["diagnosis"] in ("INCOMPLETE", "BLOCKED_BY_FEASIBILITY_GATE"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
