#!/usr/bin/env bash
set -eo pipefail
server_ws="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$server_ws"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH GAZEBO_PLUGIN_PATH GAZEBO_MODEL_PATH
source /opt/ros/noetic/setup.bash
if [[ -f "$server_ws/devel/setup.bash" ]]; then source "$server_ws/devel/setup.bash"; fi
catkin config --extend /opt/ros/noetic --link-devel --cmake-args -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3
catkin build --jobs "${HYDRONE_BUILD_JOBS:-2}"
