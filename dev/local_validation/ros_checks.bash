#!/usr/bin/env bash
set -eo pipefail
mode="${1:-imports}"
mkdir -p "$HOME" /results
python3 - <<'PY'
import sys
import rospy, roslib, roslaunch, rospkg
from gazebo_msgs.srv import GetWorldProperties
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image, LaserScan
from geometry_msgs.msg import Twist
assert sys.version_info[:2] == (3, 8), sys.version
print('ROS_IMPORTS_OK Python=' + sys.version.split()[0])
PY
if [[ "$mode" == unit ]]; then
  python3 - <<'PY'
import torch
assert torch.__version__.endswith('+cpu') and torch.version.cuda is None
assert not torch.cuda.is_available()
PY
  exec timeout --signal=TERM --kill-after=5s 120s python3 \
    /repo/dev/local_validation/checks.py \
    --_suite /repo/src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/tests/icra2021 \
    --_pattern test_agent_runner.py --_result /results/agent-runner.json
fi
[[ "$mode" == build ]] || exit 0
mkdir -p /tmp/hydrone-local-catkin
cd /tmp/hydrone-local-catkin
python3 - <<'PY'
import hashlib, json, os, shutil
from pathlib import Path
root = Path('/repo/src')
manifest = {str(p.relative_to(root)): {'symlink': os.readlink(str(p))} if p.is_symlink()
            else {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(root.rglob('*')) if p.is_symlink() or p.is_file()}
Path('/results/source-before.json').write_text(json.dumps(manifest, indent=2) + '\n')
shutil.copytree(str(root), '/tmp/hydrone-local-catkin/src', symlinks=True)
PY
catkin config --workspace "$PWD" --source-space "$PWD/src" \
  --build-space "$PWD/build" --devel-space "$PWD/devel" --log-space /results/catkin-logs \
  --extend /opt/ros/noetic --link-devel \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3
# Compilation is bounded, with two workers to fit a laptop; no training launch.
set +e
timeout --signal=TERM --kill-after=15s 30m catkin build --workspace "$PWD" --jobs 2 --parallel-packages 2 --no-status
build_status=$?
set -e
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
def identities(root):
    return {str(p.relative_to(root)): {'symlink': os.readlink(str(p))} if p.is_symlink()
            else {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(root.rglob('*')) if p.is_symlink() or p.is_file()}
before = json.loads(Path('/results/source-before.json').read_text())
after = identities(Path('/tmp/hydrone-local-catkin/src'))
originals_match = identities(Path('/repo/src')) == before
report = {'original_repository_unchanged': originals_match,
          'reason': 'Existing RotorS build rules generate SDF inside their source tree; only the temporary copy is writable.',
          'temporary_source_changes': {name: {'before': before.get(name), 'after': after.get(name)}
                                       for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)}}
Path('/results/source-build-effects.json').write_text(json.dumps(report, indent=2) + '\n')
assert originals_match, 'Read-only repository identity unexpectedly changed'
print('SOURCE_COPY_IDENTITIES_OK temporary_changes=' + str(len(report['temporary_source_changes'])))
PY
[[ "$build_status" == 0 ]] || exit "$build_status"
source "$PWD/devel/setup.bash"
python3 - <<'PY'
from mav_msgs.msg import Actuators
from uuv_gazebo_ros_plugins_msgs.srv import GetFloat, SetFloat
from uuv_control_msgs.msg import TrajectoryPoint
print('PROJECT_GENERATED_MESSAGES_OK')
PY
roslaunch --nodes hydrone_aerial_underwater_deep_rl icra2021_simulation.launch gui:=false paused:=true > /results/project-launch-nodes.txt
timeout --signal=TERM --kill-after=5s 45s xacro \
  /repo/tools/interface_experiment/assets/robot.xacro \
  namespace:=hydrone_local_smoke > /results/interface-robot.urdf
python3 - <<'PY'
import xml.etree.ElementTree as ET
assert ET.parse('/results/interface-robot.urdf').getroot().tag == 'robot'
print('PROJECT_XACRO_OK')
PY
python3 /repo/dev/local_validation/ros_smoke.py --output-dir /results/gazebo
