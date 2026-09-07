# PPU: inherit the image's vendor runtime; do not clear its library paths.
hydrone_server_ws="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
[[ -f "$hydrone_server_ws/.venvs/hydrone-ppu/bin/activate" ]] || { echo 'Run tools/server/create_ppu_env.bash first.' >&2; return 1; }
source "$hydrone_server_ws/.venvs/hydrone-ppu/bin/activate" || return
source /opt/ros/noetic/setup.bash || return
source "$hydrone_server_ws/devel/setup.bash" || return
export GAZEBO_MODEL_PATH="$hydrone_server_ws/src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/models:$hydrone_server_ws/src/uuv_simulator/uuv_gazebo_worlds/models${GAZEBO_MODEL_PATH:+:$GAZEBO_MODEL_PATH}"
export GAZEBO_MODEL_DATABASE_URI=""
export ROS_MASTER_URI=http://127.0.0.1:11311 ROS_IP=127.0.0.1
export ROS_HOME="$hydrone_server_ws/logs/ros-home" ROS_LOG_DIR="$hydrone_server_ws/logs/ros"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 PYTHONUNBUFFERED=1
mkdir -p "$ROS_HOME" "$ROS_LOG_DIR"
