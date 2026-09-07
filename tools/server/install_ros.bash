#!/usr/bin/env bash
# Stage 1 only: install system dependencies; no build or simulation.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
unset PYTHONHOME PYTHONPATH CONDA_PREFIX CONDA_DEFAULT_ENV
project_ws="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ $EUID -eq 0 ]]; then
  sudo() { "$@"; }
fi
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 20.04 ]] || {
  echo 'This installer requires Ubuntu 20.04.' >&2; exit 1;
}
mkdir -p "$project_ws/logs/environment"
log_file="$project_ws/logs/environment/install-noetic-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$log_file") 2>&1
trap 'echo "Installation stopped at line $LINENO. Log: $log_file"' ERR
echo "Installation log: $log_file"
if [[ $EUID -ne 0 ]]; then sudo -v; fi
# Dedicated source and key; existing disabled ROS/ROS2 sources stay disabled.
key_file=/usr/share/keyrings/hydrone-ros-archive-keyring.gpg
source_file=/etc/apt/sources.list.d/hydrone-ros-noetic.list
source_line="deb [signed-by=$key_file] https://mirrors.tuna.tsinghua.edu.cn/ros/ubuntu/ focal main"
previous_source_line="deb [signed-by=$key_file] https://packages.ros.org/ros/ubuntu focal main"
if [[ -e "$source_file" ]] && [[ "$(cat "$source_file")" != "$source_line" ]]; then
  if [[ "$(cat "$source_file")" == "$previous_source_line" ]]; then
    sudo cp -a "$source_file" "${source_file}.backup-$(date +%Y%m%d-%H%M%S)"
  else
    echo "Existing source differs; inspect $source_file before continuing." >&2
    exit 1
  fi
fi
sudo install -m 0644 "$script_dir/../environment/ros-archive-keyring.gpg" "$key_file"
printf '%s\n' "$source_line" | sudo tee "$source_file"
# The migrated machine omitted focal-updates, leaving newer installed runtime
# libraries without matching development packages. Add it separately.
updates_file=/etc/apt/sources.list.d/hydrone-ubuntu-updates.list
updates_line='deb https://mirrors.ustc.edu.cn/ubuntu/ focal-updates main restricted universe multiverse'
if [[ -e "$updates_file" ]] && [[ "$(cat "$updates_file")" != "$updates_line" ]]; then
  echo "Existing source differs; inspect $updates_file before continuing." >&2
  exit 1
fi
printf '%s\n' "$updates_line" | sudo tee "$updates_file"
sudo apt-get update
# Ubuntu's old monolithic package owns files now supplied by ROS's modules
# package. APT's dependency simulation cannot detect this file overlap and
# orders modules first. Unpack the verified ROS wrapper first to release those
# old files, then let APT install modules and configure the pending transaction.
catkin_pkg_version="$(dpkg-query -W -f='${Version}' python3-catkin-pkg 2>/dev/null || true)"
if [[ "$catkin_pkg_version" == '0.4.16-1' ]]; then
  wrapper_deb="$script_dir/../environment/python3-catkin-pkg_1.0.0-100_all.deb"
  printf '%s  %s\n' \
    df38f24e396c6664f2759fb4b9bd18e9c7a61e2b35450e787807a28ef3be5b91 \
    "$wrapper_deb" | sha256sum --check
  sudo dpkg --unpack "$wrapper_deb"
fi
apt-get --simulate --fix-broken --no-remove install python3-catkin-pkg python3-catkin-pkg-modules
sudo apt-get --fix-broken --no-remove install -y python3-catkin-pkg python3-catkin-pkg-modules
base_packages=(
  ca-certificates curl gnupg build-essential cmake git \
  python3-catkin-pkg python3-catkin-tools python3-rosdep python3-vcstool \
  python3-venv python3-dev python3-pip \
  ros-noetic-desktop-full ros-noetic-gazebo-ros-control \
  ros-noetic-xacro ros-noetic-robot-state-publisher \
  ros-noetic-joint-state-publisher ros-noetic-joy \
  ros-noetic-mavros-msgs ros-noetic-geographic-msgs \
  ros-noetic-octomap-msgs libgoogle-glog-dev ros-noetic-mavros \
  ros-noetic-octomap-ros ros-noetic-octomap python3-scipy xvfb libgl1-mesa-dri
)
# Resolve the whole transaction before downloading or installing packages.
apt-get --simulate --no-remove install "${base_packages[@]}"
sudo apt-get install --no-remove -y "${base_packages[@]}"
test -f /opt/ros/noetic/setup.bash
/usr/bin/python3 --version
dpkg-query -W ros-noetic-desktop-full gazebo11 python3-catkin-tools
echo 'ROS_INSTALL_OK: Run tools/server/build.bash next.'
