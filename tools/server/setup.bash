# Source from bash after building the workspace.
hydrone_server_ws="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${HYDRONE_BACKEND:-nvidia}" == ppu ]]; then
  source "$hydrone_server_ws/tools/server/setup_ppu.bash"
  return $?
fi
hydrone_conda_root="${HYDRONE_CONDA_ROOT:-}"
if [[ -z "$hydrone_conda_root" ]]; then
  for candidate in "$HOME/anaconda3" "$HOME/miniconda3" /opt/conda; do
    if [[ -f "$candidate/etc/profile.d/conda.sh" ]]; then hydrone_conda_root="$candidate"; break; fi
  done
fi
[[ -f "$hydrone_conda_root/etc/profile.d/conda.sh" ]] || { echo 'Set HYDRONE_CONDA_ROOT to your Conda installation.' >&2; return 1; }
source "$hydrone_conda_root/etc/profile.d/conda.sh"
conda activate hydrone-gpu || return
unset LD_LIBRARY_PATH GAZEBO_PLUGIN_PATH GAZEBO_MODEL_PATH PYTHONPATH PYTHONHOME
source /opt/ros/noetic/setup.bash || return
source "$hydrone_server_ws/devel/setup.bash" || return
export GAZEBO_MODEL_PATH="$hydrone_server_ws/src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/models:$hydrone_server_ws/src/uuv_simulator/uuv_gazebo_worlds/models"
export GAZEBO_MODEL_DATABASE_URI=""
if [[ -f /usr/lib/x86_64-linux-gnu/libffi.so.7 ]]; then
  case ":${LD_PRELOAD:-}:" in
    *:/usr/lib/x86_64-linux-gnu/libffi.so.7:*) ;;
    *) export LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libffi.so.7${LD_PRELOAD:+:$LD_PRELOAD}" ;;
  esac
fi
export ROS_MASTER_URI=http://127.0.0.1:11311 ROS_IP=127.0.0.1
export ROS_HOME="$hydrone_server_ws/logs/ros-home" ROS_LOG_DIR="$hydrone_server_ws/logs/ros"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 PYTHONUNBUFFERED=1
mkdir -p "$ROS_HOME" "$ROS_LOG_DIR"
