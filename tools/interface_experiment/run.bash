#!/usr/bin/env bash
set -eo pipefail
interface_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export HYDRONE_ROOT="$interface_root"
export HYDRONE_BACKEND="${HYDRONE_BACKEND:-ppu}"
source "$interface_root/tools/server/setup.bash"
exec python "$interface_root/tools/interface_experiment/run.py" "$@"
