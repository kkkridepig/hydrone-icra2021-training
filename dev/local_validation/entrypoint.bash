#!/usr/bin/env bash
set -e
# ROS setup scripts are not guaranteed to tolerate nounset.
source /opt/ros/noetic/setup.bash
if [[ $# -eq 0 ]]; then
  set -- python3 --version
fi
exec "$@"
