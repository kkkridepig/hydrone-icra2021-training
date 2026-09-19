#!/usr/bin/env bash
# Local CPU/ROS validation only. Source is always mounted read-only.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd -- "$here/../.." && pwd)"
image="${HYDRONE_LOCAL_IMAGE:-hydrone-local-validation:noetic}"
cpu_image="${HYDRONE_LOCAL_CPU_IMAGE:-hydrone-local-validation:noetic-cpu}"
mode="${1:-help}"
if [[ $# -gt 1 ]]; then echo 'Only one validation mode is accepted.' >&2; exit 2; fi
case "$mode" in
  help|-h|--help)
    echo 'Usage: bash dev/local_validation/run.bash {build-image|build-cpu-image|static|unit|unit-cpu|ros|ros-cpu|gazebo|l2}'
    echo 'static: L0 (reports existing legacy failures); unit/unit-cpu: L1; ros/gazebo/l2: L2; no L3 mode.'
    exit 0 ;;
  build-image|build-cpu-image|static|unit|unit-cpu|ros|ros-cpu|gazebo|l2) ;;
  *) echo "Unknown mode: $mode" >&2; exit 2 ;;
esac
command -v docker >/dev/null || { echo 'Docker CLI unavailable. Enable Docker Desktop integration for this WSL2 distribution.' >&2; exit 2; }
docker info --format '{{.OSType}}' | grep -qx linux || { echo 'A running Linux Docker engine is required.' >&2; exit 2; }
if [[ "$mode" == build-image || "$mode" == build-cpu-image ]]; then
  target=base
  [[ "$mode" != build-cpu-image ]] || { target=cpu-torch; image="$cpu_image"; }
  exec docker build --progress=plain --target "$target" \
    --build-arg "UBUNTU_APT_MIRROR=${HYDRONE_UBUNTU_APT_MIRROR:-}" -t "$image" "$here"
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$-$mode"
output="$here/_runs/$stamp"
mkdir -p "$here/_runs"
mkdir "$output"
if [[ "$mode" == unit-cpu || "$mode" == ros-cpu ]]; then image="$cpu_image"; fi
docker image inspect "$image" > "$output/image.json"
git -C "$root" rev-parse HEAD > "$output/source-commit.txt"
git -C "$root" status --porcelain > "$output/source-status.txt"
args=(run --rm --init --network none --cpus "${HYDRONE_LOCAL_CPUS:-2}"
      --memory "${HYDRONE_LOCAL_MEMORY:-6g}" --pids-limit 512 --shm-size 256m
      --cap-drop ALL --security-opt no-new-privileges --user "$(id -u):$(id -g)"
      --mount "type=bind,source=$root,target=/repo,readonly"
      --mount "type=bind,source=$output,target=/results"
      --workdir /repo -e HOME=/tmp/local-home -e PYTHONDONTWRITEBYTECODE=1
      -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo)
case "$mode" in
  static) command=(python3 /repo/dev/local_validation/checks.py --root /repo --output /results/checks --level static) ;;
  unit) command=(python3 /repo/dev/local_validation/checks.py --root /repo --output /results/checks --level unit) ;;
  unit-cpu) command=(python3 /repo/dev/local_validation/checks.py --root /repo --output /results/checks --level unit --cpu-torch) ;;
  ros) command=(bash /repo/dev/local_validation/ros_checks.bash imports) ;;
  ros-cpu) command=(bash /repo/dev/local_validation/ros_checks.bash unit) ;;
  gazebo) command=(python3 /repo/dev/local_validation/ros_smoke.py --output-dir /results/gazebo) ;;
  l2) command=(bash /repo/dev/local_validation/ros_checks.bash build) ;;
esac
echo "LOCAL_VALIDATION_MODE=$mode IMAGE=$image OUTPUT=$output"
set +e
docker "${args[@]}" "$image" "${command[@]}" 2>&1 | tee "$output/console.log"
status=${PIPESTATUS[0]}
set -e
printf '%s\n' "$status" > "$output/exit-code.txt"
echo "LOCAL_VALIDATION_EXIT=$status OUTPUT=$output (L3 server validation was not run)"
exit "$status"
